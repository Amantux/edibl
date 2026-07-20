from datetime import datetime

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import (StockLot, Product, Location, ConsumptionEvent, utcnow,
                      STORAGE_METHODS, OUTCOMES, LOSS_OUTCOMES)
from ..auth import login_required, current_group
from ..schemas.serializers import stock_out, expiry_status
from ..services.estimation import estimate_expiry, product_insights

bp = Blueprint("stock", __name__)


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return None


def _days_kept(purchase):
    """Whole days from purchase to now, tolerating tz-naive stored dates."""
    if not purchase:
        return None
    p = purchase.replace(tzinfo=None) if purchase.tzinfo else purchase
    return max((datetime.utcnow() - p).days, 0)


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
    return StockLot(
        product_id=product.id, location_id=data.get("locationId") or None,
        quantity=float(data.get("quantity") or 1), unit=data.get("unit") or product.default_unit,
        storage_method=storage, state=state, purchase_date=purchase, expiry_date=expiry,
        expiry_estimated=estimated, cost=data.get("cost"), source=data.get("source", ""),
        lot_code=data.get("lotCode", ""), notes=data.get("notes", ""),
        attrs=data.get("attrs") or {}, group_id=gid,
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
                 "nextExpiry": None, "nextExpiryStatus": "unknown", "lots": []}
            groups[key] = g
        g["totalQuantity"] = round(g["totalQuantity"] + (s.quantity or 0), 3)
        g["lotCount"] += 1
        g["products"].add(s.product.name)
        st = expiry_status(s.expiry_date)
        if st == "expiring":
            g["expiring"] += 1
        elif st == "expired":
            g["expired"] += 1
        if s.expiry_date and (g["nextExpiry"] is None):
            g["nextExpiry"] = s.expiry_date.isoformat()
            g["nextExpiryStatus"] = st
        g["lots"].append(stock_out(s))
    out = []
    for g in groups.values():
        g["products"] = sorted(g["products"])
        g["productCount"] = len(g["products"])
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
    db.session.add(lot)
    db.session.commit()
    return jsonify(stock_out(lot)), 201


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


@bp.post("/stock/<lot_id>/consume")
@login_required
def consume(lot_id):
    """Resolve some/all of a lot with an *outcome* — eaten (default), spoiled,
    expired, discarded. Records a ConsumptionEvent with how long it was kept and
    its ripeness state; this feeds runout prediction AND personalized shelf-life
    learning (losses shorten future estimates for the product)."""
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    amount = data.get("quantity")
    amount = float(amount) if amount is not None else s.quantity  # default: all
    amount = min(amount, s.quantity)
    s.quantity = round(s.quantity - amount, 4)

    outcome = (data.get("outcome") or "").strip().lower()
    if outcome not in OUTCOMES:
        # Back-compat: fall back to the legacy `reason` field.
        legacy = (data.get("reason") or "used").strip().lower()
        outcome = {"used": "eaten", "expired": "expired",
                   "discarded": "discarded"}.get(legacy, "eaten")
    state = (data.get("freshness") or data.get("state") or s.state or "").strip()
    days_kept = _days_kept(s.purchase_date)

    if s.quantity <= 0:
        s.finished = True
        s.quantity = 0
    db.session.add(ConsumptionEvent(
        product_id=s.product_id, quantity=amount, unit=s.unit,
        reason="used" if outcome == "eaten" else outcome,
        outcome=outcome, days_kept=days_kept, state=state,
        group_id=current_group().id))
    db.session.commit()
    insight = None
    if outcome in LOSS_OUTCOMES:
        insight = product_insights(current_group().id, s.product_id).get("suggestion") or None
    return jsonify({**stock_out(s), "insight": insight})


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
