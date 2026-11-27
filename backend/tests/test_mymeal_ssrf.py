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


def test_guard_reject_emits_a_structured_metric(monkeypatch, caplog):
    """The myMeal use-path guard must leave the same structured trace as the LLM
    base-URL guard: one metric sample + one `metric ...` log line, and still refuse."""
    import logging as _logging

    from app.services import integrations as integ
    from app.services import metrics as metrics_svc
    from app.services import url_guard

    monkeypatch.setattr(url_guard, "llm_url_ok", lambda url: (False, "unresolvable host"))

    with caplog.at_level(_logging.INFO, logger="edibl.metrics"):
        blocked = integ._url_blocked("http://this-host-does-not-exist.invalid")

    assert blocked is True
    assert any("metric" in r.getMessage() and "url_guard" in r.getMessage()
               and "ok=False" in r.getMessage() for r in caplog.records)
    samples = metrics_svc.recent("url_guard")
    assert samples, "a guard reject must be recorded in the metrics ring"
    assert samples[-1]["kind"] == "url_guard"
    assert samples[-1]["reason"] == "unresolvable host"


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

