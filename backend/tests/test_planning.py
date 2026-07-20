"""myMeal integration: propagate planned ingredients, reconcile vs inventory,
predict shortfall, and turn it into an order."""


def _stock(auth_client, name, qty, unit="count"):
    return auth_client.post("/api/v1/stock",
                            json={"productName": name, "quantity": qty, "unit": unit})


def test_ingest_plan_and_reconcile_against_inventory(auth_client):
    # On hand: 2 eggs, 1 L milk. Planned (from myMeal): 6 eggs, 1 L milk, 200 g flour.
    _stock(auth_client, "Eggs", 2)
    _stock(auth_client, "Whole milk", 1, "l")
    r = auth_client.post("/api/v1/integrations/mymeal/plan", json={
        "meal": "Pancakes", "sourceRef": "recipe-42",
        "items": [
            {"name": "Eggs", "quantity": 6},
            {"name": "Whole milk", "quantity": 1, "unit": "l"},
            {"name": "Flour", "quantity": 200, "unit": "g"},
        ]})
    assert r.status_code == 201 and r.get_json()["upserted"] == 3

    plan = auth_client.get("/api/v1/plan").get_json()
    assert plan["canMakeAll"] is False
    byname = {i["name"]: i for i in plan["items"]}
    assert byname["Whole milk"]["have"] is True          # 1 >= 1
    assert byname["Eggs"]["shortfall"] == 4              # need 6, have 2
    assert byname["Flour"]["shortfall"] == 200           # have none
    # shortfall list = what to order
    short = {s["name"] for s in plan["shortfall"]}
    assert short == {"Eggs", "Flour"}


def test_plan_reingest_upserts_not_duplicates(auth_client):
    body = {"sourceRef": "recipe-1", "items": [{"name": "Butter", "quantity": 1}]}
    auth_client.post("/api/v1/integrations/mymeal/plan", json=body)
    auth_client.post("/api/v1/integrations/mymeal/plan",
                     json={"sourceRef": "recipe-1",
                           "items": [{"name": "Butter", "quantity": 3}]})
    plan = auth_client.get("/api/v1/plan").get_json()
    butter = [p for p in plan["planned"] if p["name"] == "Butter"]
    assert len(butter) == 1 and butter[0]["quantity"] == 3


def test_plan_check_is_stateless(auth_client):
    _stock(auth_client, "Rice", 500, "g")
    r = auth_client.post("/api/v1/plan/check", json={
        "ingredients": [{"name": "Rice", "quantity": 200, "unit": "g"},
                        {"name": "Beans", "quantity": 1}]}).get_json()
    assert r["canMakeAll"] is False
    assert {s["name"] for s in r["shortfall"]} == {"Beans"}
    # nothing persisted
    assert auth_client.get("/api/v1/plan").get_json()["planned"] == []


def test_plan_order_adds_shortfall_to_shopping_list(auth_client):
    auth_client.post("/api/v1/integrations/mymeal/plan", json={
        "items": [{"name": "Onions", "quantity": 3}, {"name": "Garlic", "quantity": 1}]})
    r = auth_client.post("/api/v1/plan/order").get_json()
    assert r["added"] == 2
    names = {i["name"] for i in auth_client.get("/api/v1/shopping").get_json()}
    assert {"Onions", "Garlic"} <= names


def test_integration_status_reports_unconfigured(auth_client):
    st = auth_client.get("/api/v1/integrations/status").get_json()
    assert st["myMeal"]["configured"] is False
    assert st["homeHoard"]["configured"] is False
