"""Long-lived API tokens for machine clients (myMeal, the MCP server, HA)."""
from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import ApiToken, generate_raw_token, hash_token
from ..auth import owner_required, current_user, current_group
from ..schemas.serializers import iso

bp = Blueprint("tokens", __name__)


def _out(t):
    return {"id": t.id, "name": t.name, "hint": t.hint,
            "createdAt": iso(t.created_at), "lastUsedAt": iso(t.last_used_at)}


@bp.get("/tokens")
@owner_required
def list_tokens():
    rows = (db.session.query(ApiToken).filter_by(group_id=current_group().id)
            .order_by(ApiToken.created_at.desc()).all())
    return jsonify([_out(t) for t in rows])


@bp.post("/tokens")
@owner_required
def create():
    data = request.get_json(force=True) or {}
    raw = generate_raw_token()
    t = ApiToken(name=(data.get("name") or "API token").strip(),
                 token_hash=hash_token(raw), hint=raw[:9],
                 user_id=current_user().id, group_id=current_group().id)
    db.session.add(t)
    db.session.commit()
    return jsonify({**_out(t), "token": raw}), 201


@bp.delete("/tokens/<token_id>")
@owner_required
def revoke(token_id):
    t = db.session.get(ApiToken, token_id)
    if not t or t.group_id != current_group().id:
        abort(404)
    db.session.delete(t)
    db.session.commit()
    return "", 204
