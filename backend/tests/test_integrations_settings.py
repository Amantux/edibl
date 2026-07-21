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
