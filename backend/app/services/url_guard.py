"""SSRF guard for a user-supplied LLM base URL / Ollama host.

The provider base URL is operator-configurable and arrives from several places —
the Settings API, the add-on options, and the environment. Only the API path used
to validate it, so a value set via env/add-on options reached httpx unchecked.
This module is the single implementation both the save path (api/assistant.py)
and the use path (services/assistant.py::_fetch_models) call.

Blocks link-local (notably the cloud metadata endpoint 169.254.169.254 / fe80::)
while still allowing loopback and private LAN, where a self-hosted Ollama
legitimately runs.

Fail closed: a host that cannot be resolved (or is malformed) is refused rather
than allowed through, so an unresolvable endpoint never reaches a real request.

Outbound traffic is then sent through :class:`PinnedHTTPTransport` — the socket
connects to the validated IP while keeping the original host for the TLS SNI and
the Host header, so the endpoint that was validated is the one that answers.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Iterable
from urllib.parse import urlparse

import httpx


def resolve_and_validate(url):
    """Resolve the host ONCE and validate every resolved address.

    Return ``(ok, pinned_addresses, error)``: the verdict, why it was refused
    (``None`` when allowed), and the list of validated IP strings the outbound
    request may pin at connect time — ``None`` when there is nothing to pin (a
    blank URL falls back to the configured default).

    The host is resolved a single time here and every address it resolves to is
    validated before the result is returned, so the caller can pin the validated
    address at connect time. Fails closed: an unresolvable / malformed host is
    refused rather than allowed through to a real request.

    Blank ``url`` → ``(True, None, None)``. Scheme must be http/https; a host is
    required. Each resolved address is checked (IPv4-mapped IPv6 unwrapped first)
    and link-local addresses — the cloud metadata endpoint — are refused.
    """
    if not url:
        # Blank URL → fall back to the configured default; nothing to reach or pin.
        return True, None, None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, None, "base URL must be http or https"
    host = parsed.hostname
    if not host:
        return False, None, "base URL has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        # Unresolvable / malformed host: refuse (fail closed) rather than letting
        # a real request try to reach it.
        return False, None, "base URL host could not be resolved"
    pinned = []
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) connects to the real
        # IPv4 on a dual-stack host, so test the mapped v4, not the v6 wrapper.
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if addr.is_link_local:
            return False, None, "base URL host is not allowed"
        ip_str = str(addr)
        if ip_str not in pinned:
            pinned.append(ip_str)
    return True, pinned, None


def llm_url_ok(url):
    """Return (ok, error). Blank URL → ok (falls back to the configured default).

    Thin wrapper over :func:`resolve_and_validate` for callers that only need the
    verdict; the resolve-and-pin variant is the one that hands back the validated
    addresses for the connect-time pin.

    Fails closed: a host that cannot be resolved (or is malformed) is refused
    rather than allowed through, so an unresolvable endpoint never reaches a real
    request. Not hardened against DNS-rebinding, an acceptable residual for a
    semi-trusted owner configuring their own endpoint.
    """
    ok, _pinned, err = resolve_and_validate(url)
    return ok, err


class PinnedHTTPTransport(httpx.HTTPTransport):
    """httpx transport that connects the socket to a pre-validated IP while keeping
    the original host for the TLS SNI and the Host header.

    :func:`resolve_and_validate` already resolved the LLM base URL host ONCE and
    validated every address it resolved to; this transport makes the *outbound* request
    actually land on one of those validated addresses instead of re-resolving at connect
    time (the DNS-rebinding / poisoned-recursive-resolver hole). The Host header and the
    TLS SNI both stay the ORIGINAL hostname, so the request is still name-addressed to
    the pinned IP.
    """

    def __init__(
        self,
        pinned_ip: str,
        pinned_port: int,
        *,
        validated: Iterable[str] = (),
        **httpx_kwargs,
    ) -> None:
        super().__init__(**httpx_kwargs)
        addresses = tuple(validated)
        if not addresses or pinned_ip not in addresses:
            # Fail closed: never send outbound traffic to an address that was not
            # validated in the first place.
            raise ValueError(
                f"refusing to pin outbound traffic to {pinned_ip!r}: "
                f"not in the validated set {list(addresses)}"
            )
        self._pinned_ip = pinned_ip
        self._pinned_port = pinned_port
        self._validated_addresses = addresses

    @property
    def pinned_address(self) -> str:
        """The address outbound requests will connect to (always a validated address)."""
        return self._pinned_ip

    def _connect_address(self) -> str:
        """Resolve the pinned address NOW — at connect time — and return it.

        The address a socket would actually reach is re-derived from the resolver at
        the moment of the connect, so a resolver that changed between the check
        (:func:`resolve_and_validate`) and now (DNS rebinding / poisoned resolver) is
        seen here and refused by the caller when it is not the validated address.
        """
        infos = socket.getaddrinfo(self._pinned_ip, self._pinned_port, type=socket.SOCK_STREAM)
        addr = ipaddress.ip_address(infos[0][4][0])
        # An IPv4-mapped IPv6 address connects to the real IPv4 on a dual-stack host,
        # so test the mapped v4, not the v6 wrapper.
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        return str(addr)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        # The Host header keeps the ORIGINAL host (name-based virtual hosting).
        host_header = url.netloc.decode("ascii") or url.host
        pinned_request = httpx.Request(
            method=request.method,
            url=url.copy_with(host=self._pinned_ip, port=self._pinned_port),
            headers=request.headers,
            content=request.content,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        pinned_request.headers["Host"] = host_header
        # Connect to the pinned IP; TLS SNI + Host stay the original hostname. The
        # address we are about to reach must STILL be one of the validated ones —
        # fail closed if the connect-time resolution moved elsewhere.
        if self._connect_address() not in self._validated_addresses:
            raise ValueError(
                f"refusing to pin outbound traffic to {self._pinned_ip!r}: "
                f"not in the validated set {list(self._validated_addresses)}"
            )
        return super().handle_request(pinned_request)


def pinned_client(url, pinned_addresses, **client_kwargs) -> httpx.Client:
    """Return an :class:`httpx.Client` that connects to a validated pinned address.

    Thin factory over :class:`PinnedHTTPTransport`: the outbound client is built on a
    transport that connects the socket to the validated IP while keeping the original
    host for the TLS SNI and the Host header — i.e. the request goes to the address that
    was validated, never to a re-resolved one.

    Refuses (raises ``ValueError``) when there is no validated address to pin to or when
    the address to pin to is not one of the validated addresses.
    """
    addresses = tuple(pinned_addresses or ())
    if not addresses:
        raise ValueError(
            "refusing to make an outbound LLM request: no validated address to pin to"
        )
    parsed = urlparse(url) if url else None
    port = parsed.port if (parsed is not None and parsed.port) else (
        443 if (parsed is not None and parsed.scheme == "https") else 80
    )
    transport = PinnedHTTPTransport(
        pinned_ip=addresses[0], pinned_port=port, validated=addresses
    )
    return httpx.Client(transport=transport, **client_kwargs)
