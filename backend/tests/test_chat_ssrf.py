"""The LLM base URL is validated on the CHAT/completion path, not only at save.

llm_url_ok ran at save-time, in list_models, and in job opts — but NOT in the
run_chat / _complete path, so an env/add-on-options base_url reached the actual
LLM request with the API key attached. A link-local target must be refused
before any outbound request.
"""
from app.services import assistant


def _cfg_linklocal(monkeypatch):
    monkeypatch.setattr(assistant, "_cfg", lambda gid=None: {
        "provider": "ollama", "base_url": "http://169.254.169.254",
        "api_key": "secret", "model": "x", "agent_id": "",
        "timeout": 5, "max_steps": 3,
    })


def test_run_chat_refuses_a_link_local_base_url(monkeypatch):
    # run_chat swallows every exception into a friendly provider-error, so
    # asserting on the reply is vacuous — count client OPENS instead. The guard
    # must block BEFORE any client is built.
    import httpx
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("client opened for blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)
    _cfg_linklocal(monkeypatch)

    assistant.run_chat("g", [{"role": "user", "content": "hi"}])
    assert opened["n"] == 0, "run_chat reached the network for a blocked base URL"


def test_complete_refuses_a_link_local_base_url(monkeypatch):
    import pytest
    import httpx
    monkeypatch.setattr(httpx, "Client",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("opened for blocked URL")))
    cfg = {"provider": "ollama", "base_url": "http://169.254.169.254",
           "api_key": "k", "model": "x", "timeout": 5}
    with pytest.raises(ValueError):
        assistant._complete(cfg, "sys", "user")


def test_a_private_lan_base_url_is_still_allowed(monkeypatch):
    """The guard must not break a self-hosted LAN LLM — the whole point."""
    import httpx
    called = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, **k):
            called["url"] = url
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    cfg = {"provider": "ollama", "base_url": "http://192.168.1.5:11434",
           "api_key": "k", "model": "x", "timeout": 5}
    assistant._complete(cfg, "sys", "user")
    assert called["url"].startswith("http://192.168.1.5:11434")
