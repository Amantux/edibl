"""Credential-free summaries of upstream (LLM provider) failures.

Provider SDK/HTTP exceptions get surfaced to API clients — the assistant
endpoints put ``str(exc)`` straight into a JSON error or a chat reply — so the
raw text must never carry an API key, a URL with embedded userinfo, or a whole
response body. Keep the exception type plus a redacted, truncated message:
enough to tell "connection refused" from "401 unauthorized" without leaking the
secret that caused it.
"""
from __future__ import annotations

# The credential pattern lives in app.logsafe: it is applied both here (at the
# raise site, for text that reaches an API client) and as a logging filter over
# every record we write. One definition, two consumers.
from ..logsafe import SECRETISH as _SECRETISH


_MAX_UPSTREAM_DETAIL = 200


def safe_upstream_detail(exc, limit=_MAX_UPSTREAM_DETAIL):
    """Return ``"TypeName: redacted message"`` for an upstream failure."""
    detail = _SECRETISH.sub("[redacted]", str(exc) or "")
    detail = " ".join(detail.split())  # collapse multi-line response bodies
    if len(detail) > limit:
        detail = detail[:limit].rstrip() + "…"
    kind = type(exc).__name__
    return f"{kind}: {detail}" if detail else kind
