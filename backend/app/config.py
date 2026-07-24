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
    def sqlalchemy_uri(cls) -> str:
        raw = (cls.DATABASE_URL or "").strip()
        if raw:  # a blank / whitespace-only value falls through to SQLite
            url = cls._normalize_db_url(raw)
            scheme = url.split(":", 1)[0]
            if not (scheme.startswith("sqlite") or scheme.startswith("postgresql")):
                raise RuntimeError(
                    f"EDIBL_DATABASE_URL scheme {scheme!r} is unsupported. Only SQLite "
                    "(default) and Postgres (postgresql+psycopg://user:pass@host/db) "
                    "are supported."
                )
            if scheme.startswith("postgresql+") and scheme != "postgresql+psycopg":
                raise RuntimeError(
                    f"EDIBL_DATABASE_URL driver {scheme!r} isn't bundled — use "
                    "postgresql+psycopg:// (the sync psycopg 3 driver Edibl ships)."
                )
            return url
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        return f"sqlite:///{os.path.join(cls.DATA_DIR, 'edibl.db')}"
