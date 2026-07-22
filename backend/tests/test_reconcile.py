"""Phase-3 human inventory: location reconciliation as one reversible batch, and
contextual tracking-mode defaults."""


def _loc(c, name="Pantry"):
    return c.post("/api/v1/locations", json={"name": name, "kind": "pantry"}).get_json()


def _add(c, loc_id, name, quantity, **over):
    body = {"name": name, "quantity": quantity, "unit": "count", "locationId": loc_id}
    body.update(over)
    return c.post("/api/v1/stock", json=body).get_json()


def test_reconcile_applies_counts_missing_and_additions_in_one_batch(auth_client):
    loc = _loc(auth_client)
    a = _add(auth_client, loc["id"], "Beans", 2)
    b = _add(auth_client, loc["id"], "Lentils", 1)

    r = auth_client.post(f"/api/v1/locations/{loc['id']}/reconcile", json={
        "counts": [{"lotId": a["id"], "quantity": 1.6}],
        "missing": [b["id"]],
        "additions": [{"name": "Quinoa", "quantity": 3, "unit": "count"}],
    }).get_json()
    assert r["counted"] == 1 and r["removed"] == 1 and r["added"] == 1
    assert r["batchId"]

    assert auth_client.get(f"/api/v1/stock/{a['id']}").get_json()["quantity"] == 1.6
    assert auth_client.get(f"/api/v1/stock/{b['id']}").get_json()["finished"] is True
    names = [i["product"]["name"] for i in
             auth_client.get("/api/v1/stock").get_json()["items"]]
    assert "Quinoa" in names


def test_reconcile_reverses_as_one_operation(auth_client):
    loc = _loc(auth_client)
    a = _add(auth_client, loc["id"], "Beans", 2)
    b = _add(auth_client, loc["id"], "Lentils", 1)
    r = auth_client.post(f"/api/v1/locations/{loc['id']}/reconcile", json={
        "counts": [{"lotId": a["id"], "quantity": 1.6}],
        "missing": [b["id"]],
        "additions": [{"name": "Quinoa", "quantity": 3}],
    }).get_json()

    auth_client.post(f"/api/v1/inventory/reconciliations/{r['batchId']}/reverse")

    assert auth_client.get(f"/api/v1/stock/{a['id']}").get_json()["quantity"] == 2.0
    assert auth_client.get(f"/api/v1/stock/{b['id']}").get_json()["finished"] is False
    # the discovered item is archived (finished) on undo
    quinoa = next(i for i in auth_client.get("/api/v1/stock?includeFinished=true")
                  .get_json()["items"] if i["product"]["name"] == "Quinoa")
    assert quinoa["finished"] is True


def test_reconcile_unknown_location_is_404(auth_client):
    assert auth_client.post("/api/v1/locations/nope/reconcile",
                            json={"counts": []}).status_code == 404


def test_tracking_mode_defaults_by_category(auth_client):
    condiment = auth_client.post("/api/v1/stock",
                                 json={"name": "Ketchup", "category": "condiment"}).get_json()
    assert condiment["product"]  # created
    prods = auth_client.get("/api/v1/products").get_json()
    ketchup = next(p for p in prods["items"] if p["name"] == "Ketchup") \
        if isinstance(prods, dict) and "items" in prods else \
        next(p for p in prods if p["name"] == "Ketchup")
    assert ketchup["effectiveTrackingMode"] == "level"


def test_explicit_tracking_mode_overrides_default(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Ground beef", "category": "meat"})
    prods = auth_client.get("/api/v1/products").get_json()
    items = prods["items"] if isinstance(prods, dict) and "items" in prods else prods
    beef = next(p for p in items if p["name"] == "Ground beef")
    assert beef["effectiveTrackingMode"] == "measure"  # meat default

    updated = auth_client.put(f"/api/v1/products/{beef['id']}",
                              json={"trackingMode": "portions"}).get_json()
    assert updated["trackingMode"] == "portions"
    assert updated["effectiveTrackingMode"] == "portions"
