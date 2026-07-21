"""Outbound clients for sibling apps (myMeal, HomeHoard).

Bounded (short timeout, no retries) and gracefully degrading: when a sibling is
not configured or unreachable, callers get a clear {configured/reachable:false}
rather than an exception. Edibl is the source of truth for inventory; these are
for pulling recipes/plans (myMeal) or cross-querying non-food items (HomeHoard).
"""
import logging
import os

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


def _write(method, base_url, token, path, payload=None):
    """POST/PUT/DELETE to a sibling. Same {configured, reachable, data} envelope
    as `_get`; DELETE (204, no body) yields data=None."""
    if not base_url:
        return {"configured": False, "reachable": False}
    try:
        with _client(base_url, token) as c:
            r = c.request(method, path, json=payload)
            r.raise_for_status()
            return {"configured": True, "reachable": True,
                    "data": r.json() if r.content else None}
    except Exception as e:  # noqa: BLE001 - bounded, best-effort
        _LOGGER.warning("integration %s %s%s failed: %s", method, base_url, path, e)
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


def mymeal_post(path, payload=None):
    url, token = mymeal_cfg()
    return _write("POST", url, token, path, payload)


def mymeal_put(path, payload=None):
    url, token = mymeal_cfg()
    return _write("PUT", url, token, path, payload)


def mymeal_delete(path):
    url, token = mymeal_cfg()
    return _write("DELETE", url, token, path)


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


def _supervisor_get(path):
    """GET the Home Assistant Supervisor API (add-on has hassio_api). Returns the
    parsed JSON, or None when not running under the Supervisor / on error."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    import httpx
    try:
        with httpx.Client(base_url="http://supervisor",
                          headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT) as c:
            r = c.get(path)
            r.raise_for_status()
            return r.json()
    except Exception as e:  # noqa: BLE001 — best-effort
        _LOGGER.info("supervisor GET %s failed: %s", path, e)
        return None


def _internal_port(info):
    """myMeal's internal port: prefer ingress_port, else the first mapped container
    port, else a sane default."""
    if info.get("ingress_port"):
        return info["ingress_port"]
    for container_port in (info.get("network") or {}):
        try:
            return int(str(container_port).split("/")[0])
        except (ValueError, TypeError):
            continue
    return 8000


MYMEAL_DEFAULT_PORT = 7850
MYMEAL_HEALTH_PATH = "/api/v1/status"  # public status endpoint on myMeal
_PROBE_TIMEOUT = 2.0  # short: we may try several hosts, most refusing fast


def _mymeal_reachable(host, port):
    """True if a myMeal-like service answers here. A status < 500 counts (a
    401/403 still means 'something is there' — it just wants auth)."""
    import httpx
    try:
        r = httpx.get(f"http://{host}:{port}{MYMEAL_HEALTH_PATH}", timeout=_PROBE_TIMEOUT)
        return r.status_code < 500
    except Exception:  # noqa: BLE001 — absence is the normal case, not an error
        return False


def discover_mymeal():
    """Find a companion myMeal on the internal add-on network. Gathers candidate
    hosts from the Supervisor add-on list (when permitted) PLUS fixed internal
    hostnames, then returns only those whose status endpoint actually answers —
    so it works even when the Supervisor denies cross-add-on info queries (the
    default add-on role can't read a *sibling* add-on's /info), and the returned
    candidates are guaranteed reachable, not just listed."""
    seen, candidates = set(), []

    def consider(host, port, slug="", name="", running=None):
        if not host or f"{host}:{port}" in seen:
            return
        seen.add(f"{host}:{port}")
        if _mymeal_reachable(host, port):
            candidates.append({"slug": slug, "name": name or slug or "myMeal",
                               "hostname": host, "port": port,
                               "url": f"http://{host}:{port}", "running": running})

    for a in ((_supervisor_get("/addons") or {}).get("data") or {}).get("addons", []):
        name, slug = (a.get("name") or ""), (a.get("slug") or "")
        if "meal" not in f"{name} {slug}".lower():
            continue
        info = (_supervisor_get(f"/addons/{slug}/info") or {}).get("data") or {}
        running = a.get("state") == "started"
        port = _internal_port(info) if info else MYMEAL_DEFAULT_PORT
        # Supervisor-reported hostname (needs manager role); else guess from slug.
        consider(info.get("hostname") or a.get("hostname"), port, slug, name, running)
        if slug:
            consider(f"local-{slug}", port, slug, name, running)
            consider(slug.replace("_", "-"), port, slug, name, running)

    # Fixed fallbacks — reachable on the add-on network with no Supervisor access.
    for host in ("local-mymeal", "mymeal", "homeassistant.local"):
        consider(host, MYMEAL_DEFAULT_PORT)

    return {"available": True, "candidates": candidates}


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
