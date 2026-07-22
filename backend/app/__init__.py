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
        from .services.bootstrap import seed_all_households
        seed_all_households()

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
        "users": {"ha_user_id": "VARCHAR(255)", "is_owner": "BOOLEAN DEFAULT 0"},
        "products": {"family": "VARCHAR(255) DEFAULT ''",
                     "tracking_mode": "VARCHAR(16) DEFAULT ''",
                     "concept_id": "VARCHAR(36)",
                     "item_type": "VARCHAR(16) DEFAULT 'food'",
                     "min_quantity": "FLOAT", "target_quantity": "FLOAT",
                     "reorder_threshold": "FLOAT",
                     "staple": "BOOLEAN DEFAULT 0",
                     "do_not_suggest": "BOOLEAN DEFAULT 0"},
        "stock_lots": {
            "state": "VARCHAR(32) DEFAULT ''", "created_by": "VARCHAR(36)",
            "package_state": "VARCHAR(24) DEFAULT 'sealed'",
            "quantity_kind": "VARCHAR(16) DEFAULT 'exact'",
            "provenance": "VARCHAR(64) DEFAULT 'manual'",
            "confidence": "FLOAT",
            "acquisition_lot_id": "VARCHAR(36)",
        },
        "consumption_events": {
            "outcome": "VARCHAR(16) DEFAULT 'eaten'",
            "days_kept": "INTEGER",
            "state": "VARCHAR(32) DEFAULT ''",
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

    # Backfill roles for installs that predate ownership: promote the earliest
    # user of any household that has no owner, so an existing admin isn't locked
    # out of the now-owner-gated config surfaces. Idempotent — a household that
    # already has an owner is skipped, so this is a no-op on every later start.
    if "users" in existing_tables:
        db.session.execute(text("""
            UPDATE users SET is_owner = 1
            WHERE id IN (
                SELECT u.id FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM users o
                    WHERE o.group_id = u.group_id AND o.is_owner = 1
                )
                AND u.created_at = (
                    SELECT MIN(u2.created_at) FROM users u2
                    WHERE u2.group_id = u.group_id
                )
            )
        """))
        db.session.commit()

    # Ledger backfill: derive the orthogonal package_state for legacy lots, and
    # give every existing active lot an opening-balance `import` event so it has
    # provenance in the new inventory-event ledger. Idempotent + no-op on fresh DBs.
    if "stock_lots" in existing_tables and "inventory_events" in existing_tables:
        _backfill_inventory_events()
    if "stock_lots" in existing_tables and "acquisition_lots" in existing_tables:
        _backfill_acquisition_lots()
    if "products" in existing_tables and "food_concepts" in existing_tables:
        _backfill_food_concepts()


def _backfill_food_concepts():
    """Turn the free-text `family` grouping into canonical FoodConcepts, one per
    distinct family (else per product name), and link products. Preserves the
    original label as the concept's canonical name. Once-per-product, self-quiescing:
    products that already have a concept are skipped."""
    from .models import Product, FoodConcept
    unlinked = (db.session.query(Product)
                .filter(Product.concept_id.is_(None)).all())
    if not unlinked:
        return
    # Reuse an existing concept for the same label within a household.
    existing = {}
    for c in db.session.query(FoodConcept).all():
        existing[(c.group_id, (c.canonical_name or "").lower())] = c
    made = 0
    for p in unlinked:
        label = (p.family or p.name or "").strip()
        if not label:
            continue
        key = (p.group_id, label.lower())
        concept = existing.get(key)
        if concept is None:
            concept = FoodConcept(canonical_name=label, item_type=p.item_type or "food",
                                  group_id=p.group_id)
            db.session.add(concept)
            db.session.flush()
            existing[key] = concept
            made += 1
        p.concept_id = concept.id
    db.session.commit()
    if made:
        _LOGGER.info("stock redesign: created %d food concepts", made)


def _backfill_acquisition_lots():
    """Give every position that lacks one an AcquisitionLot from its purchase facts.
    Runs once per position (skips lots that already link one), and self-quiesces:
    when no unlinked lots remain it does nothing. New command-path adds link their
    own acquisition lot, so this mainly catches legacy + bulk/import lots."""
    from .models import StockLot, AcquisitionLot
    unlinked = (db.session.query(StockLot)
                .filter(StockLot.acquisition_lot_id.is_(None)).all())
    if not unlinked:
        return
    for lot in unlinked:
        acq = AcquisitionLot(
            product_id=lot.product_id, acquired_at=lot.purchase_date, source=lot.source,
            original_quantity=lot.quantity, unit=lot.unit, cost=lot.cost,
            lot_code=lot.lot_code, provenance=lot.provenance or "migration",
            created_by=lot.created_by, group_id=lot.group_id)
        db.session.add(acq)
        db.session.flush()
        lot.acquisition_lot_id = acq.id
    db.session.commit()
    _LOGGER.info("stock redesign: linked %d positions to acquisition lots", len(unlinked))


def _backfill_inventory_events():
    """Expand+backfill for the stock redesign (Phase 1). Restart-safe/idempotent:
    the presence of an `import` event marks a lot as already migrated, so a re-run
    adds nothing AND won't re-derive package_state (which would clobber a lot the
    user later explicitly re-sealed). Both the derivation and the opening event
    happen exactly once per lot — the first time it's seen."""
    from .models import StockLot, InventoryEvent

    already = {e.dst_position_id for e in
               db.session.query(InventoryEvent.dst_position_id).filter_by(type="import")}
    made = 0
    for lot in db.session.query(StockLot).filter_by(finished=False):
        if lot.id in already:
            continue  # already migrated — never touched again
        # One-time derivation of the orthogonal package facet for a legacy lot that
        # was "opened" via storage_method / opened_date, tied to this single visit.
        if (lot.package_state or "sealed") == "sealed" and (
                lot.storage_method == "opened" or lot.opened_date is not None):
            lot.package_state = "opened"
        db.session.add(InventoryEvent(
            type="import", group_id=lot.group_id, source_app="migration",
            dst_position_id=lot.id, delta_value=str(lot.quantity), delta_unit=lot.unit,
            provenance="migration",
            summary=f"Opening balance: {lot.quantity} {lot.unit}".strip(),
            state_changes={"package_state": [None, lot.package_state]}))
        made += 1
    if made:
        db.session.commit()
        _LOGGER.info("stock redesign: created %d opening-balance events", made)


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
    from .api.data import bp as data_bp
    from .api.misc import bp as misc_bp

    for bp in (users_bp, tokens_bp, locations_bp, products_bp, stock_bp,
               shopping_bp, dashboard_bp, integrations_bp, assistant_bp,
               data_bp, misc_bp):
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
