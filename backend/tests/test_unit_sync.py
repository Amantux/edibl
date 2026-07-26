"""Two-way units-of-measure sync with a connected myMeal (mocked)."""


def _mock_mymeal(monkeypatch, units, pushed):
    from app.api import integrations as integ_api
    from app.services import integrations as integ_svc
    monkeypatch.setattr(integ_api, "mymeal_get",
                        lambda path, params=None: {"configured": True, "reachable": True,
                                                   "data": units})
    monkeypatch.setattr(integ_svc, "mymeal_post",
                        lambda path, payload=None: (pushed.append(payload) or
                                                    {"configured": True, "reachable": True, "data": {}}))


def test_sync_units_deterministic_union(app, auth_client, monkeypatch):
    auth_client.post("/api/v1/units", json={"name": "cup"})
    auth_client.post("/api/v1/units", json={"name": "count"})
    from app.services import assistant
    monkeypatch.setattr(assistant, "reconcile_units", lambda e, m: None)  # force union
    pushed = []
    _mock_mymeal(monkeypatch, [
        {"name": "each", "pluralName": "", "abbreviation": ""},   # ≈ count (Edibl has)
        {"name": "clove", "pluralName": "cloves", "abbreviation": ""},  # new → to Edibl
    ], pushed)

    r = auth_client.post("/api/v1/integrations/mymeal/sync-units").get_json()

    assert r["reconciler"] == "union" and r["addedToEdibl"] == 1
    assert "clove" in [u["name"] for u in auth_client.get("/api/v1/units").get_json()]
    pushed_names = [p["name"] for p in pushed]
    assert "cup" in pushed_names and "count" not in pushed_names  # count≈each already in myMeal


def test_sync_units_uses_llm_plan_when_available(app, auth_client, monkeypatch):
    from app.services import assistant
    monkeypatch.setattr(assistant, "reconcile_units", lambda e, m: {
        "toEdibl": [{"name": "pinch", "pluralName": "pinches", "abbreviation": ""}],
        "toMyMeal": [{"name": "bottle", "pluralName": "", "abbreviation": ""}],
    })
    pushed = []
    _mock_mymeal(monkeypatch, [], pushed)

    r = auth_client.post("/api/v1/integrations/mymeal/sync-units").get_json()

    assert r["reconciler"] == "llm" and r["addedToEdibl"] == 1 and r["pushedToMyMeal"] == 1
    assert "pinch" in [u["name"] for u in auth_client.get("/api/v1/units").get_json()]
    assert pushed[0]["name"] == "bottle"


def test_sync_units_requires_connection(auth_client, monkeypatch):
    from app.api import integrations as integ_api
    monkeypatch.setattr(integ_api, "mymeal_get",
                        lambda path, params=None: {"configured": False, "reachable": False})
    assert auth_client.post("/api/v1/integrations/mymeal/sync-units").status_code == 409


def test_sync_survives_malformed_llm_plan(app, auth_client, monkeypatch):
    """A model that returns bare strings instead of {name} objects must not 500."""
    from app.services import assistant
    monkeypatch.setattr(assistant, "reconcile_units",
                        lambda e, m: {"toEdibl": ["cup", "tsp"], "toMyMeal": "count"})
    _mock_mymeal(monkeypatch, [], [])
    r = auth_client.post("/api/v1/integrations/mymeal/sync-units")
    assert r.status_code == 200 and r.get_json()["addedToEdibl"] == 0  # junk skipped


def test_sync_survives_garbage_mymeal_units(app, auth_client, monkeypatch):
    """A /units response that isn't a list of objects must not 500."""
    from app.api import integrations as integ_api
    from app.services import assistant
    monkeypatch.setattr(assistant, "reconcile_units", lambda e, m: None)  # union path
    monkeypatch.setattr(integ_api, "mymeal_get",
                        lambda path, params=None: {"configured": True, "reachable": True,
                                                   "data": ["cup", "tsp"]})  # strings
    auth_client.post("/api/v1/units", json={"name": "count"})
    r = auth_client.post("/api/v1/integrations/mymeal/sync-units")
    assert r.status_code == 200  # garbage ignored, no crash
