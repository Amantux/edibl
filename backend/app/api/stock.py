from datetime import datetime

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import (StockLot, Product, Location, ConsumptionEvent, InventoryEvent,
                      utcnow, STORAGE_METHODS, PACKAGE_STATES, QUANTITY_KINDS)
from ..auth import login_required, current_group, current_user
from ..schemas.serializers import stock_out, expiry_status
from ..services.estimation import estimate_expiry, product_insights
from ..services import inventory

bp = Blueprint("stock", __name__)


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return None


def _lot_quantity(s):
    """The lot's amount as a dimension/kind-aware Quantity."""
    from ..services.quantity import Quantity
    return Quantity(value=s.quantity, unit=s.unit,
                    kind=getattr(s, "quantity_kind", "exact") or "exact")


def _group_summary(g):
    """Dimension-safe, package-aware human summary, e.g.
    "2 cartons: 1 open, 1 sealed" or "unknown amount". Never an invalid total."""
    from ..services.quantity import aggregate
    parts = [q.describe() for q in aggregate(g["_qtys"])]
    amount = ", ".join(parts) if parts else "none"
    pkg = []
    if g["openCount"]:
        pkg.append(f"{g['openCount']} open")
    if g["sealedCount"]:
        pkg.append(f"{g['sealedCount']} sealed")
    return f"{amount}: {', '.join(pkg)}" if pkg else amount


def _get(lot_id):
    s = db.session.get(StockLot, lot_id)
    if not s or s.group_id != current_group().id:
        abort(404)
    return s


def _valid_location(gid, location_id):
    """A lot may only be placed in a location owned by its own group. Empty is OK
    (unassigned). Guards against attaching stock to another tenant's location."""
    if not location_id:
        return True
    loc = db.session.get(Location, location_id)
    return bool(loc and loc.group_id == gid)


def _resolve_product(data):
    """Use productId, else find/create by name (+ category)."""
    gid = current_group().id
    pid = data.get("productId")
    if pid:
        p = db.session.get(Product, pid)
        if p and p.group_id == gid:
            return p
    name = (data.get("productName") or data.get("name") or "").strip()
    if not name:
        return None
    family = (data.get("family") or data.get("group") or "").strip()
    p = db.session.query(Product).filter_by(group_id=gid, name=name).first()
    if not p:
        p = Product(name=name, category=(data.get("category") or "other").strip(),
                    family=family, barcode=data.get("barcode") or "",
                    default_unit=data.get("unit") or "count", group_id=gid)
        db.session.add(p)
        db.session.flush()
    elif family and not p.family:
        # Let a new lot assign the grouping if the product doesn't have one yet.
        p.family = family
    return p


def _package_state(data):
    """Package state is orthogonal to storage. Honour an explicit `packageState`;
    otherwise derive it — an opened date, or the legacy storage_method "opened",
    means the package is open. Defaults to sealed."""
    ps = (data.get("packageState") or "").strip().lower()
    if ps in PACKAGE_STATES:
        return ps
    if data.get("openedDate") or (data.get("storageMethod") == "opened"):
        return "opened"
    return "sealed"


def _quantity_kind(data):
    """How sure we are of the amount. Explicit `quantityKind` wins; a quantity of
    None/"" with no kind is treated as `presence` (we have it, amount unmeasured) —
    NEVER silently coerced to 1. A given number is `exact` unless told otherwise."""
    qk = (data.get("quantityKind") or "").strip().lower()
    if qk in QUANTITY_KINDS:
        return qk
    if data.get("quantity") in (None, ""):
        return "presence" if "quantity" in data else "exact"
    return "exact"


