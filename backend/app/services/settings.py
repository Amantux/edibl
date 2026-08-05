"""Per-household runtime settings, stored in the DB so they survive restarts.

Used for the chat LLM provider: values set here override the add-on / env
defaults, so a provider can be configured in Home Assistant *or* in the Edibl UI
and is remembered either way.
"""
from flask import current_app

from ..extensions import db
from ..models import Setting

# Per-provider API keys — one slot per vendor so switching providers (for chat OR a
# background job) keeps each key separate and never sends one vendor's key to
# another's endpoint. Home Assistant uses its Supervisor token (no stored key).
# `llm_api_key` is the legacy single slot, kept as a read fallback for back-compat.
PROVIDER_KEY_KEYS = ("openai_api_key", "anthropic_api_key", "ollama_api_key")
LLM_KEYS = ("llm_provider", "llm_base_url", "llm_api_key", "llm_model",
            "llm_agent_id") + PROVIDER_KEY_KEYS
MYMEAL_KEYS = ("mymeal_url", "mymeal_token")


def _all(gid):
    return {s.key: s.value for s in
            db.session.query(Setting).filter_by(group_id=gid).all()}


def get_llm_overrides(gid):
    """Only the LLM keys the household has explicitly set (may be '')."""
    d = _all(gid)
    return {k: d[k] for k in LLM_KEYS if k in d}


def _set(gid, key, value):
    row = db.session.query(Setting).filter_by(group_id=gid, key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(Setting(group_id=gid, key=key, value=value))


def set_llm(gid, provider=None, base_url=None, api_key=None, model=None, agent_id=None):
    """Upsert LLM settings. Any arg left as None is untouched; a string (incl. '')
    is stored. Empty strings fall back to the env default at read time."""
    if provider is not None:
        _set(gid, "llm_provider", provider.strip())
    if base_url is not None:
        _set(gid, "llm_base_url", base_url.strip())
    if model is not None:
        _set(gid, "llm_model", model.strip())
    if agent_id is not None:
        _set(gid, "llm_agent_id", agent_id.strip())
    if api_key is not None:
        # Store the key under the provider it belongs to, so each vendor's key stays
        # separate. Fall back to the legacy single slot only when no provider is known.
        target = (provider.strip() if provider is not None else "") or _all(gid).get("llm_provider", "")
        _set(gid, f"{target}_api_key" if target else "llm_api_key", api_key)
    db.session.commit()


CURRENCY_KEY = "currency"


def get_currency(gid):
    """The household's currency code for prices (single-currency by design). Falls
    back to the EDIBL_CURRENCY env default, else 'USD'. Uppercased, <=8 chars."""
    try:
        stored = (_all(gid).get(CURRENCY_KEY) or "").strip()
    except Exception:  # noqa: BLE001 — best-effort read, never break pricing
        stored = ""
    code = stored or (current_app.config.get("CURRENCY") or "") or "USD"
    return code.strip().upper()[:8]


def set_currency(gid, code):
    _set(gid, CURRENCY_KEY, (code or "").strip().upper()[:8])
    db.session.commit()


CHAT_STREAM_KEY = "chat_streaming"


def get_chat_stream(gid):
    """This household's default for streaming chat replies (default False)."""
    try:
        return (_all(gid).get(CHAT_STREAM_KEY) or "").strip().lower() == "true"
    except Exception:  # noqa: BLE001 — best-effort read, never break the widget
        return False


def set_chat_stream(gid, on):
    _set(gid, CHAT_STREAM_KEY, "true" if on else "false")
    db.session.commit()


# --- Async-job AI preference (background work, separate from chat) ----------
# "enrich" = product-description jobs; "organize" = categorize + cluster. Blank
# provider = same as the chat provider.
JOB_KEYS = ("enrich_provider", "enrich_model", "organize_provider", "organize_model")


JOB_AREAS = ("enrich", "organize")
_BLANK_JOB = {"provider": None, "model": None, "base_url": None, "api_key": None}


def job_override(gid, kind):
    """Stored async override for a background job: provider/model/base_url/api_key.

    ``enrich`` has its own; the organize jobs (``categorize``/``cluster``) share
    ``organize``. Blank fields mean "same as chat", so an unset area behaves
    exactly as before.

    The base URL matters on its own: the usual setup is a fast hosted model for
    chat and a small local model on your own box for the slow async work. Without
    a per-area host that is only possible when the two are different PROVIDERS —
    two Ollama servers would both resolve the single shared base URL.
    """
    prefix = "enrich" if kind == "enrich" else "organize"
    try:
        d = _all(gid)
    except Exception:  # noqa: BLE001 — best-effort; never break a job
        return dict(_BLANK_JOB)
    return {
        "provider": d.get(f"{prefix}_provider") or None,
        "model": d.get(f"{prefix}_model") or None,
        "base_url": d.get(f"{prefix}_base_url") or None,
        "api_key": d.get(f"{prefix}_api_key") or None,
    }


def get_job_settings(gid):
    """UI view. Never includes the API key value — only whether one is stored."""
    d = _all(gid)
    return {area: {"provider": d.get(f"{area}_provider", ""),
                   "model": d.get(f"{area}_model", ""),
                   "baseUrl": d.get(f"{area}_base_url", ""),
                   "apiKeySet": bool(d.get(f"{area}_api_key", ""))}
            for area in JOB_AREAS}


def set_job_settings(gid, enrich=None, organize=None):
    """Upsert the async-job AI preference. Each area is
    {provider, model, baseUrl, apiKey?, clearApiKey?}; a field left out is
    untouched, '' clears it (falls back to the chat provider).

    The key follows the same rule as the chat provider's: a blank apiKey leaves
    the stored one alone, because the form never receives it back and treating
    blank as a clear would wipe it on every unrelated save. Clearing is explicit.
    """
    for area, blk in (("enrich", enrich), ("organize", organize)):
        if not isinstance(blk, dict):
            continue
        if "provider" in blk:
            _set(gid, f"{area}_provider", str(blk.get("provider") or "").strip())
        if "model" in blk:
            _set(gid, f"{area}_model", str(blk.get("model") or "").strip()[:100])
        if "baseUrl" in blk:
            _set(gid, f"{area}_base_url", str(blk.get("baseUrl") or "").strip()[:300])
        if blk.get("clearApiKey"):
            _set(gid, f"{area}_api_key", "")
        elif blk.get("apiKey"):
            _set(gid, f"{area}_api_key", str(blk["apiKey"]))
    db.session.commit()


def clear_llm(gid):
    """Drop all LLM overrides so the effective config falls back to the add-on /
    env defaults (the 'reset to add-on default' action)."""
    (db.session.query(Setting)
     .filter(Setting.group_id == gid, Setting.key.in_(LLM_KEYS))
     .delete(synchronize_session=False))
    db.session.commit()


def get_mymeal_overrides(gid):
    d = _all(gid)
    return {k: d[k] for k in MYMEAL_KEYS if k in d}


def set_mymeal(gid, url=None, token=None):
    if url is not None:
        _set(gid, "mymeal_url", url.strip())
    if token is not None:
        _set(gid, "mymeal_token", token)
    db.session.commit()
