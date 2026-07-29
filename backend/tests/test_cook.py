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


# --------------------------------------------------------------------------- #
# "What can I cook right now" — Edibl proxies myMeal's ranking (client mocked).
# --------------------------------------------------------------------------- #
def _env(data):
    return {"configured": True, "reachable": True, "data": data}


def test_cook_suggestions_maps_payload(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.integrations.mymeal_suggest", lambda: _env({
        "suggestions": [{"recipeId": "r1", "name": "Pancakes", "haveCount": 3,
                         "totalCount": 4, "missingCount": 1, "coverage": 0.75,
                         "missing": [{"name": "Eggs"}]}], "ediblAvailable": True}))
    r = auth_client.get("/api/v1/cook/suggestions?mode=make").get_json()
    assert r["configured"] and r["reachable"] and r["mode"] == "make"
    assert r["suggestions"][0]["name"] == "Pancakes"
    assert r["suggestions"][0]["missing"][0]["name"] == "Eggs"


def test_cook_suggestions_graceful_when_unconfigured(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.integrations.mymeal_suggest",
                        lambda: {"configured": False, "reachable": False})
    r = auth_client.get("/api/v1/cook/suggestions?mode=make")
    assert r.status_code == 200                       # graceful, not a 500
    body = r.get_json()
    assert body["configured"] is False and body["suggestions"] == []


def test_useup_mode_calls_use_it_up(auth_client, monkeypatch):
    called = {}

    def fake_useup(days=None, limit=None):
        called["days"] = days
        return _env({"suggestions": [{"recipeId": "r9", "name": "Stir fry",
                     "soonestDaysLeft": 2, "uses": [{"name": "Pepper"}]}], "expiring": []})
    monkeypatch.setattr("app.services.integrations.mymeal_use_it_up", fake_useup)
    r = auth_client.get("/api/v1/cook/suggestions?mode=useup&days=3").get_json()
    assert called["days"] == 3 and r["suggestions"][0]["soonestDaysLeft"] == 2


def test_cook_recipe_deducts_stock(auth_client, monkeypatch):
    # Seed real stock, then "cook" a mocked myMeal recipe → stock must be deducted.
    auth_client.post("/api/v1/stock", json={"name": "Milk", "quantity": 2, "unit": "l"})
    recipe = {"name": "Milkshake", "ingredients": [
        {"food": {"name": "Milk"}, "unit": {"abbreviation": "l"}, "quantity": 1,
         "display": "1 l milk"}]}
    monkeypatch.setattr("app.services.integrations.mymeal_recipe", lambda rid: _env(recipe))
    res = auth_client.post("/api/v1/cook/recipe/abc").get_json()
    assert res["recipe"] == "Milkshake"
    c = res["cooked"][0]
    assert c["matched"] and c["consumed"] == 1.0 and c["shortfall"] == 0.0
    lots = auth_client.get("/api/v1/stock").get_json()["items"]
    milk = next(s for s in lots if s["product"]["name"] == "Milk")
    assert milk["quantity"] == 1.0                    # 2 l − 1 l cooked


def test_cook_recipe_unreachable_mymeal_502(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.integrations.mymeal_recipe",
                        lambda rid: {"configured": True, "reachable": False, "error": "down"})
    r = auth_client.post("/api/v1/cook/recipe/abc")
    assert r.status_code == 502 and r.get_json()["reachable"] is False


