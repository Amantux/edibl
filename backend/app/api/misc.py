import os
import tempfile
import time

from flask import Blueprint, jsonify, current_app
from sqlalchemy import text

from ..extensions import db
from ..auth import login_required

bp = Blueprint("misc", __name__)


@bp.get("/status")
def status():
    """Liveness."""
    return jsonify({"health": True, "title": "Edibl", "versions": ["v1"]})


@bp.get("/diagnostics")
@login_required
def diagnostics():
    """Coarse, non-sensitive runtime facts for a user-initiated bug report.

    Login-gated so it doesn't reveal AI-configured state to anonymous callers, and
    deliberately returns only publishable facts — never secrets (API keys, provider
    base URLs, the DB URL) or any inventory content — because the report they seed
    becomes a PUBLIC GitHub issue."""
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    try:
        from ..services import assistant
        provider = assistant._cfg().get("provider") or "none"
    except Exception:  # noqa: BLE001 - diagnostics must never 500
        provider = current_app.config.get("LLM_PROVIDER") or "none"
    return jsonify({
        "app": "Edibl",
        "dbBackend": "sqlite" if uri.startswith("sqlite") else "postgresql",
        "aiProvider": provider,
        "mcpEnabled": os.environ.get("EDIBL_MCP_ENABLED", "").lower() == "true",
        "authDisabled": bool(current_app.config.get("DISABLE_AUTH")),
    })


@bp.get("/ready")
def ready():
    """Readiness: DB reachable + data dir writable. 503 if a dependency is down."""
    checks, ok = {}, True
    t = time.monotonic()
    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
        ok = False
    checks["dbLatencyMs"] = round((time.monotonic() - t) * 1000, 1)
    try:
        d = current_app.config["DATA_DIR"]
        os.makedirs(d, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".readycheck-", dir=d)
        try:
            os.write(fd, b"ok")
        finally:
            os.close(fd)
            os.remove(probe)
        checks["storage"] = "ok"
    except Exception:  # noqa: BLE001
        checks["storage"] = "error"
        ok = False
    return jsonify({"ready": ok, "checks": checks}), (200 if ok else 503)


@bp.get("/meta")
def meta():
    """Enum reference data for the UI (categories, units, storage methods…)."""
    from ..models import (CATEGORIES, UNITS, STORAGE_METHODS, LOCATION_KINDS,
                          FRESHNESS_LEVELS, FRESHNESS_SCALE, FRESHNESS_SCALES, OUTCOMES)
    fresh = [s for s in FRESHNESS_LEVELS if s]
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    return jsonify({
        # Storage backend in use — drives the "Migrate to PostgreSQL" action.
        "dbBackend": "sqlite" if uri.startswith("sqlite") else "postgresql",
        # Categories / units / freshness are user-driven; these are seed
        # suggestions. See /products/suggestions for the values actually in use.
        "categories": list(CATEGORIES), "units": list(UNITS),
        "storageMethods": list(STORAGE_METHODS), "locationKinds": list(LOCATION_KINDS),
        "freshnessLevels": fresh, "lifecycleStates": fresh,  # alias
        "freshnessScale": [dict(s) for s in FRESHNESS_SCALE],  # default (back-compat)
        # Per-domain 1–5 condition scales (produce=ripeness, bakery=staleness, …).
        "freshnessScales": {k: [dict(s) for s in v] for k, v in FRESHNESS_SCALES.items()},
        "outcomes": list(OUTCOMES),
    })