def _build_lot(data, product, gid):
    storage = data.get("storageMethod") or "refrigerated"
    if storage not in STORAGE_METHODS:
        storage = "refrigerated"
    state = (data.get("freshness") or data.get("state") or "").strip()
    purchase = _parse_dt(data.get("purchaseDate")) or utcnow()
    expiry = _parse_dt(data.get("expiryDate"))
    estimated = False
    if not expiry:
        # Personalized: learns from this household's own spoilage history for the
        # product, falling back to the category/storage table.
        expiry, estimated = estimate_expiry(purchase, product.category, storage,
                                            product.shelf_life_days,
                                            group_id=gid, product_id=product.id)
    kind = _quantity_kind(data)
    # A presence/unknown lot carries NO meaningful number: store 0 as an internal
    # placeholder (never the misleading default 1); quantity_kind is the source of
    # truth and the serializer surfaces null, not a fake amount.
    qty = float(data.get("quantity") or 1) if kind in ("exact", "estimated", "approximate") else 0.0
    return StockLot(
        product_id=product.id, location_id=data.get("locationId") or None,
        quantity=qty, unit=data.get("unit") or product.default_unit,
        storage_method=storage, package_state=_package_state(data),
        quantity_kind=kind,
        state=state, purchase_date=purchase, opened_date=_parse_dt(data.get("openedDate")),
        expiry_date=expiry, expiry_estimated=estimated, cost=data.get("cost"),
        source=data.get("source", ""), lot_code=data.get("lotCode", ""),
        notes=data.get("notes", ""), attrs=data.get("attrs") or {}, group_id=gid,
        created_by=current_user().id,
    )


@bp.get("/stock")
@login_required
def list_stock():
    gid = current_group().id
    q = db.session.query(StockLot).filter_by(group_id=gid)
    args = request.args
    if args.get("includeFinished", "false").lower() != "true":
        q = q.filter(StockLot.finished.is_(False))
    if args.get("locationId"):
        q = q.filter(StockLot.location_id == args["locationId"])
    if args.get("storageMethod"):
        q = q.filter(StockLot.storage_method == args["storageMethod"])
    # NULL expiry sorts last (portable across SQLite versions).
    lots = q.order_by(StockLot.expiry_date.is_(None).asc(),
                      StockLot.expiry_date.asc()).all()
    if args.get("category"):
        lots = [s for s in lots if s.product and s.product.category == args["category"]]
    if args.get("status"):  # fresh/expiring/expired/unknown
        lots = [s for s in lots if expiry_status(s.expiry_date) == args["status"]]
    return jsonify({"items": [stock_out(s) for s in lots], "total": len(lots)})


@bp.get("/stock/grouped")
@login_required
def grouped():
    """Stock rolled up by group (a product's `family`, else its name) so multiple
    buy-dates / related products (organic vs filtered milk) read as one group while
    each lot keeps its own expiry. Each group lists its underlying lots."""
    gid = current_group().id
    lots = (db.session.query(StockLot)
            .filter_by(group_id=gid, finished=False)
            .order_by(StockLot.expiry_date.is_(None).asc(),
                      StockLot.expiry_date.asc()).all())
    groups = {}
    for s in lots:
        if not s.product:
            continue
        key = s.product.family or s.product.name
        g = groups.get(key)
        if not g:
            g = {"group": key, "category": s.product.category,
                 "totalQuantity": 0.0, "unit": s.unit, "lotCount": 0,
                 "products": set(), "expiring": 0, "expired": 0,
                 "nextExpiry": None, "nextExpiryStatus": "unknown", "lots": [],
                 "openCount": 0, "sealedCount": 0, "_qtys": []}
            groups[key] = g
        # Only real amounts contribute to the numeric total; presence/unknown lots
        # count as lots + show in `summary`, but never inflate the number.
        if (s.quantity_kind or "exact") in ("exact", "estimated", "approximate"):
            g["totalQuantity"] = round(g["totalQuantity"] + (s.quantity or 0), 3)
        g["lotCount"] += 1
        g["products"].add(s.product.name)
        if (s.package_state or "sealed") == "opened":
            g["openCount"] += 1
        else:
            g["sealedCount"] += 1
        g["_qtys"].append(_lot_quantity(s))
        st = expiry_status(s.expiry_date)
        if st == "expiring":
            g["expiring"] += 1
        elif st == "expired":
            g["expired"] += 1
        if s.expiry_date and (g["nextExpiry"] is None):
            g["nextExpiry"] = s.expiry_date.isoformat()
            g["nextExpiryStatus"] = st
        g["lots"].append(stock_out(s))
    from ..services.quantity import aggregate
    out = []
    for g in groups.values():
        g["products"] = sorted(g["products"])
        g["productCount"] = len(g["products"])
        g["summary"] = _group_summary(g)
        g["amounts"] = [q.as_dict() for q in aggregate(g["_qtys"])]
        del g["_qtys"]
        out.append(g)
    out.sort(key=lambda g: (g["nextExpiry"] is None, g["nextExpiry"] or "", g["group"]))
    return jsonify({"groups": out, "total": len(out)})


