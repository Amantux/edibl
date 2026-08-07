"""Edibl application factory."""
import logging
import os
import re
import time
import uuid

from flask import Flask, current_app, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import safe_join

from .config import Config, ensure_secret_key
from .extensions import db, limiter
from .logging_setup import configure as configure_logging, request_id_var
from .logsafe import scrub
from .settings import FIELDS_BY_NAME, PLACEHOLDER_SECRETS, load_settings

_LOGGER = logging.getLogger("edibl")

# Request ids appear in every log line for a request; keep them boring.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]+")


def _settings_from(config_object):
    """Build Settings, honouring a legacy config object as a bag of overrides.

    Tests (and any caller passing a Config subclass) fully specify their
    configuration, so every attribute matching a declared field becomes an
    explicit override — which is also what lets several differently-configured
    apps exist in one process. Passing the bare Config class means "resolve
    normally" (env + /data/options.json).
    """
    if config_object is None or config_object is Config:
        return load_settings()

    overrides = {}
    for name in FIELDS_BY_NAME:
        if hasattr(config_object, name):
            overrides[name] = getattr(config_object, name)
    # Legacy derived attributes: the old Config exposed the computed value, the
    # registry declares the input.
    if hasattr(config_object, "MAX_UPLOAD_BYTES") and "MAX_UPLOAD_MB" not in overrides:
        overrides["MAX_UPLOAD_MB"] = max(1, int(config_object.MAX_UPLOAD_BYTES) // (1024 * 1024))
    if hasattr(config_object, "JWT_EXPIRES") and "JWT_HOURS" not in overrides:
        overrides["JWT_HOURS"] = max(1, int(config_object.JWT_EXPIRES.total_seconds() // 3600))
    return load_settings(overrides=overrides, ha_options={})


def create_app(config_object=Config):
    app = Flask(__name__, static_folder=None)
    settings = _settings_from(config_object)

    # Every declared field is exposed under its own name, exactly as
    # `from_object(Config)` used to expose the class attributes — so existing
    # `app.config["LLM_PROVIDER"]`-style reads keep working.
    # Before anything else logs: until this release Edibl configured logging
    # nowhere, so every INFO line was discarded and the only handler present had
    # been installed as a side effect of Alembic's fileConfig.
    configure_logging(settings, process="app")

    app.config.update(settings.values)
    app.config["SETTINGS"] = settings
    # Absolute, matching the old `Config.DATA_DIR = os.path.abspath(...)`.
    app.config["DATA_DIR"] = settings.data_dir
    app.config["KNOWN_DEFAULT_SECRETS"] = PLACEHOLDER_SECRETS
    app.config["JWT_EXPIRES"] = settings.jwt_expires
    app.config["MAX_UPLOAD_BYTES"] = settings.max_upload_bytes
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_bytes
    app.config["JSON_SORT_KEYS"] = False
    app.config["SQLALCHEMY_DATABASE_URI"] = settings.sqlalchemy_uri
    # pool_pre_ping recycles connections a remote Postgres dropped (idle timeout,
    # restart). Harmless for SQLite. Enables the optional Postgres backend.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    for warning in settings.warnings:
        _LOGGER.warning("Configuration: %s", warning)
    _db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    _LOGGER.info("Edibl storage backend: %s",
                 "sqlite" if _db_uri.startswith("sqlite") else _db_uri.split("://", 1)[0])

    # Resolve the signing key BEFORE validating it: an operator-supplied key wins,
    # otherwise we reuse (or generate once and persist) one under DATA_DIR. This
    # replaces the entrypoint's old /dev/urandom default, which minted a new key
    # every boot and silently logged everyone out.
    secret, generated = ensure_secret_key(app.config)
    app.config["SECRET_KEY"] = secret
    if generated:
        _LOGGER.warning(
            "No EDIBL_SECRET_KEY was supplied; generated one and persisted it to "
            "%s so sessions survive restarts. Back this file up with your data.",
            os.path.join(app.config["DATA_DIR"], ".secret_key"),
        )

    if not app.config["DISABLE_AUTH"]:
        if secret in app.config["KNOWN_DEFAULT_SECRETS"] or len(secret) < 32:
            raise RuntimeError(
                "EDIBL_SECRET_KEY is a known default or < 32 chars. Set a "
                "strong random secret before enabling authentication."
            )
    if app.config["DISABLE_AUTH"]:
        # Behind HA ingress (Supervisor) or a trusted reverse proxy this is expected
        # — the front door already authenticates. Standalone with neither, it means
        # the API is wide open on the network; warn loudly so it's not a surprise.
        behind_frontdoor = bool(os.environ.get("SUPERVISOR_TOKEN")) or app.config["PROXY_HOPS"] > 0
        if behind_frontdoor:
            _LOGGER.warning("EDIBL_DISABLE_AUTH is on: relying on HA ingress / trusted "
                            "proxy for authentication.")
        else:
            _LOGGER.warning("EDIBL_DISABLE_AUTH is on with no HA ingress or trusted proxy "
                            "detected — the API is UNAUTHENTICATED on the network. Set "
                            "EDIBL_DISABLE_AUTH=false (and mint API keys) to require auth.")

    hops = app.config["PROXY_HOPS"]
    if hops > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)

    origins = app.config["CORS_ORIGINS"]
    CORS(app, origins=origins or [], supports_credentials=bool(origins))

    db.init_app(app)
    limiter.init_app(app)

    from . import models  # noqa: F401
    _init_schema(app)

    _register_request_context(app)
    _register_blueprints(app)
    _register_spa(app)
    _register_errors(app)
    _register_security_headers(app)

    from .worker import start_worker
    start_worker(app)  # background job poller (no-op when WORKER_ENABLED is False)
    return app


def _enable_sqlite_pragmas():
    """WAL + a busy timeout + FK enforcement on every SQLite connection — better
    concurrency (readers don't block the writer) and integrity for a single-file DB.
    No-op on non-SQLite. Registered before the first connection is used."""
    from sqlalchemy import event
    if db.engine.dialect.name != "sqlite":
        return

    @event.listens_for(db.engine, "connect")
    def _set_pragmas(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _init_schema(app):
    """Bring the schema to head via Alembic and run idempotent data backfills +
    seeding, all under an exclusive file lock so concurrent gunicorn workers don't
    race on a fresh DB. Works on SQLite and Postgres alike."""
    import fcntl

    lock_path = os.path.join(app.config["DATA_DIR"], ".schema-init.lock")
    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass  # locking unsupported (rare FS) — Alembic is still safe to run.
        with app.app_context():
            _enable_sqlite_pragmas()
            _maybe_boot_migrate(app)
            _run_migrations(app)
            _run_data_backfills()
            _seed_reference_data()
            from .services.bootstrap import seed_all_households
            seed_all_households()


def _warn_stranded_sqlite(app, db_filename):
    """Loudly warn when the app is about to serve a Postgres database (external or
    shared-add-on provisioned) while a populated local SQLite still sits in
    DATA_DIR and migrate_from_sqlite is OFF — i.e. the inventory would look empty
    and the existing data is stranded (recoverable, not deleted). Silent in the
    normal cases (SQLite target, or no local DB to strand)."""
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        return
    src_file = os.path.join(app.config["DATA_DIR"], db_filename)
    if not os.path.exists(src_file):
        return
    _LOGGER.warning(
        "A Postgres database is configured but migrate_from_sqlite is off, and a "
        "local %s still holds data. The app is serving Postgres, which may be "
        "empty — your SQLite data is intact but not shown. To copy it over, enable "
        "migrate_from_sqlite and restart once.", src_file)


def _maybe_boot_migrate(app):
    """HA add-on convenience: when EDIBL_MIGRATE_FROM_SQLITE is on and the app is
    configured for an external (Postgres) DB, copy the local SQLite database into
    the EMPTY target before serving, then normal startup stamps it to baseline.

    Skips a non-empty target (already migrated). If the copy FAILS, it raises to
    abort startup rather than let the app adopt-and-serve the empty target while
    real data is stranded in SQLite — the operator sees the failure, the SQLite
    source is untouched, and a fixed retry (or reverting database_url) recovers."""
    if not app.config.get("MIGRATE_FROM_SQLITE"):
        _warn_stranded_sqlite(app, "edibl.db")
        return
    target = app.config["SQLALCHEMY_DATABASE_URI"]
    if target.startswith("sqlite"):
        return  # not configured for an external DB — nothing to migrate to
    src_file = os.path.join(app.config["DATA_DIR"], "edibl.db")
    if not os.path.exists(src_file):
        _LOGGER.info("migrate_from_sqlite: no local %s to migrate — skipping", src_file)
        return
    from .services.db_copy import TargetNotEmpty, copy_database
    try:
        report = copy_database(f"sqlite:///{src_file}", target)
        _LOGGER.warning("migrate_from_sqlite: copied %d rows from SQLite into the "
                        "external database", report["total"])
    except TargetNotEmpty:
        _LOGGER.info("migrate_from_sqlite: target already has data — skipping (done)")
    except Exception as exc:
        # Do NOT serve an empty database with data stranded in SQLite. Fail the
        # boot loudly; the SQLite source is intact — fix the target (or unset
        # migrate_from_sqlite / revert database_url) and restart.
        raise RuntimeError(
            "migrate_from_sqlite failed and the target isn't populated; refusing to "
            f"start on an empty database (your SQLite data is intact): {exc}"
        ) from exc


def _run_migrations(app):
    """Run Alembic migrations to head. Three cases, all handled:
      * Fresh DB → upgrade runs the baseline (create_all) + any deltas.
      * Existing PRE-Alembic install (tables, no alembic_version) → fill gaps
        (missing tables via create_all, missing legacy columns via the additive
        SQLite migration), then stamp baseline so later deltas apply.
      * Already on Alembic → apply pending revisions.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AlembicConfig(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    # Pass the URL via attributes, NOT set_main_option: Alembic's ConfigParser would
    # try to %-interpolate a URL-encoded password (e.g. `%40` for '@') and crash.
    cfg.attributes["url"] = app.config["SQLALCHEMY_DATABASE_URI"]

    with db.engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current is None and inspect(db.engine).has_table("users"):
        db.create_all()             # any missing tables
        _legacy_sqlite_columns()    # any missing legacy columns (SQLite only)
        command.stamp(cfg, "0001_baseline")
    command.upgrade(cfg, "head")


def _legacy_sqlite_columns():
    """Additive SQLite column migration for pre-Alembic databases only.

    Used solely to bring an existing SQLite install fully up to the baseline
    before stamping it (see _run_migrations). Fresh DBs get every column from the
    metadata-driven baseline; Postgres is created fresh, so this no-ops there.
    """
    from sqlalchemy import inspect, text

    if db.engine.dialect.name != "sqlite":
        return
    wanted = {
        "users": {"ha_user_id": "VARCHAR(255)", "is_owner": "BOOLEAN DEFAULT 0"},
        "api_tokens": {"scope": "VARCHAR(16) DEFAULT 'full'"},
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
            "best_by": "DATETIME", "use_by": "DATETIME",
            "expiry_basis": "VARCHAR(16) DEFAULT ''", "expiry_confidence": "FLOAT",
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


def _run_data_backfills():
    """Idempotent, dialect-agnostic DATA migrations (not schema — Alembic owns
    that). Self-quiescing: no-ops once applied and on fresh databases, so they run
    safely on every boot, on SQLite and Postgres alike."""
    from sqlalchemy import inspect, text

    existing_tables = set(inspect(db.engine).get_table_names())

    # Promote the earliest user of any household that has no owner, so an install
    # that predates ownership isn't locked out of the owner-gated surfaces.
    # `true`/`false` (not 0/1) so the boolean comparison is valid on Postgres too.
    if "users" in existing_tables:
        db.session.execute(text("""
            UPDATE users SET is_owner = true
            WHERE id IN (
                SELECT u.id FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM users o
                    WHERE o.group_id = u.group_id AND o.is_owner = true
                )
                AND u.created_at = (
                    SELECT MIN(u2.created_at) FROM users u2
                    WHERE u2.group_id = u.group_id
                )
            )
        """))
        db.session.commit()

    # Ledger backfill: derive package_state for legacy lots + opening-balance
    # `import` events so each has provenance. Idempotent + no-op on fresh DBs.
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
    from .models import FoodConcept, Product
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
    from .models import AcquisitionLot, StockLot
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
    from .models import InventoryEvent, StockLot

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
    from .api.assistant import bp as assistant_bp
    from .api.dashboard import bp as dashboard_bp
    from .api.data import bp as data_bp
    from .api.ha import bp as ha_bp
    from .api.integrations import bp as integrations_bp
    from .api.jobs import bp as jobs_bp
    from .api.locations import bp as locations_bp
    from .api.misc import bp as misc_bp
    from .api.products import bp as products_bp
    from .api.shopping import bp as shopping_bp
    from .api.stock import bp as stock_bp
    from .api.suggestions import bp as suggestions_bp
    from .api.tokens import bp as tokens_bp
    from .api.units import bp as units_bp
    from .api.users import bp as users_bp

    for bp in (users_bp, tokens_bp, locations_bp, products_bp, stock_bp,
               shopping_bp, dashboard_bp, integrations_bp, assistant_bp,
               data_bp, misc_bp, ha_bp, units_bp, jobs_bp, suggestions_bp):
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

    @app.errorhandler(Exception)
    def unhandled(e):
        """Log the traceback deliberately and return a curated body.

        Flask's default already logs unhandled exceptions, but only if logging
        happens to be configured — which it was not until this release. Doing it
        explicitly also lets us hand the caller the request id, so "it broke
        once" becomes a line an operator can actually find in the log.

        The exception text is NEVER returned: a driver error can carry the
        database password.
        """
        from werkzeug.exceptions import HTTPException

        if isinstance(e, HTTPException):
            return e  # 404/413/429 etc. keep their own handlers and status

        rid = request_id_var.get()
        _LOGGER.exception("unhandled error [%s] %s %s", rid,
                          scrub(request.method), scrub(request.path))
        return jsonify({"error": "internal server error", "requestId": rid}), 500


def _register_request_context(app):
    """Give every request a correlation id, and log the ones worth logging.

    The id is echoed as X-Request-Id and included in a 500 body, so a user can
    quote it in a bug report and it can be grepped straight out of the log.

    Only slow requests and server errors get a line: gunicorn's access log
    already records the ordinary ones, and writing a second line per request is
    how a household add-on fills its disk.
    """
    @app.before_request
    def _assign_request_id():
        incoming = request.headers.get("X-Request-Id", "")
        # Bounded and charset-checked: this value lands in every log line for
        # the request, so an attacker-supplied one must not be able to forge
        # log structure or bloat the file.
        if incoming and len(incoming) <= 64 and _SAFE_REQUEST_ID.fullmatch(incoming):
            rid = incoming
        else:
            rid = uuid.uuid4().hex[:12]
        request_id_var.set(rid)
        g._started_at = time.monotonic()

    @app.after_request
    def _finish_request(resp):
        resp.headers.setdefault("X-Request-Id", request_id_var.get())
        started = getattr(g, "_started_at", None)
        if started is None:
            return resp
        elapsed_ms = int((time.monotonic() - started) * 1000)
        threshold = app.config.get("SLOW_REQUEST_MS", 1000)
        if resp.status_code >= 500 or (threshold >= 0 and elapsed_ms >= threshold):
            _LOGGER.warning("%s %s -> %s in %dms",
                            scrub(request.method), scrub(request.path),
                            resp.status_code, elapsed_ms)
        return resp


def _register_security_headers(app):
    from .auth import _request_from_ingress

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        csp = (
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; base-uri 'self'; form-action 'self'; object-src 'none'"
        )
        # Anti-clickjacking only when NOT from the ingress peer: HA frames the
        # panel legitimately, and disable_auth:false behind ingress is supported
        # (ingress identity runs even with auth on), so keying on auth mode
        # blanks the HA panel — while a standalone disable_auth deployment is
        # still clickjackable and must get the headers. Keyed per-request.
        if not _request_from_ingress():
            csp += "; frame-ancestors 'self'"
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Content-Security-Policy", csp)
        if request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000; includeSubDomains")
        return resp


_DEFAULT_FRONTEND_DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))


def _frontend_dist() -> str:
    """Where the built SPA lives — read per request from the app's resolved
    settings (FRONTEND_DIST, blank = the location baked into the image).

    Deliberately NOT a module-level constant: resolving config at import time is
    the pattern this refactor removed, and it makes `python3 -m app.config_check`
    die with a traceback on a bad config instead of reporting it cleanly.
    """
    return current_app.config.get("FRONTEND_DIST") or _DEFAULT_FRONTEND_DIST


def _serve_spa(path):
    _FRONTEND_DIST = _frontend_dist()
    # safe_join, not os.path.join: it rejects traversal ("../") and absolute
    # segments, returning None. os.path.join would happily build a path outside
    # the dist dir, letting the isfile() check probe for arbitrary files.
    # Nested asset paths ("assets/index-abc.js") are still served normally.
    full = safe_join(_FRONTEND_DIST, path) if path else None
    if full and os.path.isfile(full):
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
