"""Auth: JWT bearer tokens + long-lived API keys, with a DISABLE_AUTH single-
tenant mode. Mirrors HomeHoard's hardened auth."""
import functools
import logging
from datetime import datetime, timezone

import jwt
from flask import current_app, g, request, jsonify
from passlib.hash import bcrypt

from .extensions import db
from .models import User, Group, ApiToken, hash_token, TOKEN_PREFIX

_LOGGER = logging.getLogger("edibl.auth")
DEFAULT_EMAIL = "local@edibl"
DEFAULT_GROUP = "Household"


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(password, hashed)
    except (ValueError, TypeError):
        return False


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "iat": now, "exp": now + current_app.config["JWT_EXPIRES"]}
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def decode_token(token: str):
    try:
        payload = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def _default_user() -> User:
    user = db.session.query(User).filter_by(email=DEFAULT_EMAIL).first()
    if user:
        return user
    group = Group(name=DEFAULT_GROUP)
    db.session.add(group)
    db.session.flush()
    user = User(name="Local", email=DEFAULT_EMAIL,
                password_hash=hash_password("unused"), group_id=group.id)
    db.session.add(user)
    db.session.commit()
    return user


def _user_from_api_token(raw: str):
    rec = db.session.query(ApiToken).filter_by(token_hash=hash_token(raw)).first()
    if rec is None:
        return None
    now = datetime.utcnow()
    if rec.last_used_at is None or (now - rec.last_used_at).total_seconds() > 60:
        rec.last_used_at = now
        db.session.commit()
    return db.session.get(User, rec.user_id)


def load_current_user():
    if current_app.config["DISABLE_AUTH"]:
        return _default_user()
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    if token.startswith(TOKEN_PREFIX):
        return _user_from_api_token(token)
    user_id = decode_token(token)
    return db.session.get(User, user_id) if user_id else None


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = load_current_user()
        if user is None:
            _LOGGER.warning("unauthorized %s %s from %s",
                            request.method, request.path, request.remote_addr)
            return jsonify({"error": "unauthorized"}), 401
        g.current_user = user
        g.current_group = user.group
        return fn(*args, **kwargs)
    return wrapper


def current_user() -> User:
    return g.current_user


def current_group() -> Group:
    return g.current_group
