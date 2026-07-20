import logging

from flask import Blueprint, request, jsonify, current_app

from ..extensions import db, limiter
from ..models import User, Group
from ..auth import (login_required, current_user, hash_password, verify_password,
                    create_token)

bp = Blueprint("users", __name__)
_LOGGER = logging.getLogger("edibl.auth")
_DUMMY_HASH = hash_password("this-is-not-a-real-password")


def _password_ok(pw):
    n = current_app.config["MIN_PASSWORD_LENGTH"]
    if len(pw or "") < n:
        return False, (jsonify({"error": f"password must be at least {n} characters"}), 422)
    if len(pw) > 4096:
        return False, (jsonify({"error": "password too long"}), 422)
    return True, None


def _user_out(u):
    return {"id": u.id, "name": u.name, "email": u.email, "groupId": u.group_id}


@bp.post("/users/register")
@limiter.limit("5 per minute")
def register():
    if not current_app.config["ALLOW_REGISTRATION"]:
        return jsonify({"error": "registration disabled"}), 403
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email required"}), 422
    ok, err = _password_ok(data.get("password") or "")
    if not ok:
        return err
    if db.session.query(User).filter_by(email=email).first():
        return jsonify({"error": "email already registered"}), 409
    group = Group(name=data.get("groupName") or "Household")
    db.session.add(group)
    db.session.flush()
    user = User(name=data.get("name") or "", email=email,
                password_hash=hash_password(data["password"]), group_id=group.id)
    db.session.add(user)
    db.session.commit()
    return jsonify(_user_out(user)), 201


@bp.post("/users/login")
@limiter.limit("10 per minute")
def login():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    user = db.session.query(User).filter_by(email=email).first()
    valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not valid:
        _LOGGER.warning("login failed for %r from %s", email, request.remote_addr)
        return jsonify({"error": "invalid credentials"}), 401
    _LOGGER.info("login ok for %r", email)
    return jsonify({"token": f"Bearer {create_token(user)}"})


@bp.get("/users/self")
@login_required
def get_self():
    return jsonify({"item": _user_out(current_user())})
