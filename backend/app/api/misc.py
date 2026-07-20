import os
import tempfile
import time

from flask import Blueprint, jsonify, current_app
from sqlalchemy import text

from ..extensions import db

bp = Blueprint("misc", __name__)


@bp.get("/status")
def status():
    """Liveness."""
    return jsonify({"health": True, "title": "Edibl", "versions": ["v1"]})


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
    from ..models import CATEGORIES, UNITS, STORAGE_METHODS, LOCATION_KINDS
    return jsonify({
        "categories": list(CATEGORIES), "units": list(UNITS),
        "storageMethods": list(STORAGE_METHODS), "locationKinds": list(LOCATION_KINDS),
    })
