"""'Made it' — deduct a meal's ingredients from stock (plan → inventory)."""


def test_cook_deducts_matched_ingredients(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Whole milk", "quantity": 3, "unit": "l"})
    r = auth_client.post("/api/v1/plan/cook",
                         json={"ingredients": [{"name": "milk", "quantity": 1}]}).get_json()
    c = r["cooked"][0]
    assert c["matched"] and c["consumed"] == 1.0 and c["shortfall"] == 0.0

    lots = auth_client.get("/api/v1/stock").get_json()["items"]
    milk = next(s for s in lots if s["product"]["name"] == "Whole milk")
    assert milk["quantity"] == 2.0     # 3 − 1 consumed


def test_cook_reports_shortfall(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Butter", "quantity": 1, "unit": "count"})
    r = auth_client.post("/api/v1/plan/cook",
                         json={"ingredients": [{"name": "Butter", "quantity": 3}]}).get_json()
    assert r["cooked"][0]["consumed"] == 1.0 and r["cooked"][0]["shortfall"] == 2.0


def test_cook_never_matches_non_food(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Foil wrap", "quantity": 2,
                                            "unit": "pack", "itemType": "consumable"})
    r = auth_client.post("/api/v1/plan/cook",
                         json={"ingredients": [{"name": "Foil wrap", "quantity": 1}]}).get_json()
    assert r["cooked"][0]["matched"] is False and r["cooked"][0]["consumed"] == 0.0


def test_cook_clears_satisfied_planned_items(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Eggs", "quantity": 12, "unit": "count"})
    auth_client.post("/api/v1/integrations/mymeal/plan",
                     json={"items": [{"name": "Eggs", "quantity": 2}]})
    r = auth_client.post("/api/v1/plan/cook", json={"clear": True}).get_json()
    assert r["cleared"] >= 1
    plan = auth_client.get("/api/v1/plan").get_json()
    assert not any(p["name"] == "Eggs" for p in plan["planned"])
