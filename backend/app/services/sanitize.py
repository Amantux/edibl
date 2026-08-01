"""Credential-free summaries of upstream (LLM provider) failures.

Provider SDK/HTTP exceptions get surfaced to API clients — the assistant
endpoints put ``str(exc)`` straight into a JSON error or a chat reply — so the
raw text must never carry an API key, a URL with embedded userinfo, or a whole
response body. Keep the exception type plus a redacted, truncated message:
enough to tell "connection refused" from "401 unauthorized" without leaking the
secret that caused it.
"""
from __future__ import annotations

import re

_SECRETISH = re.compile(
    r"""(?ix)
    ( (?:sk|rk|pk|xai|gsk)[-_][A-Za-z0-9_\-]{8,}   # OpenAI/Anthropic/xAI/Groq keys
    | AIza[A-Za-z0-9_\-]{10,}                      # Google API keys
    | Bearer\s+[A-Za-z0-9._\-]{8,}                 # Authorization header values
    # name=value / "name": "value" — the optional quotes matter: a JSON body
    # renders as `"api_key": "…"`, which a bare name[=:] pattern never matches.
    # The optional `Bearer ` inside the VALUE matters: without it this arm
    # matches `Authorization: Bearer` and stops at the space, leaving the token.
    | (?:api[-_]?key|access[-_]?token|authorization|key|token)
      ["']?\s*[=:]\s*["']?(?:Bearer\s+)?[^"'\s,}&]+
    | [A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@   # scheme://user:pass@
    # Catch-all for high-entropy blobs (Ollama Cloud / bare hex or base64 keys)
    # that match no vendor prefix. Long enough not to eat ordinary words.
    | \b[A-Za-z0-9_\-]{40,}\b
    )""",
)
_MAX_UPSTREAM_DETAIL = 200


def safe_upstream_detail(exc, limit=_MAX_UPSTREAM_DETAIL):
    """Return ``"TypeName: redacted message"`` for an upstream failure."""
    detail = _SECRETISH.sub("[redacted]", str(exc) or "")
    detail = " ".join(detail.split())  # collapse multi-line response bodies
    if len(detail) > limit:
        detail = detail[:limit].rstrip() + "…"
    kind = type(exc).__name__
    return f"{kind}: {detail}" if detail else kind
