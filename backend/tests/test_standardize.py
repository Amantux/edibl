"""Write-time standardization: units canonicalized, generic families derived."""


def test_stock_write_normalizes_unit_and_derives_family(auth_client):
    lot = auth_client.post("/api/v1/stock", json={
        "name": "Wegmans Teriyaki Marinade", "quantity": 2, "unit": "Pieces",
    }).get_json()

    assert lot["unit"] == "count"                            # 'Pieces' → canonical
    assert lot["product"]["family"] == "Teriyaki Marinade"   # brand stripped to a group


def test_bulk_write_normalizes_units(auth_client):
    res = auth_client.post("/api/v1/stock/bulk", json={"items": [
        {"name": "Eggs", "quantity": 12, "unit": "each"},
        {"name": "Flour", "quantity": 2, "unit": "kilograms"},
    ]}).get_json()
    assert res["created"] == 2
    units = {s["product"]["name"]: s["unit"]
             for s in auth_client.get("/api/v1/stock").get_json()["items"]}
    assert units["Eggs"] == "count" and units["Flour"] == "kg"
