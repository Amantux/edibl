"""Guard-time vs connect-time enforcement for the owner-supplied LLM base URL.

``llm_url_ok`` keeps a fail-open posture on an unresolvable host: it returns
``(True, None)`` — allowed — rather than refusing, so the real request can surface
a clean error instead of a 500. That verdict is deliberate at GUARD time, but it is
NOT an authorization of the outbound connection: the connect-time pinned transport
(:mod:`app.services.pinned_transport`) is the sole point of enforcement and fails
closed there (it raises on an unresolvable host), so the pinned connection is
refused rather than silently permitted.
"""
import pytest

from app.services.pinned_transport import pinned_client
from app.services.url_guard import llm_url_ok


_UNRESOLVABLE = "http://edibl-guard-unresolvable.invalid:11434"


def test_guard_returns_true_none_for_an_unresolvable_host():
    """The guard keeps failing open on an unresolvable host — it surfaces the host.

    (True, None) means "not refused at guard time"; it must not be read as a green
    light for the connection.
    """
    ok, err = llm_url_ok(_UNRESOLVABLE)
    assert ok is True
    assert err is None


def test_guard_true_none_on_unresolvable_host_does_not_authorize_the_pinned_connection():
    """The guard's (True, None) on an unresolvable host must NOT authorize a pinned
    connection — the connect-time transport refuses it (fail closed)."""
    ok, err = llm_url_ok(_UNRESOLVABLE)
    # Guard time keeps its fail-open behaviour …
    assert ok is True
    assert err is None
    # … but the connection is still refused by the transport that would actually
    # have been used (fail closed).
    with pytest.raises(ValueError):
        pinned_client(_UNRESOLVABLE, timeout=5)
