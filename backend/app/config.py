"""Edibl configuration — env-driven so it runs standalone, in Docker, or as a
Home Assistant add-on. Mirrors HomeHoard's hardened config patterns."""
import os
from datetime import timedelta


def _bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Storage ---------------------------------------------------------
    DATA_DIR = os.environ.get("EDIBL_DATA_DIR", os.path.abspath("./data"))
    DATABASE_URL = os.environ.get("EDIBL_DATABASE_URL")
    # One-shot: when DATABASE_URL points at an EMPTY Postgres and a local SQLite DB
    # exists, copy the SQLite data into Postgres on startup before serving.
    MIGRATE_FROM_SQLITE = _bool("EDIBL_MIGRATE_FROM_SQLITE", False)
    # Home Assistant only: discover the "Shared PostgreSQL" add-on and use a
    # database it provisions for Edibl. The entrypoint's pg_provision step writes
    # the discovered DSN to <DATA_DIR>/.database_url, which sqlalchemy_uri reads.
    # Ignored when DATABASE_URL is set. Off by default (built-in SQLite).
    USE_SHARED_POSTGRES = _bool("EDIBL_USE_SHARED_POSTGRES", False)
    # Token for the Shared PostgreSQL add-on's provisioning API. Blank = auto-obtain
    # it via Supervisor discovery; set it if discovery can't supply it.
    POSTGRES_PROVISION_TOKEN = os.environ.get("EDIBL_POSTGRES_PROVISION_TOKEN", "")

    # --- Security --------------------------------------------------------
    SECRET_KEY = os.environ.get("EDIBL_SECRET_KEY", "")
    JWT_EXPIRES = timedelta(hours=int(os.environ.get("EDIBL_JWT_HOURS", "72")))
    KNOWN_DEFAULT_SECRETS = frozenset({
        "change-me-in-production",
        "please-change-me-to-a-long-random-string",
    })
    # Single-tenant behind a trusted proxy / HA ingress (no per-request auth).
    DISABLE_AUTH = _bool("EDIBL_DISABLE_AUTH", False)
    ALLOW_REGISTRATION = _bool("EDIBL_ALLOW_REGISTRATION", True)
    # Background job worker (async AI tooling). On by default; off in tests, which
    # drive the job functions directly.
    WORKER_ENABLED = _bool("EDIBL_WORKER_ENABLED", True)
    # Auto-categorization: a proposed category at/above this model-reported confidence
    # (and within the known CATEGORIES) is applied automatically; below it, or an
    # unknown category, goes to the review queue. 0..1.
    AI_CONFIDENCE_THRESHOLD = float(os.environ.get("EDIBL_AI_CONFIDENCE_THRESHOLD", "0.8"))
    MIN_PASSWORD_LENGTH = int(os.environ.get("EDIBL_MIN_PASSWORD_LENGTH", "8"))
    # New households start with a default Kitchen/Fridge/Freezer so intake works
    # immediately. Off in tests for a clean baseline.
    SEED_DEFAULTS = _bool("EDIBL_SEED_DEFAULTS", True)

    # --- Network / proxy -------------------------------------------------
    CORS_ORIGINS = [
        o.strip() for o in os.environ.get("EDIBL_CORS_ORIGINS", "").split(",") if o.strip()
    ]
    PROXY_HOPS = int(os.environ.get("EDIBL_PROXY_HOPS", "0"))
    RATELIMIT_ENABLED = _bool("EDIBL_RATELIMIT_ENABLED", True)

    # --- Integration -----------------------------------------------------
    # Base URLs of sibling apps this instance may query (myMeal / HomeHoard).
    MYMEAL_URL = os.environ.get("EDIBL_MYMEAL_URL", "")
    MYMEAL_TOKEN = os.environ.get("EDIBL_MYMEAL_TOKEN", "")
    HOMEHOARD_URL = os.environ.get("EDIBL_HOMEHOARD_URL", "")
    HOMEHOARD_TOKEN = os.environ.get("EDIBL_HOMEHOARD_TOKEN", "")

    # --- Chat assistant (provider-neutral LLM) ---------------------------
    # Point the in-app assistant at whichever endpoint you run. Designed for
    # Home Assistant deployments: use a local Ollama, an OpenAI-compatible
    # endpoint, or Anthropic. Leave provider empty for the built-in rules-based
    # assistant (works with zero config; handles the common intents).
    #   EDIBL_LLM_PROVIDER = ollama | openai | anthropic | ""(rules)
    LLM_PROVIDER = os.environ.get("EDIBL_LLM_PROVIDER", "").strip().lower()
    LLM_BASE_URL = os.environ.get("EDIBL_LLM_BASE_URL", "").strip().rstrip("/")
    LLM_API_KEY = os.environ.get("EDIBL_LLM_API_KEY", "").strip()
    LLM_MODEL = os.environ.get("EDIBL_LLM_MODEL", "").strip()
    # Only for the `homeassistant` provider: which HA conversation agent to target
    # (e.g. conversation.ollama). Blank = HA's default agent. Also settable in the
    # UI, which overrides this — the effective value is UI > this env/option.
    LLM_AGENT_ID = os.environ.get("EDIBL_LLM_AGENT_ID", "").strip()
    LLM_TIMEOUT = int(os.environ.get("EDIBL_LLM_TIMEOUT", "60"))
    LLM_MAX_STEPS = int(os.environ.get("EDIBL_LLM_MAX_STEPS", "6"))
    # Ollama hosted web-search key (ollama.com) for AI product descriptions. The
    # model/base for phrasing come from the existing ollama LLM provider config.
    OLLAMA_SEARCH_KEY = os.environ.get("EDIBL_OLLAMA_SEARCH_KEY", "")

    # --- Barcode enrichment ----------------------------------------------
    # When a scanned barcode isn't known locally, optionally look it up in the
    # public Open Food Facts database (network; off by default).
    BARCODE_LOOKUP = _bool("EDIBL_BARCODE_LOOKUP", False)
    # Product barcode database for non-food / OFF-miss codes (UPCitemdb trial by
    # default — no key needed; a keyed endpoint can be supplied). Falls back to the
    # Ollama web search when both miss.
    BARCODE_DB_URL = os.environ.get(
        "EDIBL_BARCODE_DB_URL", "https://api.upcitemdb.com/prod/trial/lookup")
    BARCODE_DB_KEY = os.environ.get("EDIBL_BARCODE_DB_KEY", "")

    MAX_UPLOAD_BYTES = int(os.environ.get("EDIBL_MAX_UPLOAD_MB", "25")) * 1024 * 1024
    JSON_SORT_KEYS = False

    # --- delegation to the configuration registry -------------------------
    # These used to hold a SECOND implementation of DB-URL normalization, the
    # SQLite fallback and secret persistence. They now forward to app.settings so
    # there is exactly one, per the one-adapter rule. Kept by name because
    # migrations/env.py, services/db_copy and the tests call them.

    @staticmethod
    def _normalize_db_url(url: str) -> str:
        from .settings import normalize_db_scheme
        return normalize_db_scheme(url)

    @classmethod
    def sqlalchemy_uri(cls) -> str:
        """Resolve the database URL, honouring attributes set on a subclass.

        validate=False: this also answers "which database?" for the bare
        `alembic` CLI, a recovery path that must not abort because an unrelated
        setting is wrong.
        """
        from .settings import FIELDS_BY_NAME, load_settings
        if cls is Config:
            return load_settings(validate=False).sqlalchemy_uri
        overrides = {n: getattr(cls, n) for n in FIELDS_BY_NAME if hasattr(cls, n)}
        return load_settings(overrides=overrides, ha_options={},
                             validate=False).sqlalchemy_uri


