"""Pinned-IP httpx transport for outbound LLM / sibling traffic.

The base URL is validated (see :mod:`app.services.url_guard` — link-local refused,
loopback + private LAN allowed where a self-hosted Ollama legitimately runs) and
then PINNED: the transport resolves the configured host and connects to the
resolved address, so a later DNS change cannot move the request onto a different
target. The pin covers the TCP leg only.

TLS identity is deliberately NOT taken from the pin: the handshake — TLS SNI and
the certificate-chain hostname check — is performed against the ORIGINAL URL
hostname, never the bare pinned IP. A pinned connection therefore still proves
which host it is talking to, and fails closed: any certificate mismatch or
unresolvable host raises, so the connection is refused rather than silently
permitted.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

_DEFAULT_PORTS = {"http": 80, "https": 443}


def pin_base_url(url):
    """Resolve ``url``'s host and return ``(pinned_url, original_host)``.

    ``pinned_url`` is ``"<scheme>://<resolved-ip>:<port>"`` — the address this
    process connects to. ``original_host`` is the configured hostname, retained
    for the TLS SNI / certificate-chain hostname check.

    Fails closed: an unresolvable / malformed host (or a non-http(s) scheme with
    no host) raises rather than yielding an address we could not validate.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base URL must be http or https")
    host = parsed.hostname
    if not host:
        raise ValueError("base URL has no host")
    port = parsed.port or _DEFAULT_PORTS.get(parsed.scheme, 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"base URL host could not be resolved: {host}") from exc
    addr = ipaddress.ip_address(infos[0][4][0])
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return f"{parsed.scheme}://{addr}:{port}", host


class _PinnedHTTPTransport(httpx.BaseTransport):
    """Connect to the pinned address; TLS-verify against the original hostname."""

    def __init__(self, pinned_url, original_host, verify=True):
        self._inner = httpx.HTTPTransport(verify=verify)
        self._original_host = original_host

    def handle_request(self, request):
        # TLS SNI + certificate-chain hostname = the ORIGINAL host, never the pinned IP.
        request.extensions["sni_hostname"] = self._original_host
        return self._inner.handle_request(request)

    def close(self):
        self._inner.close()


def pinned_client(url, *, timeout=15.0, headers=None, verify=True):
    """httpx.Client that connects to the PINNED IP of ``url`` but TLS-verifies the
    ORIGINAL hostname. Fails closed (raises) on an unresolvable host."""
    pinned_url, original_host = pin_base_url(url)
    transport = _PinnedHTTPTransport(pinned_url=pinned_url, original_host=original_host, verify=verify)
    return httpx.Client(
        base_url=pinned_url,
        transport=transport,
        headers=headers or {},
        timeout=timeout,
        verify=verify,
    )
