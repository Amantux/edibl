"""Security hardening: SSRF at the point of use, safe SPA path joins, and
credential-free upstream error text.

Each test pins a specific fix from the code-scanning triage. They assert
BEHAVIOUR (a request is not made, a traversal is refused, a secret is redacted)
rather than the shape of the code, so a refactor that keeps the guarantee keeps
the tests green.
"""
import pytest
import socket

from app.services.sanitize import safe_upstream_detail
from app.services.url_guard import llm_url_ok, resolve_and_validate

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


# --- url_guard: fail closed on unresolvable hosts, resolve once + pin --------


def _fake_resolve(monkeypatch, addresses):
    """Point ``socket.getaddrinfo`` at fixed addresses, so no test here needs real DNS."""
    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in addresses]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_resolve_and_validate_pins_the_validated_public_ip(monkeypatch):
    _fake_resolve(monkeypatch, ["8.8.8.8"])
    ok, pinned, err = resolve_and_validate("http://api.openai.com/v1")
    assert ok is True
    assert err is None
    assert pinned == ["8.8.8.8"]


def test_resolve_and_validate_has_no_pins_for_a_blank_url():
    # Blank URL → fall back to the configured default; nothing to reach or pin.
    assert resolve_and_validate("") == (True, None, None)
    assert resolve_and_validate(None) == (True, None, None)


def test_resolve_and_validate_refuses_a_scheme_other_than_http_or_https(monkeypatch):
    _fake_resolve(monkeypatch, ["8.8.8.8"])
    ok, pinned, err = resolve_and_validate("ftp://api.openai.com/v1")
    assert ok is False
    assert pinned is None
    assert err == "base URL must be http or https"


def test_resolve_and_validate_refuses_a_url_without_a_host():
    ok, pinned, err = resolve_and_validate("http:///api")
    assert ok is False
    assert pinned is None
    assert err == "base URL has no host"


@pytest.mark.parametrize("exc", [
    OSError("name resolution failed"),
    UnicodeError("malformed host"),
])
def test_resolve_and_validate_fails_closed_when_getaddrinfo_raises(monkeypatch, exc):
    # Fail closed: an unresolvable / malformed host is refused, not allowed through.
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ok, pinned, err = resolve_and_validate("http://no-such-host.invalid")
    assert ok is False
    assert pinned is None
    assert err == "base URL host could not be resolved"


def test_resolve_and_validate_unwraps_an_ipv4_mapped_link_local(monkeypatch):
    # ::ffff:169.254.169.254 is the IPv4-mapped form of the cloud metadata endpoint;
    # the mapped-v4 unwrap handling must still refuse it.
    _fake_resolve(monkeypatch, ["::ffff:169.254.169.254"])
    ok, pinned, err = resolve_and_validate("http://169.254.169.254")
    assert ok is False
    assert pinned is None
    assert err == "base URL host is not allowed"


def test_llm_url_ok_fails_closed_on_an_unresolvable_host(monkeypatch):
    def boom(*a, **k):
        raise OSError("name resolution failed")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ok, err = llm_url_ok("http://no-such-host.invalid")
    assert ok is False
    assert err == "base URL host could not be resolved"


def test_llm_url_ok_fails_closed_on_a_malformed_host(monkeypatch):
    def boom(*a, **k):
        raise UnicodeError("malformed host")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ok, err = llm_url_ok("http://no-such-host.invalid")
    assert ok is False
    assert err == "base URL host could not be resolved"


def test_llm_url_ok_allows_a_public_address(monkeypatch):
    _fake_resolve(monkeypatch, ["8.8.8.8"])
    ok, err = llm_url_ok("http://api.openai.com/v1")
    assert ok is True
    assert err is None


def test_llm_url_ok_refuses_a_link_local_host(monkeypatch):
    _fake_resolve(monkeypatch, ["169.254.169.254"])
    ok, err = llm_url_ok("http://169.254.169.254")
    assert ok is False
    assert err == "base URL host is not allowed"


# --- url_guard: pinned httpx transport (connect to the validated IP) --------


def test_pinned_client_refuses_a_missing_validated_address():
    # No validated address to pin to → refuse rather than letting the request go to a
    # host that was never validated (and so never re-resolved-checked).
    from app.services.url_guard import pinned_client

    with pytest.raises(ValueError):
        pinned_client("http://api.openai.com/v1", None)
    with pytest.raises(ValueError):
        pinned_client("http://api.openai.com/v1", [])


def test_pinned_transport_refuses_a_pin_outside_the_validated_set():
    # The pin must come from the validated set — refuse otherwise (fail closed).
    from app.services.url_guard import PinnedHTTPTransport

    with pytest.raises(ValueError):
        PinnedHTTPTransport(pinned_ip="9.9.9.9", pinned_port=443, validated=["8.8.8.8"])


def _pinned_transport_captures_the_request(url, pinned_ip, pinned_port, validated):
    """Run one request through a PinnedHTTPTransport backed by a fake pool that
    records the httpcore request and then stops the send."""
    import httpx as _httpx
    from app.services.url_guard import PinnedHTTPTransport

    transport = PinnedHTTPTransport(
        pinned_ip=pinned_ip, pinned_port=pinned_port, validated=validated
    )
    captured = {}

    class _FakePool:
        def handle_request(self, req):
            captured["req"] = req
            raise RuntimeError("captured")

    transport._pool = _FakePool()
    req = _httpx.Request("GET", url, headers={"Authorization": "Bearer sk-x"}, json={"a": 1})
    with pytest.raises(RuntimeError):
        transport.handle_request(req)
    return captured["req"]


