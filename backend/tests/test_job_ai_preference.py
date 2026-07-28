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
        assert st.job_preference(gid, "enrich") == (None, None)
        assert st.job_preference(gid, "categorize") == (None, None)


def test_organize_preference_shared_by_categorize_and_cluster(app, auth_client):
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        st.set_job_settings(gid, organize={"provider": "ollama", "model": "llama3.1"})
        assert st.job_preference(gid, "categorize") == ("ollama", "llama3.1")
        assert st.job_preference(gid, "cluster") == ("ollama", "llama3.1")
        assert st.job_preference(gid, "enrich") == (None, None)  # enrich is separate


def test_job_cfg_switching_vendor_drops_the_chat_key(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(assistant, "_cfg", lambda: {
            "provider": "anthropic", "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-secret", "model": "claude", "agent_id": "",
            "timeout": 60, "max_steps": 6})
        cfg = assistant.job_cfg("g1", "enrich", {"provider": "ollama"})
        assert cfg["provider"] == "ollama"
        assert cfg["api_key"] == ""                 # the anthropic key must not reach ollama
        assert "sk-ant-secret" not in str(cfg)


def test_job_cfg_model_only_keeps_provider_and_key(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(assistant, "_cfg", lambda: {
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
        monkeypatch.setattr(assistant, "_cfg", lambda: {
            "provider": "", "base_url": "", "api_key": "", "model": "",
            "agent_id": "", "timeout": 60, "max_steps": 6})
        cfg = assistant.job_cfg(gid, "categorize", {})
        assert cfg["provider"] == "ollama" and cfg["model"] == "tiny"


def test_job_settings_rejects_hosted_vendor(auth_client):
    # Edibl stores one key; a different hosted vendor for jobs would run keyless.
    r = auth_client.put("/api/v1/assistant/job-settings",
                        json={"enrich": {"provider": "openai"}})
    assert r.status_code == 422


def test_job_settings_endpoint_roundtrip_and_validation(auth_client):
    r = auth_client.put("/api/v1/assistant/job-settings", json={
        "enrich": {"provider": "ollama", "model": "m1"},
        "organize": {"provider": "", "model": ""}})
    assert r.status_code == 200
    assert r.get_json()["enrich"] == {"provider": "ollama", "model": "m1"}
    assert auth_client.get("/api/v1/assistant/job-settings").get_json()["enrich"]["provider"] == "ollama"

    bad = auth_client.put("/api/v1/assistant/job-settings", json={"enrich": {"provider": "bogus"}})
    assert bad.status_code == 422


def test_job_settings_requires_auth(client):
    assert client.get("/api/v1/assistant/job-settings").status_code == 401
