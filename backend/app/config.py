"""Edibl configuration — env-driven so it runs standalone, in Docker, or as a
Home Assistant add-on. Mirrors HomeHoard's hardened config patterns."""
import os
import secrets
import stat
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
    SECRET_KEY = os.environ.get("EDIBL_SECRET_KEY", "change-me-in-production")
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

    @staticmethod
    def _normalize_db_url(url: str) -> str:
        """Pin the psycopg (v3) driver for Postgres URLs. `postgres://` (Heroku
        style) and bare `postgresql://` both resolve to psycopg2 in SQLAlchemy,
        which we don't ship — rewrite them to `postgresql+psycopg://`."""
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @classmethod
    def _validate_db_url(cls, raw: str) -> str:
        """Normalize + scheme-check a database URL (env value or provisioned DSN).
        Only SQLite and Postgres-via-psycopg are supported."""
        url = cls._normalize_db_url(raw)
        scheme = url.split(":", 1)[0]
        if not (scheme.startswith("sqlite") or scheme.startswith("postgresql")):
            raise RuntimeError(
                f"database URL scheme {scheme!r} is unsupported. Only SQLite "
                "(default) and Postgres (postgresql+psycopg://user:pass@host/db) "
                "are supported."
            )
        if scheme.startswith("postgresql+") and scheme != "postgresql+psycopg":
            raise RuntimeError(
                f"database URL driver {scheme!r} isn't bundled — use "
                "postgresql+psycopg:// (the sync psycopg 3 driver Edibl ships)."
            )
        return url

    @classmethod
    def sqlalchemy_uri(cls) -> str:
        raw = (cls.DATABASE_URL or "").strip()
        if raw:  # a blank / whitespace-only value falls through
            return cls._validate_db_url(raw)
        # Shared PostgreSQL: pg_provision wrote the discovered DSN here at boot.
        # Read it rather than routing a runtime value through env precedence.
        if getattr(cls, "USE_SHARED_POSTGRES", False):
            try:
                with open(os.path.join(cls.DATA_DIR, ".database_url")) as fh:
                    dsn = fh.read().strip()
            except OSError:
                dsn = ""
            if dsn:
                return cls._validate_db_url(dsn)
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        return f"sqlite:///{os.path.join(cls.DATA_DIR, 'edibl.db')}"


def ensure_secret_key(config_object) -> tuple[str, bool]:
    """Return ``(secret, was_generated)``, persisting a generated one.

    A signing key regenerated on every restart logs every user out and voids
    every issued API token — a silent, confusing failure. The entrypoint used to
    default EDIBL_SECRET_KEY from /dev/urandom, which did exactly that on each
    container start. If the operator does not supply a real key we generate one
    ONCE and persist it beside the database, so restarts are non-events.

    A placeholder (the shipped ``change-me-in-production`` and friends) counts as
    UNSET, not as a key — otherwise every install would share one public secret.
    """
    configured = (getattr(config_object, "SECRET_KEY", "") or "").strip()
    placeholders = getattr(config_object, "KNOWN_DEFAULT_SECRETS", frozenset())
    if configured and configured not in placeholders:
        return configured, False

    data_dir = config_object.DATA_DIR
    path = os.path.join(data_dir, ".secret_key")
    try:
        with open(path) as fh:
            existing = fh.read().strip()
    except OSError:
        existing = ""
    if existing:
        return existing, False

    generated = secrets.token_urlsafe(48)
    os.makedirs(data_dir, exist_ok=True)
    # 0600 at CREATE, not after: a world-readable window, however brief, is a
    # window in which the signing key can be read.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as fh:
        fh.write(generated)
    return generated, True