@bp.post("/stock")
@login_required
def create():
    data = request.get_json(force=True) or {}
    gid = current_group().id
    product = _resolve_product(data)
    if not product:
        return jsonify({"error": "productId or productName required"}), 422
    if not _valid_location(gid, data.get("locationId")):
        return jsonify({"error": "unknown location"}), 422
    lot = _build_lot(data, product, gid)
    res = inventory.add_lot(
        lot, actor_user_id=current_user().id, source_app=data.get("sourceApp", "web"),
        provenance=data.get("provenance") or "manual", confidence=data.get("confidence"),
        idempotency_key=data.get("idempotencyKey"))
    return jsonify({**stock_out(res.lot), "eventId": res.event.id if res.event else None}), 201


@bp.get("/stock/<lot_id>")
@login_required
def get(lot_id):
    return jsonify(stock_out(_get(lot_id)))


@bp.put("/stock/<lot_id>")
@login_required
def update(lot_id):
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    if "quantity" in data:
        s.quantity = float(data["quantity"])
    if "unit" in data:
        s.unit = data["unit"]
    if "locationId" in data:
        if not _valid_location(s.group_id, data["locationId"]):
            return jsonify({"error": "unknown location"}), 422
        s.location_id = data["locationId"] or None
    if "storageMethod" in data and data["storageMethod"] in STORAGE_METHODS:
        s.storage_method = data["storageMethod"]
    if "freshness" in data or "state" in data:
        s.state = (data.get("freshness") or data.get("state") or "").strip()
    for k, attr in {"expiryDate": "expiry_date", "openedDate": "opened_date",
                    "purchaseDate": "purchase_date"}.items():
        if k in data:
            setattr(s, attr, _parse_dt(data[k]))
            if k == "expiryDate":
                s.expiry_estimated = False
    for k, attr in {"cost": "cost", "source": "source", "lotCode": "lot_code",
                    "notes": "notes", "finished": "finished"}.items():
        if k in data:
            setattr(s, attr, data[k])
    if "attrs" in data and isinstance(data["attrs"], dict):
        s.attrs = {**(s.attrs or {}), **data["attrs"]}
    db.session.commit()
    return jsonify(stock_out(s))


@bp.post("/stock/<lot_id>/open")
@login_required
def open_lot(lot_id):
    """Mark a package opened — an orthogonal facet, so a frozen lot can also be
    open. Idempotent (opening an open package is a no-op). Records an `open` event."""
    s = _get(lot_id)
    data = request.get_json(silent=True) or {}
    res = inventory.open_lot(s, actor_user_id=current_user().id,
                             source_app=data.get("sourceApp", "web"),
                             idempotency_key=data.get("idempotencyKey"))
    return jsonify({**stock_out(res.lot), "eventId": res.event.id if res.event else None,
                    "summary": res.summary})


