"""Background jobs can run on their own SLM server.

Mirrors myMeal and HomeHoard. The point: a fast hosted model for interactive
chat, and a small local model on your own box for the slow async work. That needs
a per-area BASE URL, not just a provider — two Ollama servers are the common case
and both would otherwise resolve the single shared base URL.
"""
import pytest

from app.extensions import db
from app.models import User
from app.services import assistant
from app.services import settings as st


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _chat_cfg(monkeypatch, host="http://fast-box:11434"):
    monkeypatch.setattr(assistant, "_cfg", lambda gid=None: {
        "provider": "ollama", "base_url": host, "api_key": "", "model": "chat-model",
        "agent_id": "", "timeout": 60, "max_steps": 6})


def test_an_unset_area_still_means_same_as_chat(app, auth_client, monkeypatch):
    """The default must not change now that the override carries a URL and key."""
    gid = _gid(app)
    with app.app_context():
        _chat_cfg(monkeypatch)
        cfg = assistant.job_cfg(gid, "enrich", {})
        assert cfg["base_url"] == "http://fast-box:11434"


def test_a_job_can_point_at_its_own_server(app, auth_client, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        st.set_job_settings(gid, enrich={"provider": "ollama", "model": "qwen3:4b",
                                         "baseUrl": "http://192.168.1.50:11434"})
        _chat_cfg(monkeypatch)
        cfg = assistant.job_cfg(gid, "enrich", {})
        assert cfg["base_url"] == "http://192.168.1.50:11434"
        assert cfg["model"] == "qwen3:4b"


def test_the_async_server_does_not_leak_into_chat(app, auth_client, monkeypatch):
    """If the async host bled into the chat config it would silently move
    interactive traffic onto the slow box."""
    gid = _gid(app)
    with app.app_context():
        st.set_job_settings(gid, enrich={"provider": "ollama",
                                         "baseUrl": "http://slow-box:11434"})
        _chat_cfg(monkeypatch)
        assert assistant.job_cfg(gid, "enrich", {})["base_url"] == "http://slow-box:11434"
        # The chat config itself is untouched.
        assert assistant._cfg(gid)["base_url"] == "http://fast-box:11434"


def test_the_two_job_areas_are_independent(app, auth_client, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        st.set_job_settings(gid,
                            enrich={"provider": "ollama", "baseUrl": "http://box-a:11434"},
                            organize={"provider": "ollama", "baseUrl": "http://box-b:11434"})
        _chat_cfg(monkeypatch)
        assert assistant.job_cfg(gid, "enrich", {})["base_url"] == "http://box-a:11434"
        assert assistant.job_cfg(gid, "categorize", {})["base_url"] == "http://box-b:11434"


def test_a_per_run_option_beats_the_stored_server(app, auth_client, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        st.set_job_settings(gid, enrich={"provider": "ollama",
                                         "baseUrl": "http://stored:11434"})
        _chat_cfg(monkeypatch)
        cfg = assistant.job_cfg(gid, "enrich", {"baseUrl": "http://per-run:11434"})
        assert cfg["base_url"] == "http://per-run:11434"


def test_the_async_key_wins_over_the_provider_default(app, auth_client, monkeypatch):
    """Applied last, so it beats the per-provider key chosen above it."""
    gid = _gid(app)
    with app.app_context():
        st.set_job_settings(gid, enrich={"provider": "ollama", "apiKey": "sk-async"})
        _chat_cfg(monkeypatch)
        assert assistant.job_cfg(gid, "enrich", {})["api_key"] == "sk-async"


# --- secrets ----------------------------------------------------------------

def test_the_async_api_key_is_never_returned(auth_client):
    auth_client.put("/api/v1/assistant/job-settings",
                    json={"enrich": {"provider": "ollama", "apiKey": "sk-async-secret"}})

    body = auth_client.get("/api/v1/assistant/job-settings").get_json()

    assert body["enrich"]["apiKeySet"] is True
    assert "sk-async-secret" not in str(body)
    assert "apiKey" not in body["enrich"]


def test_a_blank_apikey_on_resave_keeps_the_stored_one(app, auth_client):
    """Sends apiKey="" explicitly, which is what a form does when the field is
    left empty — omitting it instead would never exercise the rule."""
    auth_client.put("/api/v1/assistant/job-settings",
                    json={"enrich": {"apiKey": "sk-keep-me"}})
    auth_client.put("/api/v1/assistant/job-settings",
                    json={"enrich": {"model": "other", "apiKey": ""}})

    gid = _gid(app)
    with app.app_context():
        assert st.job_override(gid, "enrich")["api_key"] == "sk-keep-me"


def test_clearing_the_async_key_is_explicit(app, auth_client):
    auth_client.put("/api/v1/assistant/job-settings", json={"enrich": {"apiKey": "sk-gone"}})
    auth_client.put("/api/v1/assistant/job-settings", json={"enrich": {"clearApiKey": True}})

    gid = _gid(app)
    with app.app_context():
        assert st.job_override(gid, "enrich")["api_key"] is None


# --- the URL guard ----------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "http://169.254.169.254",          # cloud metadata
    "file:///etc/passwd",
    "not-a-url",
])
def test_an_unsafe_async_server_is_refused_on_save(auth_client, bad):
    r = auth_client.put("/api/v1/assistant/job-settings", json={"enrich": {"baseUrl": bad}})
    assert r.status_code == 422


def test_an_unsafe_server_supplied_per_run_is_refused_at_use(app, auth_client, monkeypatch):
    """Per-run opts never pass the settings guard, so the check has to exist at
    the point of USE too."""
    gid = _gid(app)
    with app.app_context():
        _chat_cfg(monkeypatch)
        with pytest.raises(ValueError) as ei:
            assistant.job_cfg(gid, "enrich", {"baseUrl": "http://169.254.169.254"})
    assert "not allowed" in str(ei.value)


def test_a_private_lan_server_is_allowed(auth_client):
    """Self-hosting on the LAN is the entire use case — it must not be blocked."""
    r = auth_client.put("/api/v1/assistant/job-settings",
                        json={"enrich": {"provider": "ollama",
                                         "baseUrl": "http://192.168.1.50:11434"}})
    assert r.status_code == 200
    assert r.get_json()["enrich"]["baseUrl"] == "http://192.168.1.50:11434"
