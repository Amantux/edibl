"""Food classification for the add form. With no LLM configured (test default),
the endpoint falls back to the keyword heuristic — deterministic + always works."""


def test_classify_maps_a_dairy_item(auth_client):
    r = auth_client.post("/api/v1/stock/classify", json={"name": "Organic whole milk"}).get_json()
    assert r["category"] == "dairy"
    assert r["storageMethod"] == "refrigerated"
    assert r["unit"] == "carton"
    assert r["itemType"] == "food"
    assert r["source"] == "heuristic"      # no LLM in tests


def test_classify_flags_non_food_consumable(auth_client):
    r = auth_client.post("/api/v1/stock/classify", json={"name": "Aluminum foil"}).get_json()
    assert r["itemType"] == "consumable"   # excluded from recipe/expiry logic


def test_classify_infers_storage_for_pantry_goods(auth_client):
    r = auth_client.post("/api/v1/stock/classify", json={"name": "All-purpose flour"}).get_json()
    assert r["category"] == "dry_goods" and r["storageMethod"] == "pantry"


def test_classify_unknown_item_has_safe_defaults(auth_client):
    r = auth_client.post("/api/v1/stock/classify", json={"name": "Zorblax"}).get_json()
    assert r["category"] == "other" and r["unit"] == "count" and r["itemType"] == "food"


def test_classify_requires_a_name(auth_client):
    assert auth_client.post("/api/v1/stock/classify", json={"name": ""}).status_code == 422


def test_classified_item_type_persists_on_add(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Cling wrap", "itemType": "consumable"})
    prods = auth_client.get("/api/v1/products").get_json()
    items = prods["items"] if isinstance(prods, dict) and "items" in prods else prods
    wrap = next(p for p in items if p["name"] == "Cling wrap")
    assert wrap["itemType"] == "consumable"
