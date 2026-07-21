"""myMeal connection is configurable + remembered from the UI (like the LLM)."""


def test_mymeal_persist_and_masked(auth_client):
    g = auth_client.get("/api/v1/integrations/mymeal").get_json()
    assert g["source"] == "none" and g["url"] == "" and g["hasToken"] is False
    body = auth_client.put("/api/v1/integrations/mymeal",
                           json={"url": "http://mymeal:8000", "token": "sk-mm"}).get_json()
    assert body["url"] == "http://mymeal:8000" and body["source"] == "ui"
    g = auth_client.get("/api/v1/integrations/mymeal").get_json()
    assert g["hasToken"] is True and "sk-mm" not in str(g)
    # PUT without token keeps it
    auth_client.put("/api/v1/integrations/mymeal", json={"url": "http://mymeal:9000"})
    assert auth_client.get("/api/v1/integrations/mymeal").get_json()["hasToken"] is True


def test_mymeal_override_env(auth_client):
    auth_client.application.config["MYMEAL_URL"] = "http://env-mymeal:8000"
    assert auth_client.get("/api/v1/integrations/mymeal").get_json()["source"] == "addon"
    auth_client.put("/api/v1/integrations/mymeal", json={"url": "http://ui-mymeal:8000"})
    g = auth_client.get("/api/v1/integrations/mymeal").get_json()
    assert g["url"] == "http://ui-mymeal:8000" and g["source"] == "ui"
    assert auth_client.get("/api/v1/integrations/status").get_json()["myMeal"]["url"] == "http://ui-mymeal:8000"


def test_mymeal_test_when_unconfigured(auth_client):
    r = auth_client.post("/api/v1/integrations/mymeal/test").get_json()
    assert r["configured"] is False and r["reachable"] is False


import httpx  # noqa: E402


def test_mymeal_discover_returns_empty_when_nothing_reachable(auth_client, monkeypatch):
    from app.services import integrations as integ
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setattr(integ, "_mymeal_reachable", lambda host, port: False)
    r = auth_client.post("/api/v1/integrations/mymeal/discover").get_json()
    assert r["available"] is True and r["candidates"] == []


def test_mymeal_discover_via_fixed_fallback_without_supervisor(auth_client, monkeypatch):
    """The fix: with no Supervisor access to a sibling add-on, discovery still
    finds myMeal by probing the internal add-on hostname."""
    from app.services import integrations as integ
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setattr(integ, "_mymeal_reachable",
                        lambda host, port: (host, port) == ("mymeal", 7850))
    r = auth_client.post("/api/v1/integrations/mymeal/discover").get_json()
    assert r["available"] is True and len(r["candidates"]) == 1
    assert r["candidates"][0]["url"] == "http://mymeal:7850"


def test_mymeal_discover_finds_addon_via_supervisor(auth_client, monkeypatch):
    from app.services import integrations as integ
    monkeypatch.setenv("SUPERVISOR_TOKEN", "faketoken")

    class _R:
        def __init__(self, d):
            self._d = d
        def raise_for_status(self):
            pass
        def json(self):
            return self._d

    class _Cli:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, url, params=None):
            if url == "/addons":
                return _R({"data": {"addons": [
                    {"slug": "local_mymeal", "name": "myMeal", "state": "started"},
                    {"slug": "local_grafana", "name": "Grafana", "state": "started"}]}})
            if url == "/addons/local_mymeal/info":
                return _R({"data": {"hostname": "local-mymeal", "ingress_port": 8000}})
            return _R({"data": {}})

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _Cli())
    # Only the Supervisor-reported host answers the probe.
    monkeypatch.setattr(integ, "_mymeal_reachable",
                        lambda host, port: (host, port) == ("local-mymeal", 8000))
    r = auth_client.post("/api/v1/integrations/mymeal/discover").get_json()
    assert r["available"] is True and len(r["candidates"]) == 1
    c = r["candidates"][0]
    assert c["url"] == "http://local-mymeal:8000" and c["running"] is True