def test_shop_recipe_adds_missing_and_skips_dupe(auth_client, monkeypatch):
    # Cheese already on the list → must not be duplicated; Eggs (missing) gets added.
    auth_client.post("/api/v1/shopping", json={"name": "Cheese"})
    recipe = {"name": "Omelette", "ingredients": [
        {"food": {"name": "Eggs"}, "unit": {"abbreviation": "count"}, "quantity": 3,
         "display": "3 eggs"},
        {"food": {"name": "Cheese"}, "unit": {"abbreviation": "g"}, "quantity": 50,
         "display": "50 g cheese"}]}
    monkeypatch.setattr("app.services.integrations.mymeal_recipe", lambda rid: _env(recipe))
    res = auth_client.post("/api/v1/cook/recipe/xyz/shop").get_json()
    names = {i["name"].lower() for i in res["items"]}
    assert "eggs" in names and "cheese" not in names   # eggs added, cheese skipped
    listed = auth_client.get("/api/v1/shopping").get_json()
    rows = listed["items"] if isinstance(listed, dict) else listed
    assert sum(1 for r in rows if r["name"].lower() == "cheese") == 1   # not duplicated


def test_cook_recipe_aggregates_duplicate_ingredient(auth_client, monkeypatch):
    # A recipe listing Flour twice (dough + dusting) → one summed deduction, not two.
    auth_client.post("/api/v1/stock", json={"name": "Flour", "quantity": 500, "unit": "g"})
    recipe = {"name": "Bread", "ingredients": [
        {"food": {"name": "Flour"}, "unit": {"abbreviation": "g"}, "quantity": 300,
         "display": "300 g flour"},
        {"food": {"name": "Flour"}, "unit": {"abbreviation": "g"}, "quantity": 50,
         "display": "50 g for dusting"}]}
    monkeypatch.setattr("app.services.integrations.mymeal_recipe", lambda rid: _env(recipe))
    res = auth_client.post("/api/v1/cook/recipe/x").get_json()
    assert len(res["cooked"]) == 1                     # two Flour lines aggregated to one
    assert res["cooked"][0]["consumed"] == 350.0       # 300 + 50, deducted once
    flour = next(s for s in auth_client.get("/api/v1/stock").get_json()["items"]
                 if s["product"]["name"] == "Flour")
    assert flour["quantity"] == 150.0                  # 500 − 350


def test_shop_recipe_dedupes_duplicate_line_within_call(auth_client, monkeypatch):
    # Same missing food twice in one recipe → a single shopping row, not two.
    recipe = {"name": "Cake", "ingredients": [
        {"food": {"name": "Sugar"}, "unit": {"abbreviation": "g"}, "quantity": 200,
         "display": "200 g sugar"},
        {"food": {"name": "Sugar"}, "unit": {"abbreviation": "g"}, "quantity": 50,
         "display": "50 g sugar"}]}
    monkeypatch.setattr("app.services.integrations.mymeal_recipe", lambda rid: _env(recipe))
    res = auth_client.post("/api/v1/cook/recipe/x/shop").get_json()
    assert res["added"] == 1
    shopping = auth_client.get("/api/v1/shopping").get_json()
    items = shopping if isinstance(shopping, list) else shopping.get("items", [])
    rows = [i for i in items if i["name"].lower() == "sugar"]
    assert len(rows) == 1


def test_cook_recipe_idempotent_on_retry(auth_client, monkeypatch):
    # Same per-press token twice (a network retry) → stock deducted ONCE, not twice.
    auth_client.post("/api/v1/stock", json={"name": "Rice", "quantity": 5, "unit": "cup"})
    recipe = {"name": "Pilaf", "ingredients": [
        {"food": {"name": "Rice"}, "unit": {"abbreviation": "cup"}, "quantity": 2,
         "display": "2 cups rice"}]}
    monkeypatch.setattr("app.services.integrations.mymeal_recipe", lambda rid: _env(recipe))
    body = {"idempotencyKey": "press-1"}
    auth_client.post("/api/v1/cook/recipe/x", json=body)
    auth_client.post("/api/v1/cook/recipe/x", json=body)   # retry, same token → replay
    rice = next(s for s in auth_client.get("/api/v1/stock").get_json()["items"]
                if s["product"]["name"] == "Rice")
    assert rice["quantity"] == 3.0                     # 5 − 2 once (not 5 − 4)
