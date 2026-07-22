"""Phase-5 planning & intelligence: canonical FoodConcepts + aliases, safe matching,
item_type boundaries, reservations, and policy-driven reorder suggestions."""


def _add(c, name, quantity=1, **over):
    body = {"name": name, "quantity": quantity, "unit": "count", "category": "produce"}
    body.update(over)
    return c.post("/api/v1/stock", json=body).get_json()


def _product_id(c, name):
    prods = c.get("/api/v1/products").get_json()
    items = prods["items"] if isinstance(prods, dict) and "items" in prods else prods
    return next(p["id"] for p in items if p["name"] == name)


# --- concepts + matching --------------------------------------------------- #
def test_matching_finds_a_product_by_its_concept_alias(auth_client):
    _add(auth_client, "Green onion")
    pid = _product_id(auth_client, "Green onion")
    concept = auth_client.post("/api/v1/concepts", json={
        "canonicalName": "Green onion", "aliases": ["scallion", "spring onion"]}).get_json()
    auth_client.put(f"/api/v1/products/{pid}", json={"conceptId": concept["id"]})

    r = auth_client.get("/api/v1/stock/match?q=scallions").get_json()
    names = [c["product"]["name"] for c in r["candidates"]]
    assert "Green onion" in names
    top = r["candidates"][0]
    assert top["score"] >= 0.9 and "alias/concept" in top["reasons"]


def test_matching_ranks_exact_above_substring(auth_client):
    _add(auth_client, "Milk")
    _add(auth_client, "Milk chocolate")
    r = auth_client.get("/api/v1/stock/match?q=milk").get_json()
    assert r["candidates"][0]["product"]["name"] == "Milk"
    assert r["candidates"][0]["score"] == 1.0


def test_match_item_type_filter_excludes_non_food(auth_client):
    _add(auth_client, "Foil")
    pid = _product_id(auth_client, "Foil")
    auth_client.put(f"/api/v1/products/{pid}", json={"itemType": "consumable"})
    r = auth_client.get("/api/v1/stock/match?q=foil&itemType=food,beverage").get_json()
    assert r["candidates"] == []


def test_resolve_for_mutation_is_ambiguous_across_similar_products(app):
    from app.extensions import db
    from app.models import Group, Product
    from app.services import matching
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        db.session.add(Product(name="Almond milk", group_id=g.id))
        db.session.add(Product(name="Oat milk", group_id=g.id))
        db.session.commit()
        res = matching.resolve_for_mutation(g.id, "milk")
        assert res.ambiguous is True and res.product is None
        assert len(res.candidates) == 2


# --- item_type in recipe demand -------------------------------------------- #
def test_non_food_consumable_never_satisfies_recipe_demand(auth_client):
    _add(auth_client, "Dishwasher tablets", quantity=30)
    pid = _product_id(auth_client, "Dishwasher tablets")
    auth_client.put(f"/api/v1/products/{pid}", json={"itemType": "consumable"})
    r = auth_client.post("/api/v1/plan/check",
                         json={"ingredients": [{"name": "Dishwasher tablets", "quantity": 1}]})
    data = r.get_json()
    item = data["items"][0]
    assert item["onHand"] == 0 and item["have"] is False  # excluded from recipe match


# --- reservations + reorder ------------------------------------------------ #
def test_reorder_suggests_below_threshold_and_reservation_reduces_available(auth_client):
    _add(auth_client, "Butter", quantity=2)
    pid = _product_id(auth_client, "Butter")
    auth_client.put(f"/api/v1/products/{pid}",
                    json={"reorderThreshold": 3, "targetQuantity": 5})

    s = auth_client.get("/api/v1/shopping/reorder").get_json()
    butter = next(x for x in s["suggestions"] if x["productId"] == pid)
    assert butter["onHand"] == 2.0 and butter["available"] == 2.0
    assert butter["suggestedQuantity"] == 3.0  # target 5 - available 2

    # reserving stock lowers what's available, increasing the suggested buy
    auth_client.post("/api/v1/reservations",
                     json={"productId": pid, "quantity": 1, "meal": "Sunday roast"})
    s2 = auth_client.get("/api/v1/shopping/reorder").get_json()
    butter2 = next(x for x in s2["suggestions"] if x["productId"] == pid)
    assert butter2["reserved"] == 1.0 and butter2["available"] == 1.0


def test_reorder_respects_do_not_suggest(auth_client):
    _add(auth_client, "Truffles", quantity=0)
    pid = _product_id(auth_client, "Truffles")
    auth_client.put(f"/api/v1/products/{pid}",
                    json={"reorderThreshold": 5, "doNotSuggest": True})
    s = auth_client.get("/api/v1/shopping/reorder").get_json()
    assert all(x["productId"] != pid for x in s["suggestions"])


def test_reservation_crud_and_isolation(auth_client, client):
    _add(auth_client, "Eggs", quantity=12)
    pid = _product_id(auth_client, "Eggs")
    r = auth_client.post("/api/v1/reservations",
                         json={"productId": pid, "quantity": 6}).get_json()
    assert auth_client.get("/api/v1/reservations").get_json()["total"] == 1

    client.post("/api/v1/users/register",
                json={"email": "b@b.com", "password": "password", "name": "B"})
    tok = client.post("/api/v1/users/login",
                      json={"email": "b@b.com", "password": "password"}).get_json()["token"]
    assert client.delete(f"/api/v1/reservations/{r['id']}",
                         headers={"Authorization": tok}).status_code == 404


def test_migration_backfills_food_concepts_idempotently(app):
    from app import _backfill_food_concepts
    from app.extensions import db
    from app.models import Group, Product, FoodConcept
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        db.session.add(Product(name="Organic milk", family="Milk", group_id=g.id))
        db.session.add(Product(name="Filtered milk", family="Milk", group_id=g.id))
        db.session.commit()

        _backfill_food_concepts()
        # both products share ONE concept named after the family
        concepts = db.session.query(FoodConcept).all()
        assert len(concepts) == 1 and concepts[0].canonical_name == "Milk"

        _backfill_food_concepts()  # re-run
        assert db.session.query(FoodConcept).count() == 1