def test_pinned_transport_connects_to_the_pinned_ip_and_keeps_host_and_sni():
    # (a) the socket goes to the PINNED address, not a re-resolved host; (b) the Host
    # header and the TLS SNI stay the ORIGINAL hostname.
    http_req = _pinned_transport_captures_the_request(
        "https://api.openai.com:8443/v1/models", "8.8.8.8", 8443, ["8.8.8.8"],
    )
    assert http_req.url.host == b"8.8.8.8"          # connected to the pinned IP
    assert http_req.url.port == 8443                  # ...on the validated port
    assert http_req.url.target == b"/v1/models"      # path/query untouched
    hdrs = {k.decode(): v.decode() for k, v in http_req.headers}
    assert hdrs["Host"] == "api.openai.com:8443"   # original host for Host/SNI
    assert hdrs["Authorization"] == "Bearer sk-x"
    assert http_req.extensions["sni_hostname"] == "api.openai.com"


def test_pinned_transport_handles_an_ipv6_pinned_address():
    # An IPv6 pin is formatted for the socket, not mangled.
    http_req = _pinned_transport_captures_the_request(
        "https://myhost.example.com:8443/v1/messages", "2001:db8::5", 8443, ["2001:db8::5"],
    )
    assert http_req.url.host == b"2001:db8::5"
    assert http_req.url.port == 8443
    hdrs = {k.decode(): v.decode() for k, v in http_req.headers}
    assert hdrs["Host"] == "myhost.example.com:8443"
    assert http_req.extensions["sni_hostname"] == "myhost.example.com"


# --- url_guard: DNS rebinding at connect time (fake resolver) -------------------


def _fake_rebinding_resolver(monkeypatch, public_ip="8.8.8.8", internal_ip="127.0.0.1"):
    """Host-aware fake resolver: a HOSTNAME resolves to the public address (the
    check-time verdict: allowed, pinned), while a pinned ADDRESS re-resolves to the
    internal address at connect time — the DNS-rebinding / poisoned-resolver turn.
    """
    import ipaddress

    def fake(host, port=None, *a, **k):
        try:
            ipaddress.ip_address(host)
            ip = internal_ip
        except ValueError:
            ip = public_ip
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


def _fake_pinned_client(monkeypatch, pinned_ip, pinned_port, validated):
    """httpx.Client wired to a PinnedHTTPTransport whose pool records sends and
    then stops the request — so a test can assert the request was never sent."""
    import httpx
    from app.services.url_guard import PinnedHTTPTransport

    sent = {"n": 0}

    class _FakePool:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def handle_request(self, req):
            sent["n"] += 1
            raise RuntimeError("should not be sent")

    real_client = httpx.Client

    def _client(*a, **k):
        transport = PinnedHTTPTransport(
            pinned_ip=pinned_ip, pinned_port=pinned_port, validated=validated,
        )
        transport._pool = _FakePool()
        return real_client(transport=transport, timeout=5)

    monkeypatch.setattr(httpx, "Client", _client)
    return sent


def test_pinned_transport_refuses_a_rebound_connect_address(monkeypatch):
    # The connect-time resolution must still land on a validated address; if the
    # resolver hands back something else the transport refuses BEFORE the request
    # is sent.
    _fake_rebinding_resolver(monkeypatch)
    import httpx
    from app.services.url_guard import PinnedHTTPTransport

    sent = {"n": 0}

    class _FakePool:
        def handle_request(self, req):
            sent["n"] += 1
            raise RuntimeError("should not be sent")

    transport = PinnedHTTPTransport(pinned_ip="8.8.8.8", pinned_port=80, validated=["8.8.8.8"])
    transport._pool = _FakePool()
    req = httpx.Request("POST", "http://api.openai.com/v1/chat/completions", json={"model": "x"})
    with pytest.raises(ValueError):
        transport.handle_request(req)
    assert sent["n"] == 0   # refused, not sent


@pytest.mark.parametrize("internal_ip", ["127.0.0.1", "169.254.169.254"])
def test_outbound_llm_request_is_refused_when_the_resolver_rebinds_to_an_internal_address(
        monkeypatch, internal_ip,
):
    """DNS rebinding: the check-time resolution said PUBLIC (ok, pinned), but by
    connect time the resolver answers with an internal address — the request must be
    refused and never sent."""
    _fake_rebinding_resolver(monkeypatch, internal_ip=internal_ip)
    from app.services import assistant as svc
    from app.services.url_guard import PinnedHTTPTransport

    sent = _fake_pinned_client(monkeypatch, "8.8.8.8", 80, ["8.8.8.8"])

    cfg = {"provider": "ollama", "base_url": "http://api.openai.com/v1", "api_key": "k",
           "model": "x", "timeout": 5}
    with pytest.raises(ValueError):
        svc._complete(cfg, "sys", "user")
    assert sent["n"] == 0   # refused at connect time, not sent


def test_fetch_models_refuses_the_llm_request_when_the_resolver_rebinds(monkeypatch):
    _fake_rebinding_resolver(monkeypatch)
    from app.services import assistant as svc
    from app.services.url_guard import PinnedHTTPTransport

    sent = _fake_pinned_client(monkeypatch, "8.8.8.8", 80, ["8.8.8.8"])
    with pytest.raises(ValueError):
        svc._fetch_models({"provider": "ollama", "base_url": "http://api.openai.com/v1",
                           "api_key": "", "timeout": 5})
    assert sent["n"] == 0


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