@bp.post("/stock/<lot_id>/consume")
@login_required
def consume(lot_id):
    """Resolve some/all of a lot with an *outcome* — eaten (default), spoiled,
    expired, discarded. Amount omitted ⇒ the whole lot. Records a ConsumptionEvent
    (shelf-life learning) AND an InventoryEvent (the ledger, for reversal). Shared
    with the assistant via the inventory command layer so behaviour can't diverge."""
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    res = inventory.consume_lot(
        s, amount=data.get("quantity"), outcome=data.get("outcome"),
        freshness=(data.get("freshness") or data.get("state")),
        reason=data.get("reason"), actor_user_id=current_user().id,
        source_app=data.get("sourceApp", "web"),
        idempotency_key=data.get("idempotencyKey"))
    # consumptionId + consumedAmount preserve the existing cross-app undo contract;
    # eventId is the new ledger handle for POST /inventory/events/<id>/reverse.
    return jsonify({**stock_out(res.lot), "insight": res.extra.get("insight"),
                    "consumptionId": res.extra.get("consumptionId"),
                    "consumedAmount": res.extra.get("consumedAmount"),
                    "eventId": res.event.id if res.event else None})


@bp.post("/stock/<lot_id>/unconsume")
@login_required
def unconsume(lot_id):
    """Back-compat reversal of a consumption on this lot. Now delegates to the
    ledger: finds the InventoryEvent for the given `consumptionId` and reverses it
    (append-only, idempotent). Body: {consumptionId?, amount}. The `amount`-only
    fallback remains for callers that can't reference an event."""
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    ce_id = data.get("consumptionId")
    if ce_id:
        # Locate the consume event that recorded this ConsumptionEvent.
        ev = (db.session.query(InventoryEvent)
              .filter_by(group_id=current_group().id, type="consume")
              .filter(InventoryEvent.state_changes["consumption_event_id"].as_string() == ce_id)
              .first())
        if ev is None:
            # Already reversed, or pre-ledger data — no-op / legacy fallback below.
            ce = db.session.get(ConsumptionEvent, ce_id)
            if ce and ce.group_id == current_group().id and ce.product_id == s.product_id:
                s.quantity = round((s.quantity or 0) + float(ce.quantity or 0), 4)
                if s.quantity > 0:
                    s.finished = False
                db.session.delete(ce)
                db.session.commit()
            return jsonify(stock_out(s))
        if ev.src_position_id != s.id:
            abort(409)
        res = inventory.reverse_event(ev, actor_user_id=current_user().id,
                                      source_app=data.get("sourceApp", "web"))
        return jsonify(stock_out(res.lot or s))

    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount > 0:
        s.quantity = round((s.quantity or 0) + amount, 4)
        if s.quantity > 0:
            s.finished = False
        db.session.commit()
    return jsonify(stock_out(s))


@bp.post("/stock/<lot_id>/adjust")
@login_required
def adjust(lot_id):
    """Correct a lot to a measured amount (e.g. estimated 2 kg → measured 1.6 kg).
    Body: {quantity, quantityKind?, reason?}. Reversible via the ledger."""
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    if data.get("quantity") is None:
        return jsonify({"error": "quantity required"}), 422
    res = inventory.adjust_lot(
        s, new_quantity=data["quantity"],
        quantity_kind=(data.get("quantityKind") or "exact"), reason=data.get("reason", ""),
        actor_user_id=current_user().id, source_app=data.get("sourceApp", "web"),
        idempotency_key=data.get("idempotencyKey"))
    return jsonify({**stock_out(res.lot), "eventId": res.event.id if res.event else None})


@bp.post("/stock/<lot_id>/move")
@login_required
def move(lot_id):
    """Move a whole lot to another location. Body: {locationId}. Reversible."""
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    if not _valid_location(s.group_id, data.get("locationId")):
        return jsonify({"error": "unknown location"}), 422
    res = inventory.move_lot(s, location_id=data.get("locationId") or None,
                             actor_user_id=current_user().id,
                             source_app=data.get("sourceApp", "web"),
                             idempotency_key=data.get("idempotencyKey"))
    return jsonify({**stock_out(res.lot), "eventId": res.event.id if res.event else None})


