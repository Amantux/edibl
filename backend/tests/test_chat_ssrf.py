"""The LLM base URL is validated on the CHAT/completion path, not only at save.

llm_url_ok ran at save-time, in list_models, and in job opts — but NOT in the
run_chat / _complete path, so an env/add-on-options base_url reached the actual
LLM request with the API key attached. A non-public target — link-local,
loopback, or private LAN — must be refused before any outbound request.
"""
import httpx
import pytest
from types import SimpleNamespace

from app.services import assistant


def _cfg_base_url(monkeypatch, base_url, provider="ollama"):
    monkeypatch.setattr(assistant, "_cfg", lambda gid=None: {
        "provider": provider, "base_url": base_url,
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
    _cfg_base_url(monkeypatch, "http://169.254.169.254")

    assistant.run_chat("g", [{"role": "user", "content": "hi"}])
    assert opened["n"] == 0, "run_chat reached the network for a blocked base URL"


def test_complete_refuses_a_link_local_base_url(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "Client",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("opened for blocked URL")))
    cfg = {"provider": "ollama", "base_url": "http://169.254.169.254",
           "api_key": "k", "model": "x", "timeout": 5}
    with pytest.raises(ValueError):
        assistant._complete(cfg, "sys", "user")


def test_a_private_lan_base_url_is_refused(monkeypatch):
    """The widened guard refuses private LAN too — only globally-routable hosts
    pass, so a self-hosted LAN LLM endpoint is no longer reachable through Edibl."""
    import httpx
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("client opened for blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)
    cfg = {"provider": "ollama", "base_url": "http://192.168.1.5:11434",
           "api_key": "k", "model": "x", "timeout": 5}
    with pytest.raises(ValueError):
        assistant._complete(cfg, "sys", "user")
    assert opened["n"] == 0, "the guard must refuse a private-LAN base URL before any client is built"


def test_a_loopback_base_url_is_refused(monkeypatch):
    """Loopback (127.0.0.8 and the IPv6 loopback) is non-public under the widened
    guard — refused at the point of use, before any client is built."""
    import httpx
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("client opened for blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)
    cfg = {"provider": "ollama", "base_url": "http://127.0.0.1:11434",
           "api_key": "k", "model": "x", "timeout": 5}
    with pytest.raises(ValueError):
        assistant._complete(cfg, "sys", "user")
    assert opened["n"] == 0, "the guard must refuse a loopback base URL before any client is built"


def test_an_unresolvable_base_url_fails_closed(monkeypatch):
    """Fail closed: a host that cannot be resolved is refused, never allowed
    through to a real request."""
    import httpx
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("client opened for blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)
    cfg = {"provider": "ollama", "base_url": "http://no-such-host.invalid",
           "api_key": "k", "model": "x", "timeout": 5}
    with pytest.raises(ValueError):
        assistant._complete(cfg, "sys", "user")
    assert opened["n"] == 0, "the guard must fail closed on an unresolvable base URL"


def _redirect_response(target):
    """A response httpx would answer with a redirect to ``target``. The guarded
    client is built with ``follow_redirects=False``, so the intended follow-up
    rides along as ``response.next_request`` instead of being chased."""
    return SimpleNamespace(next_request=httpx.Request("POST", target))


def test_complete_refuses_a_redirect_to_the_cloud_metadata_host(monkeypatch):
    """Fail closed: a mocked redirect whose target is the cloud metadata endpoint
    must abort the completion — the follow-up is never sent."""
    import httpx

    class _Client:
        def __init__(self):
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return _redirect_response("http://169.254.169.254/latest/meta-data/")

        def send(self, req, **k):
            self.sent.append(str(req.url))
            raise AssertionError("must not follow a redirect to a blocked host")

    client = _Client()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
    cfg = {"provider": "openai", "base_url": "https://api.openai.com/v1",
           "api_key": "k", "model": "x", "timeout": 5}
    with pytest.raises(ValueError, match="refusing to follow redirect"):
        assistant._complete(cfg, "sys", "user")
    assert client.sent == [], "the follow-up of a blocked redirect must not be sent"


def test_complete_follows_a_redirect_to_an_allowed_host(monkeypatch):
    """Positive control: an allowed redirect target IS followed, so the guard
    blocks only what it must (and re-validates each hop)."""
    import httpx

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Client:
        def __init__(self):
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return _redirect_response("https://api.openai.com/v1/chat/completions")

        def send(self, req, **k):
            self.sent.append(str(req.url))
            return _Resp()

    client = _Client()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
    cfg = {"provider": "openai", "base_url": "https://api.openai.com/v1",
           "api_key": "k", "model": "x", "timeout": 5}
    assert assistant._complete(cfg, "sys", "user") == "ok"
    assert client.sent == ["https://api.openai.com/v1/chat/completions"]


def test_stream_refuses_a_redirect_to_a_private_host(monkeypatch):
    """Fail closed: the streaming turn re-validates each redirect hop before
    following; a rebound-to-private target aborts the turn before the follow-up."""
    import httpx

    class _Ctx:
        def __init__(self, resp):
            self._resp = resp

        def __enter__(self):
            return self._resp

        def __exit__(self, *a):
            return False

    class _Client:
        def __init__(self):
            self.urls = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, **k):
            self.urls.append(url)
            return _Ctx(_redirect_response("http://192.168.1.9:11434/api/chat"))

        def send(self, req, **k):
            raise AssertionError("must not follow a redirect to a private host")

    client = _Client()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
    gen = assistant._stream_turn(client, "https://api.openai.com/v1/chat/completions",
                                 {}, {"model": "x", "stream": True}, False)
    with pytest.raises(ValueError, match="refusing to follow redirect"):
        next(gen)
    assert client.urls == ["https://api.openai.com/v1/chat/completions"]
