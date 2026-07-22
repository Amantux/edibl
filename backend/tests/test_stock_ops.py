"""Phase-2 core daily operations on the shared command layer: adjust, move, split,
merge, and policy-based consume (FEFO + prefer-open, spilling across lots). Every
mutation is a ledger event and reversible; splits/merges conserve the total."""


def _add(c, name="Rice", quantity=1, unit="kg", **over):
    body = {"name": name, "quantity": quantity, "unit": unit, "category": "dry_goods"}
    body.update(over)
    return c.post("/api/v1/stock", json=body).get_json()


def _loc(c, name):
    return c.post("/api/v1/locations", json={"name": name, "kind": "pantry"}).get_json()


# --- adjust ---------------------------------------------------------------- #
def test_adjust_corrects_quantity_and_is_reversible(auth_client):
    lot = _add(auth_client, quantity=2)
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/adjust",
                         json={"quantity": 1.6, "quantityKind": "exact"}).get_json()
    assert r["quantity"] == 1.6 and r["quantityKind"] == "exact"

    auth_client.post(f"/api/v1/inventory/events/{r['eventId']}/reverse")
    back = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert back["quantity"] == 2.0


# --- move ------------------------------------------------------------------ #
def test_move_relocates_and_reverses(auth_client):
    a, b = _loc(auth_client, "Pantry"), _loc(auth_client, "Garage")
    lot = _add(auth_client, locationId=a["id"])
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/move",
                         json={"locationId": b["id"]}).get_json()
    assert r["location"]["id"] == b["id"]

    auth_client.post(f"/api/v1/inventory/events/{r['eventId']}/reverse")
    back = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert back["location"]["id"] == a["id"]


# --- split ----------------------------------------------------------------- #
def test_split_conserves_total_and_reverses(auth_client):
    lot = _add(auth_client, name="Chicken", quantity=5, unit="lb", category="meat")
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/split", json={"quantity": 2})
    assert r.status_code == 201
    j = r.get_json()
    assert j["source"]["quantity"] == 3.0 and j["new"]["quantity"] == 2.0

    auth_client.post(f"/api/v1/inventory/events/{j['eventId']}/reverse")
    src = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    new = auth_client.get(f"/api/v1/stock/{j['new']['id']}").get_json()
    assert src["quantity"] == 5.0 and new["finished"] is True


def test_split_rejects_amount_at_or_above_total(auth_client):
    lot = _add(auth_client, quantity=2)
    assert auth_client.post(f"/api/v1/stock/{lot['id']}/split",
                            json={"quantity": 2}).status_code == 422


# --- merge ----------------------------------------------------------------- #
def test_merge_same_product_conserves_and_reverses(auth_client):
    a = _add(auth_client, name="Flour", quantity=3, unit="kg")
    b = _add(auth_client, name="Flour", quantity=2, unit="kg")
    r = auth_client.post("/api/v1/stock/merge",
                         json={"srcId": a["id"], "dstId": b["id"]}).get_json()
    assert r["quantity"] == 5.0

    src = auth_client.get(f"/api/v1/stock/{a['id']}").get_json()
    assert src["finished"] is True

    auth_client.post(f"/api/v1/inventory/events/{r['eventId']}/reverse")
    assert auth_client.get(f"/api/v1/stock/{a['id']}").get_json()["quantity"] == 3.0
    assert auth_client.get(f"/api/v1/stock/{b['id']}").get_json()["quantity"] == 2.0


def test_merge_rejects_different_products(auth_client):
    a = _add(auth_client, name="Flour", quantity=3, unit="kg")
    b = _add(auth_client, name="Sugar", quantity=2, unit="kg")
    assert auth_client.post("/api/v1/stock/merge",
                            json={"srcId": a["id"], "dstId": b["id"]}).status_code == 422


# --- policy-based consume (selection + spill) ------------------------------ #
def test_consume_by_product_spills_across_lots(auth_client):
    _add(auth_client, name="Milk", quantity=1, unit="carton", category="dairy")
    _add(auth_client, name="Milk", quantity=3, unit="carton", category="dairy")
    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Milk", "quantity": 2}).get_json()
    assert r["consumed"] == 2.0 and r["shortfall"] == 0
    assert len(r["draws"]) >= 1


def test_consume_by_product_reports_shortfall(auth_client):
    _add(auth_client, name="Milk", quantity=1, unit="carton", category="dairy")
    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Milk", "quantity": 5}).get_json()
    assert r["consumed"] == 1.0 and r["shortfall"] == 4.0


def test_consume_prefers_the_opened_package(auth_client):
    sealed = _add(auth_client, name="Milk", quantity=1, unit="carton", category="dairy")
    opened = _add(auth_client, name="Milk", quantity=1, unit="carton", category="dairy")
    auth_client.post(f"/api/v1/stock/{opened['id']}/open")
    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Milk", "quantity": 1}).get_json()
    drawn = {d["lot"]["id"] for d in r["draws"]}
    assert opened["id"] in drawn and sealed["id"] not in drawn


def test_consume_by_unknown_product_is_404(auth_client):
    assert auth_client.post("/api/v1/stock/consume",
                            json={"name": "Nope", "quantity": 1}).status_code == 404
