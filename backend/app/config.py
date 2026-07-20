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

    MAX_UPLOAD_BYTES = int(os.environ.get("EDIBL_MAX_UPLOAD_MB", "25")) * 1024 * 1024
    JSON_SORT_KEYS = False

    @classmethod
    def sqlalchemy_uri(cls) -> str:
        if cls.DATABASE_URL:
            return cls.DATABASE_URL
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        return f"sqlite:///{os.path.join(cls.DATA_DIR, 'edibl.db')}"
