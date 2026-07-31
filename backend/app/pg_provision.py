"""Find + register with the Shared PostgreSQL add-on.

When ``EDIBL_USE_SHARED_POSTGRES`` is enabled and we're running under Home
Assistant, discover the ``shared_postgres`` add-on, provision Edibl's OWN database,
and persist the resulting DSN to ``<DATA_DIR>/.database_url``. ``Config.sqlalchemy_uri``
reads that file (gated on the same flag), so the app comes up on Postgres.

Best-effort: any failure logs to stderr and leaves Edibl on SQLite — this must
never block startup. Run once from the entrypoint, before the schema-init step:
``python3 -m app.pg_provision``.

Token bootstrap: prefer the operator-set ``postgres_provision_token``; otherwise
read it from the Supervisor discovery message the add-on publishes (works only if
the platform exposes it to sibling add-ons). If neither yields a token, we log a
clear instruction and stay on SQLite.

Standard library only (mirrors the add-on's other Supervisor scripts) — no httpx
dependency at boot.
"""
import json
import os
import sys
import urllib.request

from app.config import Config

DSN_FILENAME = ".database_url"
API_PORT = 8087
APP_NAME = "edibl"
SUPERVISOR_TIMEOUT = 5
PROVISION_TIMEOUT = 10
# Only accept the driver Edibl ships. A malformed/foreign DSN written here would
# brick every subsequent boot at create_app, so reject it and stay on SQLite
# rather than persist something we can't load.
DSN_PREFIX = "postgresql+psycopg://"


def _log(message: str) -> None:
    # Never logs the token or the DSN (which embed credentials).
    print(f"Edibl: pg_provision: {message}", file=sys.stderr)


def _supervisor_get(path: str):
    """GET a Supervisor endpoint with the add-on token. Returns parsed JSON
    ``data`` or None. Requires ``hassio_api: true`` in the add-on config."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        req = urllib.request.Request(
            f"http://supervisor{path}", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=SUPERVISOR_TIMEOUT) as r:
            return json.loads(r.read() or b"{}").get("data")
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        _log(f"supervisor GET {path} failed: {exc}")
        return None


def _supervisor_addons() -> list:
    data = _supervisor_get("/addons")
    return (data or {}).get("addons") or []


def _discovery_config():
    """The shared_postgres discovery message config ({host, port, provision_url,
    token}), if the Supervisor exposes the discovery list to us. Best-effort."""
    data = _supervisor_get("/discovery")
    messages = data.get("discovery", []) if isinstance(data, dict) else (data or [])
    for msg in messages:
        if isinstance(msg, dict) and msg.get("service") == "shared_postgres":
            return msg.get("config") or {}
    return None


def _candidate_provision_urls(cfg):
    """Provisioning-API URLs to try, most-specific first: the discovered
    provision_url, then hostnames from the Supervisor add-on list, then fixed
    internal-DNS fallbacks."""
    urls, seen = [], set()

    def add(url):
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    if cfg and cfg.get("provision_url"):
        add(cfg["provision_url"])
    for addon in _supervisor_addons():
        slug, name = str(addon.get("slug", "")), str(addon.get("name", ""))
        if "postgres" not in f"{name} {slug}".lower():
            continue
        info = _supervisor_get(f"/addons/{slug}/info") or {}
        host = info.get("hostname") or addon.get("hostname")
        if host:
            add(f"http://{host}:{API_PORT}/provision")
    for host in ("local-shared-postgres", "local-shared_postgres", "shared-postgres"):
        add(f"http://{host}:{API_PORT}/provision")
    return urls


def _existing_sqlite_has_data() -> bool:
    """True if the built-in SQLite DB exists and is non-empty — provisioning a fresh
    (empty) Postgres now would strand it. On a first-ever boot the file does not
    exist yet, so a clean install still provisions normally."""
    path = os.path.join(Config.DATA_DIR, f"{APP_NAME}.db")
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _provision(url: str, token: str):
    payload = json.dumps({"app": APP_NAME}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=PROVISION_TIMEOUT) as resp:
        return json.loads(resp.read() or b"{}").get("dsn")


def main() -> int:
    if (Config.DATABASE_URL or "").strip():
        return 0  # explicit URL wins; nothing to provision
    if not getattr(Config, "USE_SHARED_POSTGRES", False):
        return 0

    dsn_path = os.path.join(Config.DATA_DIR, DSN_FILENAME)
    if os.path.isfile(dsn_path):
        with open(dsn_path) as fh:
            if fh.read().strip():
                return 0  # already provisioned — stable across restarts

    # Don't strand existing data: if a populated SQLite DB is present and the
    # operator hasn't also asked to migrate it, provisioning a fresh empty
    # Postgres would silently serve a blank app. Stay on SQLite and tell them how
    # to move the data over. (With migrate_from_sqlite on we DO provision, and
    # _maybe_boot_migrate copies SQLite into the new database before serving.)
    if _existing_sqlite_has_data() and not getattr(Config, "MIGRATE_FROM_SQLITE", False):
        _log("use_shared_postgres is on but a local SQLite database already holds data; "
             "staying on SQLite so nothing is stranded. Set migrate_from_sqlite: true as "
             "well to copy it into the shared PostgreSQL on the next start")
        return 0

    if not os.environ.get("SUPERVISOR_TOKEN"):
        _log("use_shared_postgres is set but not running under Home Assistant; staying on SQLite")
        return 0

    cfg = _discovery_config()
    token = ((Config.POSTGRES_PROVISION_TOKEN or "") or (cfg or {}).get("token") or "").strip()
    if not token:
        _log("no provisioning token available — set 'postgres_provision_token' to the "
             "Shared PostgreSQL add-on's token (Settings shows it). Staying on SQLite")
        return 0

    # Candidates are ordered most-trusted first (discovery message, then
    # Supervisor-/addons-identified hosts, then fixed add-on DNS names). HA
    # add-ons share a semi-trusted internal network; the DSN-scheme check below
    # is the backstop against a bad/foreign response being persisted.
    os.makedirs(Config.DATA_DIR, exist_ok=True)
    for url in _candidate_provision_urls(cfg):
        try:
            dsn = _provision(url, token)
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            _log(f"provision via {url} failed: {exc}")
            continue
        if not dsn:
            continue
        if not dsn.startswith(DSN_PREFIX):
            _log(f"ignoring provision response with unsupported DSN scheme from {url}")
            continue
        fd = os.open(dsn_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(dsn)
        _log("provisioned shared PostgreSQL; using it")
        return 0

    _log("Shared PostgreSQL add-on not reachable; staying on SQLite (will retry next start)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
