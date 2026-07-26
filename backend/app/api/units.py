"""Units of measure — a group-scoped, user-manageable vocabulary (mirrors myMeal's
/units) so the two apps can share a consistent set of units. Names are canonicalized
(services.units) so 'Cups' and 'cup' don't both appear."""
from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import Unit, UNITS
from ..auth import login_required, current_group
from ..services.units import canonical_unit, dimension

bp = Blueprint("units", __name__)


def unit_out(u):
    return {"id": u.id, "name": u.name, "pluralName": u.plural_name,
            "abbreviation": u.abbreviation, "dimension": u.dimension}


def _upsert_unit(gid, name, *, plural="", abbreviation=""):
    """Create the unit (canonical name) if the group doesn't have it; return it.
    Fills a missing plural/abbreviation on an existing record. Never duplicates."""
    name = canonical_unit(name)
    if not name:
        return None
    u = db.session.query(Unit).filter_by(group_id=gid, name=name).first()
    if u:
        if plural and not u.plural_name:
            u.plural_name = plural
        if abbreviation and not u.abbreviation:
            u.abbreviation = abbreviation
        return u
    u = Unit(name=name, plural_name=plural or "", abbreviation=abbreviation or "",
             dimension=dimension(name) or "", group_id=gid)
    db.session.add(u)
    db.session.flush()
    return u


@bp.get("/units")
@login_required
def list_units():
    units = (db.session.query(Unit).filter_by(group_id=current_group().id)
             .order_by(Unit.name.asc()).all())
    return jsonify([unit_out(u) for u in units])


@bp.post("/units")
@login_required
def create_unit():
    data = request.get_json(force=True) or {}
    if not (data.get("name") or "").strip():
        return jsonify({"error": "name is required"}), 422
    u = _upsert_unit(current_group().id, data["name"],
                     plural=data.get("pluralName") or "",
                     abbreviation=data.get("abbreviation") or "")
    db.session.commit()
    return jsonify(unit_out(u)), 201


@bp.post("/units/seed-defaults")
@login_required
def seed_defaults():
    """Populate the group's unit vocabulary with Edibl's canonical set (idempotent).
    Gives the sharing/sync a sensible baseline to work from."""
    gid = current_group().id
    before = db.session.query(Unit).filter_by(group_id=gid).count()
    for name in UNITS:
        _upsert_unit(gid, name)
    db.session.commit()
    after = db.session.query(Unit).filter_by(group_id=gid).count()
    return jsonify({"added": after - before, "total": after})


@bp.delete("/units/<unit_id>")
@login_required
def delete_unit(unit_id):
    u = db.session.get(Unit, unit_id)
    if not u or u.group_id != current_group().id:
        abort(404)
    db.session.delete(u)
    db.session.commit()
    return jsonify({"ok": True})
