"""Async-job AI preference: separate Enrichment / Background provider+model
defaults for Edibl's background jobs. Precedence: per-run opts > stored preference
> chat provider. Switching a job to a different vendor must NOT leak the chat key."""

from app.extensions import db
from app.models import User
from app.services import assistant
from app.services import settings as st


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def test_job_preference_unset_is_none(app, auth_client):
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        for kind in ("enrich", "categorize"):
            assert st.job_override(gid, kind) == {
                "provider": None, "model": None, "base_url": None, "api_key": None}


def test_organize_preference_shared_by_categorize_and_cluster(app, auth_client):
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        st.set_job_settings(gid, organize={"provider": "ollama", "model": "llama3.1"})
        for kind in ("categorize", "cluster"):
            got = st.job_override(gid, kind)
            assert (got["provider"], got["model"]) == ("ollama", "llama3.1")
        assert st.job_override(gid, "enrich")["provider"] is None  # enrich is separate


def test_job_cfg_switching_vendor_drops_the_chat_key(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(assistant, "_cfg", lambda gid=None: {
            "provider": "anthropic", "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-secret", "model": "claude", "agent_id": "",
            "timeout": 60, "max_steps": 6})
        cfg = assistant.job_cfg("g1", "enrich", {"provider": "ollama"})
        assert cfg["provider"] == "ollama"
        assert cfg["api_key"] == ""                 # the anthropic key must not reach ollama
        assert "sk-ant-secret" not in str(cfg)


def test_job_cfg_model_only_keeps_provider_and_key(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(assistant, "_cfg", lambda gid=None: {
            "provider": "ollama", "base_url": "http://h", "api_key": "k",
            "model": "big", "agent_id": "", "timeout": 60, "max_steps": 6})
        cfg = assistant.job_cfg("g1", "enrich", {"model": "smaller"})
        assert cfg["provider"] == "ollama" and cfg["model"] == "smaller" and cfg["api_key"] == "k"


def test_stored_pref_applies_without_a_request_context(app, auth_client, monkeypatch):
    """Regression: jobs run in the worker (app context, NO request). The stored
    per-group preference must still apply — it was read via current_group(), which
    the worker never sets, so the whole feature was silently inert."""
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        st.set_job_settings(gid, organize={"provider": "ollama", "model": "tiny"})
        # A bare app context simulates the worker (no g.current_group).
        monkeypatch.setattr(assistant, "_cfg", lambda gid=None: {
            "provider": "", "base_url": "", "api_key": "", "model": "",
            "agent_id": "", "timeout": 60, "max_steps": 6})
        cfg = assistant.job_cfg(gid, "categorize", {})
        assert cfg["provider"] == "ollama" and cfg["model"] == "tiny"


def test_ui_configured_provider_applies_to_jobs_without_request(app, auth_client, monkeypatch):
    """A group that set its provider in the Edibl UI (per-group override, no env)
    must have its jobs use THAT provider. The worker has no request context, so
    _cfg(gid) must resolve the group's overrides instead of falling back to env —
    the other half of the current_group() blocker."""
    from app.services.settings import set_llm
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        set_llm(gid, provider="ollama", base_url="http://ui-host:11434", model="ui-model")
        monkeypatch.setitem(app.config, "LLM_PROVIDER", "")   # nothing in env/add-on
        cfg = assistant.job_cfg(gid, "enrich", {})            # "same as chat", no override
        assert cfg["provider"] == "ollama"
        assert cfg["base_url"] == "http://ui-host:11434"
        assert cfg["model"] == "ui-model"


def test_job_settings_accepts_any_valid_provider(auth_client):
    # Per-provider keys removed the Ollama/HA-only restriction — any real provider is fine.
    assert auth_client.put("/api/v1/assistant/job-settings",
                           json={"enrich": {"provider": "openai"}}).status_code == 200
    assert auth_client.put("/api/v1/assistant/job-settings",
                           json={"enrich": {"provider": "bogus"}}).status_code == 422


def test_keys_are_isolated_per_provider(app, auth_client):
    """Two providers keyed → each resolves its OWN key; one vendor's key never
    appears in another's config."""
    from app.services.settings import set_llm
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        set_llm(gid, provider="openai", api_key="sk-openai")
        set_llm(gid, provider="ollama", api_key="sk-ollama-secured")  # active now ollama
        cfg = assistant._cfg(gid)
        assert cfg["provider"] == "ollama" and cfg["api_key"] == "sk-ollama-secured"
        assert "sk-openai" not in str(cfg)


def test_job_switch_uses_the_switched_providers_own_key(app, auth_client):
    """A job switched to a different (hosted) vendor uses THAT vendor's stored key —
    not the chat provider's key. This is what per-provider storage buys us."""
    from app.services.settings import set_llm
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        set_llm(gid, provider="anthropic", api_key="sk-ant-chat")
        set_llm(gid, provider="openai", api_key="sk-openai-jobs")
        set_llm(gid, provider="anthropic")   # chat stays anthropic; openai key persists
        cfg = assistant.job_cfg(gid, "enrich", {"provider": "openai"})
        assert cfg["provider"] == "openai" and cfg["api_key"] == "sk-openai-jobs"
        assert "sk-ant-chat" not in str(cfg)


def test_job_settings_endpoint_roundtrip_and_validation(auth_client):
    r = auth_client.put("/api/v1/assistant/job-settings", json={
        "enrich": {"provider": "ollama", "model": "m1"},
        "organize": {"provider": "", "model": ""}})
    assert r.status_code == 200
    assert r.get_json()["enrich"] == {"provider": "ollama", "model": "m1",
                                      "baseUrl": "", "apiKeySet": False}
    assert auth_client.get("/api/v1/assistant/job-settings").get_json()["enrich"]["provider"] == "ollama"

    bad = auth_client.put("/api/v1/assistant/job-settings", json={"enrich": {"provider": "bogus"}})
    assert bad.status_code == 422


def test_job_settings_requires_auth(client):
    assert client.get("/api/v1/assistant/job-settings").status_code == 401
