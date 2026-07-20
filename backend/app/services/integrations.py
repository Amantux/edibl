"""Outbound clients for sibling apps (myMeal, HomeHoard).

Bounded (short timeout, no retries) and gracefully degrading: when a sibling is
not configured or unreachable, callers get a clear {configured/reachable:false}
rather than an exception. Edibl is the source of truth for inventory; these are
for pulling recipes/plans (myMeal) or cross-querying non-food items (HomeHoard).
"""
import logging

from flask import current_app

_LOGGER = logging.getLogger("edibl.integrations")
_TIMEOUT = 8.0


def _client(base_url, token):
    import httpx
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=_TIMEOUT)


def _get(base_url, token, path, params=None):
    if not base_url:
        return {"configured": False, "reachable": False}
    try:
        with _client(base_url, token) as c:
            r = c.get(path, params=params or {})
            r.raise_for_status()
            return {"configured": True, "reachable": True, "data": r.json()}
    except Exception as e:  # noqa: BLE001 - bounded, best-effort
        _LOGGER.warning("integration GET %s%s failed: %s", base_url, path, e)
        return {"configured": True, "reachable": False, "error": str(e)}


def mymeal_get(path, params=None):
    cfg = current_app.config
    return _get(cfg["MYMEAL_URL"], cfg["MYMEAL_TOKEN"], path, params)


def homehoard_get(path, params=None):
    cfg = current_app.config
    return _get(cfg["HOMEHOARD_URL"], cfg["HOMEHOARD_TOKEN"], path, params)


def integration_status():
    cfg = current_app.config
    return {
        "myMeal": {"configured": bool(cfg["MYMEAL_URL"]), "url": cfg["MYMEAL_URL"]},
        "homeHoard": {"configured": bool(cfg["HOMEHOARD_URL"]), "url": cfg["HOMEHOARD_URL"]},
    }