def ensure_secret_key(config_object) -> tuple[str, bool]:
    """Adapter over :func:`app.settings.ensure_secret_key`.

    Accepts either a config OBJECT (tests, legacy callers) or a MAPPING such as
    ``app.config`` — create_app has no config object once settings are resolved.
    Getting this wrong is silent and severe: reading no key would generate a new
    one on every boot, which is the exact bug the persisted key exists to fix.
    """
    def _get(obj, name):
        if hasattr(obj, "get") and not isinstance(obj, type):
            return obj.get(name) or ""
        return getattr(obj, name, "") or ""

    from .settings import _is_placeholder, ensure_secret_key as _ensure

    supplied = _get(config_object, "SECRET_KEY")
    # Edibl treats a KNOWN PLACEHOLDER as "not supplied" and generates a real
    # key. This predates the registry and is deliberate: the placeholder used to
    # be Config's own default, so without it every install would have shared one
    # public signing key (tests/test_secret_key.py documents exactly that).
    # An operator who sets a placeholder explicitly WITH auth enabled is still
    # refused earlier, by _validate_semantics — they get told, not silently
    # patched. HomeHoard passes placeholders through instead, which is why this
    # lives in the adapter and not in the shared implementation.
    if _is_placeholder(supplied):
        supplied = ""
    return _ensure(supplied, _get(config_object, "DATA_DIR"))
