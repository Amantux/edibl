from datetime import datetime

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import (StockLot, Product, Location, ConsumptionEvent, utcnow,
                      STORAGE_METHODS)
from ..auth import login_required, current_group
from ..schemas.serializers import stock_out, expiry_status
from ..services.estimation import estimate_expiry

bp = Blueprint("stock", __name__)


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return None


def _get(lot_id):
    s = db.session.get(StockLot, lot_id)
    if not s or s.group_id != current_group().id:
        abort(404)
    return s


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
    p = db.session.query(Product).filter_by(group_id=gid, name=name).first()
    if not p:
        p = Product(name=name, category=data.get("category") or "other",
                    barcode=data.get("barcode") or "",
                    default_unit=data.get("unit") or "count", group_id=gid)
        db.session.add(p)
        db.session.flush()
    return p


def _build_lot(data, product, gid):
    storage = data.get("storageMethod") or "refrigerated"
    if storage not in STORAGE_METHODS:
        storage = "refrigerated"
    purchase = _parse_dt(data.get("purchaseDate")) or utcnow()
    expiry = _parse_dt(data.get("expiryDate"))
    estimated = False
    if not expiry:
        expiry, estimated = estimate_expiry(purchase, product.category, storage,
                                            product.shelf_life_days)
    return StockLot(
        product_id=product.id, location_id=data.get("locationId") or None,
        quantity=float(data.get("quantity") or 1), unit=data.get("unit") or product.default_unit,
        storage_method=storage, purchase_date=purchase, expiry_date=expiry,
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


@bp.post("/stock")
@login_required
def create():
    data = request.get_json(force=True) or {}
    product = _resolve_product(data)
    if not product:
        return jsonify({"error": "productId or productName required"}), 422
    lot = _build_lot(data, product, current_group().id)
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
        s.location_id = data["locationId"] or None
    if "storageMethod" in data and data["storageMethod"] in STORAGE_METHODS:
        s.storage_method = data["storageMethod"]
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
    """Use some/all of a lot. Records a ConsumptionEvent (feeds runout prediction)
    and marks the lot finished when it hits zero."""
    s = _get(lot_id)
    data = request.get_json(force=True) or {}
    amount = data.get("quantity")
    amount = float(amount) if amount is not None else s.quantity  # default: all
    amount = min(amount, s.quantity)
    s.quantity = round(s.quantity - amount, 4)
    reason = data.get("reason", "used")
    if s.quantity <= 0:
        s.finished = True
        s.quantity = 0
    db.session.add(ConsumptionEvent(product_id=s.product_id, quantity=amount,
                                    unit=s.unit, reason=reason,
                                    group_id=current_group().id))
    db.session.commit()
    return jsonify(stock_out(s))


@bp.delete("/stock/<lot_id>")
@login_required
def delete(lot_id):
    db.session.delete(_get(lot_id))
    db.session.commit()
    return "", 204


@bp.post("/stock/butcher")
@login_required
def butcher_session():
    """Butchering workflow: one animal/source → many vacuum-sealed frozen lots.

    Body: { source, animal, locationId, freezeDate?, cuts: [{cut, name?, weightG,
    quantity?, category?}] }. Each cut becomes a StockLot (vacuum_sealed + frozen)
    with a long estimated expiry and shared session metadata in attrs.
    """
    data = request.get_json(force=True) or {}
    cuts = data.get("cuts") or []
    if not cuts:
        return jsonify({"error": "cuts[] required"}), 422
    gid = current_group().id
    session_id = data.get("sessionId") or utcnow().strftime("butcher-%Y%m%d-%H%M%S")
    freeze = _parse_dt(data.get("freezeDate")) or utcnow()
    location_id = data.get("locationId") or None
    if location_id:
        loc = db.session.get(Location, location_id)
        if not loc or loc.group_id != gid:
            return jsonify({"error": "unknown location"}), 422
    created = []
    for c in cuts:
        cut_name = (c.get("name") or c.get("cut") or "Cut").strip()
        product = _resolve_product({"productName": cut_name,
                                    "category": c.get("category") or "meat"})
        weight_g = c.get("weightG")
        lot = _build_lot({
            "quantity": c.get("quantity", 1), "unit": c.get("unit", "pack"),
            "storageMethod": "vacuum_sealed", "purchaseDate": data.get("purchaseDate"),
            "locationId": location_id, "source": data.get("source", "butcher"),
            "attrs": {"cut": c.get("cut", cut_name), "animal": data.get("animal", ""),
                      "weightG": weight_g, "freezeDate": freeze.isoformat(),
                      "butcherSession": session_id},
        }, product, gid)
        db.session.add(lot)
        created.append(lot)
    db.session.commit()
    return jsonify({"session": session_id, "created": len(created),
                    "items": [stock_out(s) for s in created]}), 201
