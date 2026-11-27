"""SSRF guard for a user-supplied LLM base URL / Ollama host.

The provider base URL is operator-configurable and arrives from several places —
the Settings API, the add-on options, and the environment. Only the API path used
to validate it, so a value set via env/add-on options reached httpx unchecked.
This module is the single implementation both the save path (api/assistant.py)
and the use path (services/assistant.py::_fetch_models) call.

Blocks link-local (notably the cloud metadata endpoint 169.254.169.254 / fe80::)
while still allowing loopback and private LAN, where a self-hosted Ollama
legitimately runs.

This is a pre-flight check only. A verdict of ``(True, None)`` — allowed — is NOT
an authorization of the outbound connection, including when the host could not
be resolved: the guard fails open on an unresolvable host so the real request can
surface a clean error rather than a 500. The connect-time pinned transport
(:mod:`app.services.pinned_transport`) is the SOLE point of enforcement and fails
closed there (it refuses to connect on an unresolvable host), so a guard-time
``(True, None)`` never authorizes a pinned connection.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def llm_url_ok(url):
    """Return (ok, error). Blank URL → ok (falls back to the configured default).

    Checks the resolved address now; it is not hardened against DNS-rebinding,
    an acceptable residual for a semi-trusted owner configuring their own
    endpoint.

    On an unresolvable / malformed host the guard does NOT refuse — it returns
    ``(True, None)`` so the real request can fail naturally rather than 500. That
    fail-open verdict is a guard-time decision and does NOT authorize the
    connection: the connect-time pinned transport is the sole enforcement point and
    fails closed on an unresolvable host, so the pinned connection is refused rather
    than silently permitted.
    """
    if not url:
        return True, None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "base URL must be http or https"
    host = parsed.hostname
    if not host:
        return False, "base URL has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        # Unresolvable / malformed host: guard-time fail-open → (True, None) so the
        # real request fails rather than a 500. This is NOT an authorization of the
        # connection — the connect-time pinned transport refuses it (fail closed).
        return True, None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) connects to the real
        # IPv4 on a dual-stack host, so test the mapped v4, not the v6 wrapper.
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if addr.is_link_local:
            return False, "base URL host is not allowed"
    return True, None
