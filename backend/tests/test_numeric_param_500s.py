"""Plausible-but-wrong numeric input must 422 or clamp, never 500.

A hostile or merely buggy client sending ?limit=abc or {"quantity":"abc"} hit a
bare int()/float() and produced a 500. The consume/plan paths were hardened
earlier; these are the remaining ones (same defect class, found by probing every
int(/float( on request data).
"""


def _lot(c):
    return c.post("/api/v1/stock", json={"name": "Rice", "quantity": 5,
                                         "unit": "count",
                                         "category": "dry_goods"}).get_json()


def test_shopping_add_non_numeric_quantity(auth_client):
    r = auth_client.post("/api/v1/shopping", json={"name": "x", "quantity": "abc"})
    assert r.status_code < 500, f"got {r.status_code}"


def test_shopping_bulk_non_numeric_quantity(auth_client):
    r = auth_client.post("/api/v1/shopping/bulk",
                         json={"items": [{"name": "x", "quantity": "abc"}]})
    assert r.status_code < 500, f"got {r.status_code}"


def test_split_non_numeric_amount(auth_client):
    lot = _lot(auth_client)
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/split", json={"quantity": "abc"})
    assert r.status_code < 500, f"got {r.status_code}"
    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["quantity"] == 5, "split mutated the lot despite bad input"


def test_inventory_events_non_numeric_limit(auth_client):
    r = auth_client.get("/api/v1/inventory/events?limit=abc")
    assert r.status_code < 500, f"got {r.status_code}"


def test_product_autocomplete_non_numeric_limit(auth_client):
    r = auth_client.get("/api/v1/products/autocomplete?q=a&limit=abc")
    assert r.status_code < 500, f"got {r.status_code}"


def test_stock_insights_non_numeric_months(auth_client):
    r = auth_client.get("/api/v1/stock/insights?months=abc")
    assert r.status_code < 500, f"got {r.status_code}"
