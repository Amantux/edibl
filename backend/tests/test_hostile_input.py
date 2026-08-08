"""Hostile-input hardening for the endpoints touched this session.

The PUT /stock path was hardened, but its sibling consume-by-product and the
plan-ingest / cook paths took `quantity`/items straight into float()/`.get()`:
- {"quantity":"Infinity"} drained EVERY lot of a product (min(inf, avail)=avail);
- {"quantity":"abc"} / a list 500'd;
- a non-dict items entry 500'd on `.get`.
"""


def _stock(c, name="Rice", quantity=100, unit="count"):
    return c.post("/api/v1/stock", json={"name": name, "quantity": quantity,
                                         "unit": unit, "category": "dry_goods"}).get_json()


# ---- consume-by-product ----------------------------------------------------

def test_consume_infinity_does_not_drain_the_product(auth_client):
    lot = _stock(auth_client, quantity=100)
    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Rice", "quantity": "Infinity"})
    assert r.status_code == 422, f"accepted infinite quantity: {r.status_code}"
    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["quantity"] == 100, "product was drained by an infinite quantity"


def test_consume_non_numeric_quantity_is_422_not_500(auth_client):
    _stock(auth_client)
    for bad in ("abc", [1, 2], {"x": 1}):
        r = auth_client.post("/api/v1/stock/consume",
                             json={"name": "Rice", "quantity": bad})
        assert r.status_code == 422, f"{bad!r} gave {r.status_code}"


def test_consume_negative_quantity_is_refused(auth_client):
    _stock(auth_client)
    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Rice", "quantity": -5})
    assert r.status_code == 422


# ---- plan ingest / cook ----------------------------------------------------

def test_ingest_plan_bad_quantity_does_not_500(auth_client):
    r = auth_client.post("/api/v1/integrations/mymeal/plan",
                         json={"items": [{"name": "Flour", "quantity": "abc"}]})
    assert r.status_code in (200, 201), f"bad quantity 500'd: {r.status_code}"


def test_ingest_plan_infinite_quantity_not_persisted(auth_client):
    r = auth_client.post("/api/v1/integrations/mymeal/plan",
                         json={"items": [{"name": "Sugar", "quantity": "Infinity"}]})
    assert r.status_code in (200, 201)
    rows = auth_client.get("/api/v1/plan").get_json()["planned"]
    row = next(x for x in rows if x["name"] == "Sugar")
    import math
    assert math.isfinite(row["quantity"]), "infinite quantity persisted"


def test_ingest_plan_non_dict_items_do_not_500(auth_client):
    r = auth_client.post("/api/v1/integrations/mymeal/plan",
                         json={"items": ["flour", "sugar"]})
    assert r.status_code in (200, 201, 422), f"non-dict items 500'd: {r.status_code}"


def test_plan_cook_non_dict_ingredients_do_not_500(auth_client):
    r = auth_client.post("/api/v1/plan/cook", json={"ingredients": ["flour"]})
    assert r.status_code in (200, 201, 422), f"non-dict ingredients 500'd: {r.status_code}"
