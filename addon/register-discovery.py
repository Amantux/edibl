#!/usr/bin/env python3
"""Register Edibl with the Home Assistant Supervisor so the Edibl integration is
auto-discovered when this add-on runs. Best-effort; safe to fail (the integration
can still be added manually). Uses only the standard library.

The Supervisor forwards this to HA core, which starts the Edibl config flow's
`async_step_hassio` with {host, port, token}. HA core reaches the add-on on its
internal hostname:7746 (no host port mapping needed), authenticating the direct
REST path with the advertised long-lived API key.
"""
import json
import os
import socket
import sys
import time
import urllib.request

TOKEN = os.environ.get("SUPERVISOR_TOKEN")
BASE = "http://supervisor"
PORT = int(os.environ.get("EDIBL_PORT", "7746"))
DATA_DIR = os.environ.get("EDIBL_DATA_DIR", "/data")


def _integration_token():
    """Read the long-lived integration API key the app minted at startup.

    The app persists it to <data>/.integration_token (0600) before workers start;
    handing it to HA in the discovery payload authenticates the integration on the
    direct REST path. Best-effort: an empty string just means the integration
    falls back to the open path (unchanged behaviour)."""
    try:
        with open(os.path.join(DATA_DIR, ".integration_token"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or "{}")


def main():
    if not TOKEN:
        return  # not running under the Supervisor
    for attempt in range(5):
        try:
            info = _api("GET", "/addons/self/info")
            host = (info.get("data") or {}).get("hostname")
            if not host:
                # Fall back to the container hostname, which HA core can also
                # reach on the internal add-on network (matches homehoard).
                host = os.environ.get("HOSTNAME") or socket.gethostname()
            if not host:
                raise RuntimeError("no hostname from Supervisor")
            token = _integration_token()
            _api("POST", "/discovery", {"service": "edibl",
                                        "config": {"host": host, "port": PORT,
                                                   "token": token}})
            print(f"Edibl: registered discovery -> {host}:{PORT} "
                  f"(token {'set' if token else 'absent'})", flush=True)
            return
        except Exception as exc:  # noqa: BLE001 — best effort
            print(f"Edibl: discovery attempt {attempt + 1} failed: {exc}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        sys.exit(0)
