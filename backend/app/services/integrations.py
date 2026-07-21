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


def _mymeal_overrides():
    try:
        from ..auth import current_group
        from .settings import get_mymeal_overrides
        return get_mymeal_overrides(current_group().id)
    except Exception:  # noqa: BLE001 — no request/group context
        return {}


def mymeal_cfg():
    """Effective myMeal connection: UI-set (persisted) > add-on/env."""
    cfg = current_app.config
    ov = _mymeal_overrides()
    return (ov.get("mymeal_url") or cfg["MYMEAL_URL"],
            ov.get("mymeal_token") or cfg["MYMEAL_TOKEN"])


def mymeal_get(path, params=None):
    url, token = mymeal_cfg()
    return _get(url, token, path, params)


def homehoard_get(path, params=None):
    cfg = current_app.config
    return _get(cfg["HOMEHOARD_URL"], cfg["HOMEHOARD_TOKEN"], path, params)


def mymeal_public():
    """UI view of the myMeal connection (never returns the token)."""
    url, token = mymeal_cfg()
    ov = _mymeal_overrides()
    env_url = current_app.config["MYMEAL_URL"]
    return {"url": url, "hasToken": bool(token),
            "source": "ui" if ov.get("mymeal_url") else ("addon" if env_url else "none")}


def mymeal_test():
    """Ping myMeal's planned-ingredients endpoint; returns {configured, reachable}."""
    res = mymeal_get("/api/v1/plan/ingredients")
    return {"configured": res.get("configured", False),
            "reachable": res.get("reachable", False),
            "error": res.get("error"),
            "items": len((res.get("data") or {}).get("items", []))
            if res.get("reachable") else None}


def integration_status():
    cfg = current_app.config
    url, _t = mymeal_cfg()
    return {
        "myMeal": {"configured": bool(url), "url": url},
        "homeHoard": {"configured": bool(cfg["HOMEHOARD_URL"]), "url": cfg["HOMEHOARD_URL"]},
    }
