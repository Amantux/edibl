"""SSRF guard for a user-supplied LLM base URL / Ollama host.

The provider base URL is operator-configurable and arrives from several places —
the Settings API, the add-on options, and the environment. Only the API path used
to validate it, so a value set via env/add-on options reached httpx unchecked.
This module is the single implementation both the save path (api/assistant.py)
and the use path (services/assistant.py::_fetch_models) call.

Blocks non-public addresses — link-local (notably the cloud metadata endpoint
169.254.169.254 / fe80::), loopback, private LAN, reserved, and multicast — while
still allowing globally-routable hosts, which is what a hosted endpoint uses.
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

    Fails closed: a host that cannot be resolved (or is malformed) is refused
    rather than allowed through, so an unresolvable endpoint never reaches a
    real request.
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
        # Unresolvable / malformed host: refuse (fail closed).
        return False, "base URL host could not be resolved"
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) connects to the real
        # IPv4 on a dual-stack host, so test the mapped v4, not the v6 wrapper.
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            # Non-public address: private LAN, loopback, link-local (the cloud
            # metadata endpoint), reserved, or multicast — never an outbound target.
            return False, "base URL host is not allowed"
    return True, None


# A redirect chain longer than this is treated as abusive rather than followed
# hop by hop (the ceiling a normally-following client would impose).
MAX_REDIRECTS = 10


def guarded_client(base_url=None, headers=None, timeout=30.0):
    """The one outbound HTTP client for Edibl's owner-configured LLM / sibling
    endpoints.

    Built with ``follow_redirects=False`` so a redirect answer is surfaced — httpx
    then records the intended follow-up request on the response
    (``response.next_request``) instead of chasing it blindly. The follow-up hop is
    re-validated through :func:`llm_url_ok` by :func:`guarded_send` before it is sent;
    an unresolvable or rebound-to-blocked host aborts the request (fail closed).
    """
    import httpx
    return httpx.Client(base_url=base_url, headers=headers, timeout=timeout,
                       follow_redirects=False)


def guarded_redirect_target(response):
    """The follow-up request httpx prepared for a redirect answer, or ``None``.

    Only meaningful for a client built with ``follow_redirects=False``: httpx stops at
    the first redirect answer and attaches the intended next request to it instead of
    following. ``None`` for a non-redirect answer (or a response that carries no
    follow-up, e.g. a stub in tests).

    The follow-up target is re-validated through :func:`llm_url_ok` before it is
    returned, so a rebound-to-blocked host raises here instead of being followed —
    the egress fails closed rather than chasing blindly.
    """
    next_req = getattr(response, "next_request", None)
    if next_req is None:
        return None
    target = str(next_req.url)
    ok, err = llm_url_ok(target)
    if not ok:
        raise ValueError(f"refusing to follow redirect to {target}: {err}")
    return next_req


def guarded_send(client, method, url, headers=None, json=None, params=None):
    """Send one outbound LLM/sibling request, following redirects hop by hop.

    The client must come from :func:`guarded_client` (``follow_redirects=False``).
    Before ANY follow-up is sent, the redirect target is re-validated through
    :func:`llm_url_ok` — an unresolvable or rebound-to-blocked host raises, so the
    request fails closed instead of being followed.
    """
    if method == "get":
        response = client.get(url, headers=headers, params=params)
    elif method == "post":
        response = client.post(url, headers=headers, json=json)
    else:
        response = client.request(method, url, json=json, headers=headers)

    followed = 0
    while True:
        next_req = guarded_redirect_target(response)
        if next_req is None:
            return response
        if followed >= MAX_REDIRECTS:
            raise ValueError("exceeded the maximum number of redirects")
        followed += 1
        response = client.send(next_req)
