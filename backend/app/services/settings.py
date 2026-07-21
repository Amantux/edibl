"""Per-household runtime settings, stored in the DB so they survive restarts.

Used for the chat LLM provider: values set here override the add-on / env
defaults, so a provider can be configured in Home Assistant *or* in the Edibl UI
and is remembered either way.
"""
from ..extensions import db
from ..models import Setting

LLM_KEYS = ("llm_provider", "llm_base_url", "llm_api_key", "llm_model")


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


def set_llm(gid, provider=None, base_url=None, api_key=None, model=None):
    """Upsert LLM settings. Any arg left as None is untouched; a string (incl. '')
    is stored. Empty strings fall back to the env default at read time."""
    if provider is not None:
        _set(gid, "llm_provider", provider.strip())
    if base_url is not None:
        _set(gid, "llm_base_url", base_url.strip())
    if model is not None:
        _set(gid, "llm_model", model.strip())
    if api_key is not None:
        _set(gid, "llm_api_key", api_key)
    db.session.commit()
