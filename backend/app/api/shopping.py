from flask import Blueprint, request, jsonify, abort, Response

from ..extensions import db
from ..models import ShoppingItem, Product, StockLot, ConsumptionEvent
from ..auth import login_required, current_group
from ..schemas.serializers import shopping_out
from ..services.shopping import format_for_delivery

bp = Blueprint("shopping", __name__)


def _get(item_id):
    i = db.session.get(ShoppingItem, item_id)
    if not i or i.group_id != current_group().id:
        abort(404)
    return i


@bp.get("/shopping")
@login_required
def list_items():
    status = request.args.get("status", "needed")
    q = db.session.query(ShoppingItem).filter_by(group_id=current_group().id)
    if status != "all":
        q = q.filter(ShoppingItem.status == status)
    items = q.order_by(ShoppingItem.created_at.asc()).all()
    return jsonify([shopping_out(i) for i in items])


@bp.post("/shopping")
@login_required
def add():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 422
    i = ShoppingItem(name=name, quantity=float(data.get("quantity") or 1),
                     unit=data.get("unit") or "count", note=data.get("note", ""),
                     source=data.get("source", "manual"),
                     product_id=data.get("productId"), group_id=current_group().id)
    db.session.add(i)
    db.session.commit()
    return jsonify(shopping_out(i)), 201


@bp.put("/shopping/<item_id>")
@login_required
def update(item_id):
    i = _get(item_id)
    data = request.get_json(force=True) or {}
    for k, attr in {"name": "name", "note": "note", "status": "status",
                    "unit": "unit"}.items():
        if k in data:
            setattr(i, attr, data[k])
    if "quantity" in data:
        i.quantity = float(data["quantity"])
    db.session.commit()
    return jsonify(shopping_out(i))


@bp.delete("/shopping/<item_id>")
@login_required
def delete(item_id):
    db.session.delete(_get(item_id))
    db.session.commit()
    return "", 204


@bp.get("/shopping/export")
@login_required
def export():
    """Paste-friendly list for Uber Eats / Instacart / a grocery app.
    ?format=text (default) → plain text; else JSON with a `text` field."""
    items = (db.session.query(ShoppingItem)
             .filter_by(group_id=current_group().id, status="needed")
             .order_by(ShoppingItem.created_at.asc()).all())
    text = format_for_delivery(items)
    if request.args.get("format") == "json":
        return jsonify({"text": text, "count": len(items)})
    return Response(text, mimetype="text/plain")


@bp.post("/shopping/suggest")
@login_required
def suggest():
    """Auto-add items you've run out of: products with consumption history but no
    remaining active stock. Skips anything already on the list."""
    gid = current_group().id
    on_list = {i.product_id for i in db.session.query(ShoppingItem)
               .filter_by(group_id=gid, status="needed").all() if i.product_id}
    consumed_pids = {e.product_id for e in db.session.query(ConsumptionEvent)
                     .filter_by(group_id=gid).all() if e.product_id}
    added = []
    for pid in consumed_pids:
        if pid in on_list:
            continue
        remaining = sum(s.quantity for s in db.session.query(StockLot)
                        .filter_by(group_id=gid, product_id=pid, finished=False).all())
        if remaining > 0:
            continue
        p = db.session.get(Product, pid)
        if not p:
            continue
        i = ShoppingItem(name=p.name, quantity=1, unit=p.default_unit,
                         source="low_stock", product_id=pid, group_id=gid)
        db.session.add(i)
        added.append(i)
    db.session.commit()
    return jsonify({"added": len(added), "items": [shopping_out(i) for i in added]})
