from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import Product, CATEGORIES
from ..auth import login_required, current_group
from ..schemas.serializers import product_out

bp = Blueprint("products", __name__)


def _get(product_id):
    p = db.session.get(Product, product_id)
    if not p or p.group_id != current_group().id:
        abort(404)
    return p


def _apply(p, data):
    for k, attr in {"name": "name", "brand": "brand", "barcode": "barcode",
                    "notes": "notes"}.items():
        if k in data and data[k] is not None:
            setattr(p, attr, data[k])
    if data.get("category") in CATEGORIES:
        p.category = data["category"]
    if "defaultUnit" in data and data["defaultUnit"]:
        p.default_unit = data["defaultUnit"]
    if "shelfLifeDays" in data:
        p.shelf_life_days = data["shelfLifeDays"] or None


@bp.get("/products")
@login_required
def list_products():
    q = db.session.query(Product).filter_by(group_id=current_group().id)
    search = request.args.get("q")
    if search:
        like = f"%{search}%"
        q = q.filter(db.or_(Product.name.ilike(like), Product.brand.ilike(like),
                            Product.barcode.ilike(like)))
    if request.args.get("category"):
        q = q.filter(Product.category == request.args["category"])
    return jsonify([product_out(p) for p in q.order_by(Product.name.asc()).all()])


@bp.get("/products/barcode/<code>")
@login_required
def by_barcode(code):
    p = (db.session.query(Product)
         .filter_by(group_id=current_group().id, barcode=code).first())
    if not p:
        return jsonify({"found": False, "barcode": code})
    return jsonify({"found": True, "product": product_out(p)})


@bp.post("/products")
@login_required
def create():
    data = request.get_json(force=True) or {}
    p = Product(name=data.get("name", ""), group_id=current_group().id)
    _apply(p, data)
    db.session.add(p)
    db.session.commit()
    return jsonify(product_out(p)), 201


@bp.put("/products/<product_id>")
@login_required
def update(product_id):
    p = _get(product_id)
    _apply(p, request.get_json(force=True) or {})
    db.session.commit()
    return jsonify(product_out(p))


@bp.delete("/products/<product_id>")
@login_required
def delete(product_id):
    db.session.delete(_get(product_id))
    db.session.commit()
    return "", 204
