"""Home Assistant sensor feed + notification trigger."""


def _loc(c, name="Fridge"):
    return c.post("/api/v1/locations", json={"name": name, "kind": "fridge"}).get_json()


def test_ha_sensors_reports_kitchen_metrics(auth_client):
    loc = _loc(auth_client)
    m = auth_client.post("/api/v1/stock", json={"name": "Whole milk", "quantity": 2,
                                                "unit": "carton", "locationId": loc["id"]}).get_json()
    auth_client.post(f"/api/v1/stock/{m['id']}/open")
    auth_client.post("/api/v1/stock", json={"name": "Spinach", "quantity": 1, "unit": "bag",
                                            "expiryDate": "2020-01-01", "locationId": loc["id"]})

    s = auth_client.get("/api/v1/ha/sensors").get_json()
    assert s["items_in_stock"] == 2
    assert s["open_packages"] == 1
    assert s["expired"] >= 1
    assert s["expiring_soon"] >= 1
    assert any(e["name"] == "Spinach" for e in s["expiring"])
    assert "expiring" in s["attention"]


def test_ha_sensors_includes_restock(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Butter", "quantity": 1, "unit": "count"})
    prods = auth_client.get("/api/v1/products").get_json()
    items = prods["items"] if isinstance(prods, dict) and "items" in prods else prods
    bid = next(p["id"] for p in items if p["name"] == "Butter")
    auth_client.put(f"/api/v1/products/{bid}", json={"reorderThreshold": 3})
    s = auth_client.get("/api/v1/ha/sensors").get_json()
    assert s["to_restock"] >= 1
    assert any(r["name"] == "Butter" for r in s["restock"])


def test_ha_notify_no_supervisor_is_graceful(auth_client, monkeypatch):
    # Tests don't run under the Supervisor, so notify reports it can't send.
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    r = auth_client.post("/api/v1/ha/notify").get_json()
    assert r["sent"] is False and "reason" in r


def test_ha_sensors_requires_auth(client):
    assert client.get("/api/v1/ha/sensors").status_code == 401
