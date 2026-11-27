"""Adversarial SSRF tests for the LLM base-URL guard (url_guard).

Where test_chat_ssrf.py / test_security_hardening.py pin the regression fixes,
this module probes the guard the way an attacker would: scheme escapes (file://,
ftp://, empty scheme), the blank-URL fallback, every blocked IP-literal address
class, and the fail-closed branches (unresolvable host, redirect rebound to a
blocked address).

The guard resolves through socket.getaddrinfo, so the rebinding cases mock it
and no test here needs real DNS.
"""
import socket
from types import SimpleNamespace

import httpx
import pytest

from app.services import assistant
from app.services.url_guard import llm_url_ok, guarded_redirect_target, guarded_send


def _redirect_response(target):
    """A response httpx would answer with a redirect to ``target``. The guarded
    client is built with ``follow_redirects=False``, so the intended follow-up
    rides along as ``response.next_request`` instead of being chased."""
    return SimpleNamespace(next_request=httpx.Request("POST", target))


# --- Scheme escape -----------------------------------------------------------


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd",                    # file:// reads/writes the host filesystem
    "file://127.0.0.1/etc/passwd",
    "ftp://example.com/x",                  # ftp:// is not an LLM endpoint scheme
    "gopher://example.com/x",
    "ws://example.com/x",
    "//169.254.169.254/latest/meta-data/",  # empty scheme, authority present
    "/etc/passwd",                          # empty scheme, no authority at all
    "http://",                              # http scheme but no host
])
def test_llm_url_ok_refuses_a_disallowed_scheme(bad):
    ok, err = llm_url_ok(bad)
    assert not ok, f"{bad} must be refused, got ok={ok} err={err}"


def test_complete_refuses_a_file_scheme_base_url_before_any_client_is_built(monkeypatch):
    # run_chat / _complete swallow provider errors into friendly text, so the
    # guarantee is asserted on "no client was built", not on the reply.
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("client opened for a blocked scheme")

    monkeypatch.setattr(httpx, "Client", spy)
    cfg = {"provider": "ollama", "base_url": "file:///etc/passwd", "api_key": "k",
           "model": "x", "timeout": 5}
    with pytest.raises(ValueError, match="http or https"):
        assistant._complete(cfg, "sys", "user")
    assert opened["n"] == 0, "the guard must refuse a file:// base URL before any client is built"


# --- Blank / empty URL -------------------------------------------------------


@pytest.mark.parametrize("blank", ["", None])
def test_llm_url_ok_treats_a_blank_url_as_the_configured_default(blank):
    ok, err = llm_url_ok(blank)
    assert ok is True
    assert err is None


def test_assert_base_url_ok_accepts_a_blank_base_url():
    """Blank falls back to the configured default (the homeassistant provider uses
    the Supervisor endpoint) rather than being treated as a host to dial."""
    assistant._assert_base_url_ok({"base_url": ""})
    assistant._assert_base_url_ok({"base_url": None})


# --- IP literals ------------------------------------------------------------


BLOCKED_IP_LITERALS = [
    "http://127.0.0.1:11434",                     # IPv4 loopback
    "http://0.0.0.0:11434",                       # unspecified address
    "http://10.0.0.5:8099",                       # private LAN, 10/8
    "http://172.16.0.9:8099",                    # private LAN, 172.16/12
    "http://169.254.169.254/latest/meta-data/",  # link-local / cloud metadata
    "http://[::1]:11434/api/chat",                # IPv6 loopback
    "http://[::ffff:127.0.0.1]:11434/api/chat", # IPv4-mapped IPv6 loopback
    "http://[fe80::1]/x",                         # IPv6 link-local
]

PUBLIC_IP_LITERALS = [
    "http://8.8.8.8:8080/x",
    "https://1.1.1.1/v1",
]


@pytest.mark.parametrize("url", BLOCKED_IP_LITERALS)
def test_llm_url_ok_refuses_blocked_ip_literals(url):
    ok, err = llm_url_ok(url)
    assert not ok, f"{url} must be refused, got ok={ok} err={err}"


@pytest.mark.parametrize("url", PUBLIC_IP_LITERALS)
def test_llm_url_ok_allows_public_ip_literals(url):
    ok, err = llm_url_ok(url)
    assert ok, err


def test_complete_refuses_an_ipv4_mapped_loopback_base_url(monkeypatch):
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("client opened for a blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)
    cfg = {"provider": "ollama", "base_url": "http://[::ffff:127.0.0.1]:11434",
           "api_key": "k", "model": "x", "timeout": 5}
    with pytest.raises(ValueError):
        assistant._complete(cfg, "sys", "user")
    assert opened["n"] == 0, "the guard must refuse the IPv4-mapped loopback before any client is built"


# --- Fail closed: unresolvable / rebound hosts ------------------------------


def test_llm_url_ok_fails_closed_on_an_unresolvable_host(monkeypatch):
    def boom(*a, **k):
        raise OSError("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ok, err = llm_url_ok("http://no-such-host.invalid")
    assert not ok
    assert "could not be resolved" in err


def test_llm_url_ok_fails_closed_when_resolution_rebinds_to_a_private_ip(monkeypatch):
    """A host that resolves to a private address (DNS rebinding) must be refused."""

    def fake(*a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    ok, err = llm_url_ok("http://llm.internal.example")
    assert not ok, f"rebinding to a private IP must fail closed, got ok={ok} err={err}"


# --- Fail closed: redirects rebound to a blocked address ---------------------


def test_guarded_redirect_target_refuses_a_redirect_to_a_blocked_address():
    with pytest.raises(ValueError, match="refusing to follow redirect"):
        guarded_redirect_target(_redirect_response("http://10.0.0.9:11434/api/chat"))


def test_guarded_send_aborts_a_follow_up_to_a_blocked_redirect_target():
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
    with pytest.raises(ValueError, match="refusing to follow redirect"):
        guarded_send(client, "post", "https://api.openai.com/v1/chat/completions", json={})
    assert client.sent == []


def test_complete_refuses_a_redirect_to_a_private_ip_literal(monkeypatch):
    """Fail closed: a redirect whose target is a private-LAN IP must abort the
    completion — the follow-up is never sent."""

    class _Client:
        def __init__(self):
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return _redirect_response("http://10.0.0.9:11434/api/chat")

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
