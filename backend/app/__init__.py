"""Edibl application factory."""
import logging
import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .extensions import db, limiter

_LOGGER = logging.getLogger("edibl")


def create_app(config_object=Config):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_object)
    app.config["SQLALCHEMY_DATABASE_URI"] = config_object.sqlalchemy_uri()

    if not app.config["DISABLE_AUTH"]:
        secret = app.config["SECRET_KEY"] or ""
        if secret in app.config["KNOWN_DEFAULT_SECRETS"] or len(secret) < 32:
            raise RuntimeError(
                "EDIBL_SECRET_KEY is unset, a known default, or < 32 chars. Set a "
                "strong random secret before enabling authentication."
            )
    if app.config["DISABLE_AUTH"]:
        _LOGGER.warning("EDIBL_DISABLE_AUTH is on: no per-request authentication.")

    hops = app.config["PROXY_HOPS"]
    if hops > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)

    origins = app.config["CORS_ORIGINS"]
    CORS(app, origins=origins or [], supports_credentials=bool(origins))

    db.init_app(app)
    limiter.init_app(app)

    from . import models  # noqa: F401
    with app.app_context():
        db.create_all()
        _ensure_columns()
        _seed_reference_data()

    _register_blueprints(app)
    _register_spa(app)
    _register_errors(app)
    _register_security_headers(app)
    return app


def _ensure_columns():
    """Additive SQLite migration for columns added after a DB was first created.

    There's no Alembic here (create_all is the schema source of truth), so when we
    add a column to an existing model, older databases lack it. Add any missing
    columns idempotently. New databases already have them from create_all — this
    is a no-op there.
    """
    from sqlalchemy import inspect, text

    wanted = {
        "stock_lots": {"state": "VARCHAR(16) DEFAULT ''"},
        "consumption_events": {
            "outcome": "VARCHAR(16) DEFAULT 'eaten'",
            "days_kept": "INTEGER",
            "state": "VARCHAR(16) DEFAULT ''",
        },
    }
    insp = inspect(db.engine)
    existing_tables = set(insp.get_table_names())
    for table, cols in wanted.items():
        if table not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in have:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
    db.session.commit()


def _seed_reference_data():
    """Idempotently seed the shelf-life table used for expiry estimation."""
    from .models import ShelfLifeProfile
    from .services.estimation import DEFAULT_SHELF_LIFE_ROWS

    if db.session.query(ShelfLifeProfile).count() == 0:
        for row in DEFAULT_SHELF_LIFE_ROWS:
            db.session.add(ShelfLifeProfile(**row))
        db.session.commit()


def _register_blueprints(app):
    from .api.users import bp as users_bp
    from .api.tokens import bp as tokens_bp
    from .api.locations import bp as locations_bp
    from .api.products import bp as products_bp
    from .api.stock import bp as stock_bp
    from .api.shopping import bp as shopping_bp
    from .api.dashboard import bp as dashboard_bp
    from .api.integrations import bp as integrations_bp
    from .api.assistant import bp as assistant_bp
    from .api.misc import bp as misc_bp

    for bp in (users_bp, tokens_bp, locations_bp, products_bp, stock_bp,
               shopping_bp, dashboard_bp, integrations_bp, assistant_bp, misc_bp):
        app.register_blueprint(bp, url_prefix="/api/v1")


def _register_errors(app):
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return _serve_spa("index.html")

    @app.errorhandler(429)
    def rate_limited(e):
        return jsonify({"error": "too many requests, slow down"}), 429


def _register_security_headers(app):
    disable_auth = app.config["DISABLE_AUTH"]

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        csp = (
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; base-uri 'self'; form-action 'self'; object-src 'none'"
        )
        if not disable_auth:
            csp += "; frame-ancestors 'self'"
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Content-Security-Policy", csp)
        if request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000; includeSubDomains")
        return resp


_FRONTEND_DIST = os.environ.get(
    "EDIBL_FRONTEND_DIST",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")),
)


def _serve_spa(path):
    full = os.path.join(_FRONTEND_DIST, path)
    if path and os.path.isfile(full):
        return send_from_directory(_FRONTEND_DIST, path)
    index = os.path.join(_FRONTEND_DIST, "index.html")
    if os.path.isfile(index):
        return send_from_directory(_FRONTEND_DIST, "index.html")
    return ("<h1>Edibl API</h1><p>Frontend not built. API under <code>/api/v1</code>.</p>", 200)


def _register_spa(app):
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def spa(path):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        return _serve_spa(path)
