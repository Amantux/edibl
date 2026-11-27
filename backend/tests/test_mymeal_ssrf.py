"""The stored myMeal integration URL is validated at the point of use.

Edibl blocks link-local for the owner-supplied LLM URL (url_guard.llm_url_ok,
applied at save AND use), but the myMeal URL — set by the same owner at the
same trust level — bypassed the guard entirely. It is fetched with the stored
bearer token attached and the response reflected to the caller, so an owner (or
an env/options value) could turn it into an SSRF oracle at 169.254.169.254.
"""
from app.services import integrations


def test_link_local_mymeal_url_is_refused_before_the_request(monkeypatch):
    import httpx
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("httpx client built for a blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)

    res = integrations._get("http://169.254.169.254", "secrettoken", "/have")
    assert res["reachable"] is False
    assert opened["n"] == 0   # blocked before any token-bearing request

    res2 = integrations._write("POST", "http://169.254.169.254", "tok", "/x", {})
    assert res2["reachable"] is False
    assert opened["n"] == 0


def test_private_lan_mymeal_url_still_reaches_the_sibling(monkeypatch):
    import httpx

    class _Resp:
        content = b"{}"
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, params=None): return _Resp()
        def request(self, *a, **k): return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)

    assert integrations._get("http://192.168.1.9:8099", "t", "/have")["reachable"] is True
    assert integrations._write("POST", "http://10.0.0.5:8099", "t", "/x", {})["reachable"] is True


def test_sibling_request_is_refused_when_the_resolver_rebinds_to_loopback(monkeypatch):
    """DNS rebinding against the sibling URL: check-time resolves public (allowed,
    pinned), connect time resolves loopback (internal) → the request is refused and
    never sent."""
    import ipaddress
    import httpx
    import socket
    from app.services import integrations as integ
    from app.services.url_guard import PinnedHTTPTransport

    def fake(host, port=None, *a, **k):
        try:
            ipaddress.ip_address(host)
            ip = "127.0.0.1"
        except ValueError:
            ip = "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)

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
        transport = PinnedHTTPTransport(pinned_ip="8.8.8.8", pinned_port=8099, validated=["8.8.8.8"])
        transport._pool = _FakePool()
        return real_client(transport=transport, timeout=8)

    monkeypatch.setattr(httpx, "Client", _client)

    res = integ._get("http://api.mymeal.com:8099", "tok", "/api/v1/recipes")
    assert res["reachable"] is False   # refused, not sent
    assert sent["n"] == 0