@bp.post("/stock/<lot_id>/split")
@login_required
def split(lot_id):
    """Split some off a lot into a new position (conserves total). Body:
    {quantity, locationId?, packageState?}. Reversible (re-merges)."""
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    if not _valid_location(s.group_id, data.get("locationId")):
        return jsonify({"error": "unknown location"}), 422
    try:
        res = inventory.split_lot(
            s, amount=data.get("quantity"), location_id=data.get("locationId") or None,
            package_state=data.get("packageState"), actor_user_id=current_user().id,
            source_app=data.get("sourceApp", "web"),
            idempotency_key=data.get("idempotencyKey"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    return jsonify({"source": stock_out(res.lot), "new": stock_out(res.extra["newLot"]),
                    "eventId": res.event.id if res.event else None}), 201


@bp.post("/stock/merge")
@login_required
def merge():
    """Merge one lot into another (same product + unit; conserves total). Body:
    {srcId, dstId}. Reversible."""
    data = request.get_json(force=True) or {}
    src = _get(data.get("srcId") or "")
    dst = _get(data.get("dstId") or "")
    try:
        res = inventory.merge_lots(src, dst, actor_user_id=current_user().id,
                                   source_app=data.get("sourceApp", "web"),
                                   idempotency_key=data.get("idempotencyKey"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    return jsonify({**stock_out(res.lot), "eventId": res.event.id if res.event else None})


@bp.post("/stock/consume")
@login_required
def consume_by_product():
    """Consume an amount of a PRODUCT, drawing across its lots by an explicit
    selection policy (default: prefer-open, then FEFO) and spilling to the next lot
    when one runs out. Body: {productId | name, quantity, outcome?, policy?}.
    Returns each lot's resulting event so every draw is independently reversible."""
    from ..services.inventory import selection
    gid = current_group().id
    data = request.get_json(force=True) or {}
    pid = data.get("productId")
    product = None
    if pid:
        product = db.session.get(Product, pid)
        if product and product.group_id != gid:
            product = None
    if not product and data.get("name"):
        product = (db.session.query(Product)
                   .filter_by(group_id=gid, name=data["name"].strip()).first())
    if not product:
        return jsonify({"error": "productId or a known product name is required"}), 404
    if data.get("quantity") is None:
        return jsonify({"error": "quantity required"}), 422

    policy = data.get("policy") or selection.PREFER_OPEN_FEFO
    lots = [s for s in product.stock if not s.finished]
    picks, shortfall = selection.plan_consumption(lots, data["quantity"], policy)
    results = []
    for p in picks:
        res = inventory.consume_lot(
            p.lot, amount=p.take, outcome=data.get("outcome"),
            actor_user_id=current_user().id, source_app=data.get("sourceApp", "web"))
        results.append({"lot": stock_out(res.lot), "amount": p.take,
                        "eventId": res.event.id if res.event else None})
    return jsonify({"consumed": round(sum(p.take for p in picks), 4),
                    "shortfall": shortfall, "policy": policy, "draws": results})


@bp.post("/locations/<location_id>/reconcile")
@login_required
def reconcile(location_id):
    """Commit a location walk as ONE reversible operation. Body:
    {counts:[{lotId,quantity}], missing:[lotId], additions:[{name,quantity,unit,…}]}.
    Corrects counted lots, marks missing ones gone, and adds newly-found items —
    all tagged with one batch id so the whole thing undoes together."""
    gid = current_group().id
    if not _valid_location(gid, location_id):
        return jsonify({"error": "unknown location"}), 404
    data = request.get_json(force=True) or {}

    counts = []
    for c in (data.get("counts") or []):
        lot = _get(c.get("lotId") or "")
        if c.get("quantity") is not None:
            counts.append((lot, c["quantity"]))
    missing = [_get(lid) for lid in (data.get("missing") or [])]

    new_lots = []
    for add in (data.get("additions") or []):
        payload = {**add, "locationId": location_id}
        product = _resolve_product(payload)
        if not product:
            continue
        new_lots.append(_build_lot(payload, product, gid))

    res = inventory.reconcile_location(
        gid, location_id=location_id, counts=counts, missing=missing,
        new_lots=new_lots, actor_user_id=current_user().id,
        source_app=data.get("sourceApp", "web"))
    return jsonify({"batchId": res.batch_id, "summary": res.summary,
                    "counted": res.counted, "removed": res.removed,
                    "added": res.added, "eventIds": res.event_ids})


@bp.post("/inventory/reconciliations/<batch_id>/reverse")
@login_required
def reverse_reconciliation(batch_id):
    """Undo an entire reconciliation batch in one operation."""
    res = inventory.reverse_reconciliation(
        current_group().id, batch_id, actor_user_id=current_user().id,
        source_app="web")
    return jsonify({"batchId": res.batch_id, "summary": res.summary,
                    "reversed": res.event_ids})


@bp.get("/inventory/events")
@login_required
def list_events():
    """The inventory ledger for this household, newest first. Optional filters:
    `positionId` (events touching a lot), `type`, `limit` (default 100, max 500)."""
    from ..schemas.serializers import event_out
    gid = current_group().id
    q = db.session.query(InventoryEvent).filter_by(group_id=gid)
    if request.args.get("type"):
        q = q.filter(InventoryEvent.type == request.args["type"])
    pid = request.args.get("positionId")
    if pid:
        q = q.filter((InventoryEvent.src_position_id == pid)
                     | (InventoryEvent.dst_position_id == pid))
    limit = min(int(request.args.get("limit", 100) or 100), 500)
    events = q.order_by(InventoryEvent.at.desc()).limit(limit).all()
    return jsonify({"events": [event_out(e) for e in events], "total": len(events)})


@bp.post("/inventory/events/<event_id>/reverse")
@login_required
def reverse_inventory_event(event_id):
    """Reverse any forward inventory event by appending a compensating event
    (history is never rewritten). Idempotent — reversing twice is a no-op."""
    ev = db.session.get(InventoryEvent, event_id)
    if not ev or ev.group_id != current_group().id:
        abort(404)
    data = request.get_json(silent=True) or {}
    try:
        res = inventory.reverse_event(ev, actor_user_id=current_user().id,
                                      source_app=data.get("sourceApp", "web"),
                                      idempotency_key=data.get("idempotencyKey"))
    except inventory.UnsupportedReversal as e:
        return jsonify({"error": str(e)}), 422
    return jsonify({"summary": res.summary,
                    "eventId": res.event.id if res.event else None,
                    "lot": stock_out(res.lot) if res.lot else None})


@bp.get("/stock/<lot_id>/insights")
@login_required
def lot_insights(lot_id):
    """Lifecycle insight for the product behind a lot."""
    s = _get(lot_id)
    return jsonify(product_insights(current_group().id, s.product_id))


@bp.delete("/stock/<lot_id>")
@login_required
def delete(lot_id):
    db.session.delete(_get(lot_id))
    db.session.commit()
    return "", 204


def _bulk_create(shared, items, gid):
    """Create many lots at once. `shared` supplies defaults (storageMethod,
    category, locationId, source, purchaseDate, attrs) that each item can override.
    Used by the generic bulk-add flow and the (optional) butchering preset."""
    location_id = shared.get("locationId") or None
    if not _valid_location(gid, location_id):
        return None, (jsonify({"error": "unknown location"}), 422)
    created = []
    for it in items:
        name = (it.get("name") or it.get("productName") or "").strip()
        if not name:
            continue
        eff_loc = it.get("locationId") or location_id
        if not _valid_location(gid, eff_loc):
            db.session.rollback()
            return None, (jsonify({"error": "unknown location"}), 422)
        category = it.get("category") or shared.get("category") or "other"
        product = _resolve_product({"productName": name, "category": category,
                                    "barcode": it.get("barcode")})
        merged = {
            "productName": name,
            "quantity": it.get("quantity", 1),
            "unit": it.get("unit") or shared.get("unit"),
            "category": category,
            "storageMethod": it.get("storageMethod") or shared.get("storageMethod")
            or "refrigerated",
            "state": it.get("state") or shared.get("state") or "",
            "locationId": eff_loc,
            "purchaseDate": it.get("purchaseDate") or shared.get("purchaseDate"),
            "expiryDate": it.get("expiryDate"),
            "source": it.get("source") or shared.get("source") or "",
            "cost": it.get("cost"),
            "attrs": {**(shared.get("attrs") or {}), **(it.get("attrs") or {})},
        }
        lot = _build_lot(merged, product, gid)
        db.session.add(lot)
        created.append(lot)
    db.session.commit()
    return created, None


@bp.post("/stock/extract")
@login_required
def extract():
    """Turn a pasted receipt / order — as text OR a photo — into a reviewable item
    list via the configured LLM. Body: { text } or { image (base64), mediaType }.
    Returns { items, provider }; nothing is added (client reviews then bulk-adds)."""
    from ..services import assistant
    data = request.get_json(force=True) or {}
    image = data.get("image") or None
    if image and "," in image[:64]:  # tolerate a data: URL prefix
        image = image.split(",", 1)[1]
    result = assistant.extract_items(text=data.get("text", ""), image=image,
                                     media_type=data.get("mediaType", "image/jpeg"))
    return jsonify(result)


@bp.post("/stock/bulk")
@login_required
def bulk_add():
    """Add many items in one action — the flexible batch-intake path.

    Body: { shared?: {storageMethod, category, locationId, source, purchaseDate,
    attrs}, items: [{name, quantity?, unit?, category?, storageMethod?, state?,
    barcode?, expiryDate?, attrs?}] }. Item fields override the shared defaults.
    Expiry is auto-estimated per item when omitted. Freezing a butchered animal,
    unpacking a grocery haul, and logging a farm box are all the same operation.
    """
    data = request.get_json(force=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"error": "items[] required"}), 422
    if len(items) > 500:
        return jsonify({"error": "too many items (max 500)"}), 422
    created, err = _bulk_create(data.get("shared") or {}, items, current_group().id)
    if err:
        return err
    return jsonify({"created": len(created),
                    "items": [stock_out(s) for s in created]}), 201


@bp.post("/stock/butcher")
@login_required
def butcher_session():
    """Butchering preset over the generic bulk path: one animal/source → many
    vacuum-sealed frozen lots, each with a long estimated expiry and a shared
    session tag. Kept for back-compat; the UI now uses /stock/bulk directly.

    Body: { source, animal, locationId, freezeDate?, cuts: [{cut, name?, weightG,
    quantity?, category?}] }.
    """
    data = request.get_json(force=True) or {}
    cuts = data.get("cuts") or []
    if not cuts:
        return jsonify({"error": "cuts[] required"}), 422
    gid = current_group().id
    session_id = data.get("sessionId") or utcnow().strftime("butcher-%Y%m%d-%H%M%S")
    freeze = (_parse_dt(data.get("freezeDate")) or utcnow()).isoformat()
    shared = {
        "storageMethod": "vacuum_sealed", "category": "meat",
        "locationId": data.get("locationId") or None,
        "source": data.get("source", "butcher"),
        "purchaseDate": data.get("purchaseDate"),
        "attrs": {"animal": data.get("animal", ""), "freezeDate": freeze,
                  "butcherSession": session_id},
    }
    items = [{
        "name": (c.get("name") or c.get("cut") or "Cut").strip(),
        "quantity": c.get("quantity", 1), "unit": c.get("unit", "pack"),
        "category": c.get("category") or "meat",
        "attrs": {"cut": c.get("cut", (c.get("name") or "Cut")), "weightG": c.get("weightG")},
    } for c in cuts]
    created, err = _bulk_create(shared, items, gid)
    if err:
        return err
    return jsonify({"session": session_id, "created": len(created),
                    "items": [stock_out(s) for s in created]}), 201
