from flask import Blueprint, request, jsonify, abort
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Location, LOCATION_KINDS
from ..auth import login_required, current_group
from ..schemas.serializers import location_out

bp = Blueprint("locations", __name__)


def _get(location_id):
    loc = db.session.get(Location, location_id)
    if not loc or loc.group_id != current_group().id:
        abort(404)
    return loc


def _validate_parent(parent_id, moving=None):
    if not parent_id:
        return None, None
    parent = db.session.get(Location, parent_id)
    if not parent or parent.group_id != current_group().id:
        return None, "Unknown parent location."
    if moving is not None:
        node, seen = parent, set()
        while node is not None and node.id not in seen:
            if node.id == moving.id:
                return None, "Can't move a location inside its own sub-location."
            seen.add(node.id)
            node = node.parent
    return parent_id, None


@bp.get("/locations")
@login_required
def list_locations():
    locs = (db.session.query(Location)
            .filter_by(group_id=current_group().id)
            .options(selectinload(Location.stock), selectinload(Location.children))
            .all())
    return jsonify([location_out(loc) for loc in locs])


@bp.post("/locations")
@login_required
def create():
    data = request.get_json(force=True) or {}
    parent_id, err = _validate_parent(data.get("parentId"))
    if err:
        return jsonify({"error": err}), 422
    kind = data.get("kind") or "other"
    if kind not in LOCATION_KINDS:
        kind = "other"
    loc = Location(name=data.get("name", ""), kind=kind, notes=data.get("notes", ""),
                   temp_c=data.get("tempC"), parent_id=parent_id,
                   group_id=current_group().id)
    db.session.add(loc)
    db.session.commit()
    return jsonify(location_out(loc)), 201


@bp.get("/locations/<location_id>")
@login_required
def get(location_id):
    loc = _get(location_id)
    data = location_out(loc)
    data["children"] = [location_out(c) for c in loc.children]
    return jsonify(data)


@bp.put("/locations/<location_id>")
@login_required
def update(location_id):
    loc = _get(location_id)
    data = request.get_json(force=True) or {}
    for k in ("name", "notes"):
        if k in data:
            setattr(loc, k, data[k])
    if "kind" in data and data["kind"] in LOCATION_KINDS:
        loc.kind = data["kind"]
    if "tempC" in data:
        loc.temp_c = data["tempC"]
    if "parentId" in data:
        parent_id, err = _validate_parent(data["parentId"], moving=loc)
        if err:
            return jsonify({"error": err}), 422
        loc.parent_id = parent_id
    db.session.commit()
    return jsonify(location_out(loc))


@bp.delete("/locations/<location_id>")
@login_required
def delete(location_id):
    db.session.delete(_get(location_id))
    db.session.commit()
    return "", 204


@bp.get("/locations/<location_id>/path")
@login_required
def path(location_id):
    loc = _get(location_id)
    chain, node, seen = [], loc, set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        chain.append({"id": node.id, "name": node.name})
        node = node.parent
    return jsonify(list(reversed(chain)))
