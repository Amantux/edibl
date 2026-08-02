"""Security hardening: SSRF at the point of use, safe SPA path joins, and
credential-free upstream error text.

Each test pins a specific fix from the code-scanning triage. They assert
BEHAVIOUR (a request is not made, a traversal is refused, a secret is redacted)
rather than the shape of the code, so a refactor that keeps the guarantee keeps
the tests green.
"""
import pytest

from app.services.sanitize import safe_upstream_detail
from app.services.url_guard import llm_url_ok

# --- SSRF: validate the base URL where it is USED, not only where it is saved --


def _no_network(monkeypatch):
    """Record whether an HTTP client is ever constructed.

    Returns a dict the test asserts on POSITIVELY. Raising alone is not enough:
    callers wrap _fetch_models in `except Exception`, so a raising stub would be
    swallowed and the test would pass even with the guard removed. The guarantee
    is 'no client was built', not 'the call failed'.
    """
    import httpx

    seen = {"opened": False}

    def spy(*a, **k):
        seen["opened"] = True
        raise AssertionError("network client opened for a blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)
    return seen


def _cfg(base_url, provider="ollama"):
    return {"provider": provider, "base_url": base_url, "api_key": "", "timeout": 5}


def test_link_local_base_url_is_refused_without_a_request(monkeypatch):
    # 169.254.169.254 is the cloud metadata endpoint. A value supplied through
    # env / the add-on options never passes the /assistant settings guard, so the
    # block has to happen here, at the point of use.
    from app.services.assistant import _fetch_models

    seen = _no_network(monkeypatch)
    with pytest.raises(ValueError, match="refusing to reach"):
        _fetch_models(_cfg("http://169.254.169.254"))
    assert seen["opened"] is False   # blocked BEFORE any client was constructed


def test_private_lan_base_url_still_reaches_the_provider(monkeypatch):
    """The guard must NOT break a self-hosted Ollama on the LAN/loopback."""
    from app.services import assistant as svc

    called = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "llama3"}, {"name": "mistral"}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            called["url"] = url
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(svc, "_ollama_headers", lambda cfg: {})

    assert svc._fetch_models(_cfg("http://192.168.1.50:11434")) == ["llama3", "mistral"]
    assert called["url"] == "http://192.168.1.50:11434/api/tags"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434",        # loopback — the single-box install
    "http://192.168.1.50:11434",     # private LAN — a NAS running Ollama
    "https://api.openai.com/v1",     # public provider
])
def test_guard_allows_legitimate_hosts(url):
    ok, err = llm_url_ok(url)
    assert ok, err


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "ftp://example.com",                          # wrong scheme
])
def test_guard_refuses_unsafe_urls(url):
    ok, _err = llm_url_ok(url)
    assert not ok


# --- SPA path traversal ------------------------------------------------------

def test_spa_serves_a_nested_asset(app, tmp_path, monkeypatch):
    """safe_join must still allow ordinary nested asset paths."""

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    (dist / "index.html").write_text("<html></html>")
    # Set through the app's own config (the FRONTEND_DIST setting), which is
    # how an operator configures it — the module-level constant it used to
    # patch no longer exists.
    app.config["FRONTEND_DIST"] = str(dist)

    r = app.test_client().get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert b"console.log(1)" in r.data


def test_spa_refuses_traversal_without_erroring(app, tmp_path, monkeypatch):
    """A traversal must not 500 and must not serve a file outside the dist dir —
    it falls through to the SPA index instead.

    Driven through _serve_spa directly, not the URL router: Flask normalises
    "/../x" before routing, which would make an HTTP-level test vacuous. The old
    os.path.join built a real path outside dist and os.path.isfile returned True
    for it — that probe is the defect being fixed here.
    """
    from app import _serve_spa

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>")
    (tmp_path / "secret.txt").write_text("root:x:0:0:")
    # Set through the app's own config (the FRONTEND_DIST setting), which is
    # how an operator configures it — the module-level constant it used to
    # patch no longer exists.
    app.config["FRONTEND_DIST"] = str(dist)

    with app.test_request_context("/"):
        body = _serve_spa("../secret.txt")
        if isinstance(body, tuple):          # the "frontend not built" notice
            rendered = body[0].encode()
        else:                                # a file response (the SPA index)
            body.direct_passthrough = False
            rendered = body.get_data()
    assert b"root:" not in rendered
    assert b"spa" in rendered                # fell through to index.html


# --- Upstream error text carries no credentials ------------------------------

@pytest.mark.parametrize("raw, secret", [
    ("401 unauthorized for key sk-abcdef1234567890", "sk-abcdef1234567890"),
    ("request failed: Bearer abcdef1234567890xyz", "abcdef1234567890xyz"),
    ("connect to https://user:hunter2@api.example.com/v1 failed", "hunter2"),
    ('{"error":{"message":"bad","api_key":"sk-livekey123456"}}', "sk-livekey123456"),
    ("Incorrect API key provided: AIzaSyD1234567890abcdefghijklmnop", "AIzaSyD1234"),
])
def test_safe_upstream_detail_redacts_credentials(raw, secret):
    out = safe_upstream_detail(RuntimeError(raw))
    assert secret not in out
    assert "[redacted]" in out


def test_safe_upstream_detail_keeps_a_useful_summary():
    out = safe_upstream_detail(ConnectionRefusedError("connection refused"))
    assert "ConnectionRefusedError" in out      # the type still aids debugging
    assert "connection refused" in out


def test_safe_upstream_detail_truncates_a_long_response_body():
    # Realistic upstream body: prose, so no single token trips the high-entropy
    # redaction — this pins the TRUNCATION guarantee specifically.
    body = "upstream returned an unexpected error while processing the request. " * 80
    out = safe_upstream_detail(RuntimeError(body))
    assert len(out) < 300
    assert out.endswith("…")


def test_list_models_error_is_sanitised(monkeypatch):
    """The client-facing error from the model picker must not carry a key."""
    from app.services import assistant as svc

    monkeypatch.setattr(svc, "_cfg", lambda: {
        "provider": "openai", "base_url": "https://api.openai.com/v1",
        "api_key": "", "timeout": 5, "model": "gpt-4o"})

    def boom(cfg):
        raise RuntimeError("401 unauthorized for key sk-abcdef1234567890")

    monkeypatch.setattr(svc, "_fetch_models", boom)
    out = svc.list_models(provider="openai")
    assert "sk-abcdef1234567890" not in out["error"]
    assert "[redacted]" in out["error"]
