"""Confidence review queue — AI/vision detections staged for confirm/dismiss (ADR-0004)."""


def test_detect_stages_and_flags_duplicates(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Whole milk", "quantity": 1})
    r = auth_client.post("/api/v1/stock/detect", json={"source": "vision", "items": [
        {"name": "Whole milk", "quantity": 2, "confidence": 0.9},
        {"name": "Kiwi", "confidence": 0.4},
    ]}).get_json()
    assert r["staged"] == 2
    dets = auth_client.get("/api/v1/stock/detections").get_json()["detections"]
    milk = next(d for d in dets if d["name"] == "Whole milk")
    assert milk["matchedProductName"] == "Whole milk"   # deduped — flagged as already have
    assert next(d for d in dets if d["name"] == "Kiwi")["matchedProductName"] is None


def test_confirm_detection_creates_stock_and_resolves(auth_client):
    r = auth_client.post("/api/v1/stock/detect",
                         json={"items": [{"name": "Eggs", "quantity": 12, "unit": "count"}]}).get_json()
    did = r["detections"][0]["id"]
    lot = auth_client.post(f"/api/v1/stock/detections/{did}/confirm").get_json()
    assert lot["product"]["name"] == "Eggs" and lot["quantity"] == 12.0
    assert auth_client.get("/api/v1/stock/detections").get_json()["total"] == 0  # left pending


def test_confirm_twice_is_conflict(auth_client):
    r = auth_client.post("/api/v1/stock/detect", json={"items": [{"name": "Eggs", "quantity": 1}]}).get_json()
    did = r["detections"][0]["id"]
    auth_client.post(f"/api/v1/stock/detections/{did}/confirm")
    assert auth_client.post(f"/api/v1/stock/detections/{did}/confirm").status_code == 409


def test_dismiss_detection(auth_client):
    r = auth_client.post("/api/v1/stock/detect", json={"items": [{"name": "Junk"}]}).get_json()
    did = r["detections"][0]["id"]
    auth_client.post(f"/api/v1/stock/detections/{did}/dismiss")
    assert auth_client.get("/api/v1/stock/detections").get_json()["total"] == 0


def test_detections_are_household_scoped(auth_client, client):
    r = auth_client.post("/api/v1/stock/detect", json={"items": [{"name": "Secret"}]}).get_json()
    did = r["detections"][0]["id"]
    client.post("/api/v1/users/register", json={"email": "b@b.com", "password": "password", "name": "B"})
    tok = client.post("/api/v1/users/login",
                      json={"email": "b@b.com", "password": "password"}).get_json()["token"]
    assert client.post(f"/api/v1/stock/detections/{did}/confirm",
                       headers={"Authorization": tok}).status_code == 404


def test_bulk_confirm_signs_off_many_at_once(auth_client):
    r = auth_client.post("/api/v1/stock/detect", json={"items": [
        {"name": "Eggs", "quantity": 12}, {"name": "Butter", "quantity": 2},
        {"name": "Bread", "quantity": 1},
    ]}).get_json()
    ids = [d["id"] for d in r["detections"]]

    # Sign off two of the three, with a quantity override on one.
    res = auth_client.post("/api/v1/stock/detections/confirm", json={
        "ids": ids[:2], "overrides": {ids[0]: {"quantity": 18}},
    }).get_json()

    assert res["added"] == 2 and set(res["addedIds"]) == set(ids[:2])
    # The two are ingested and resolved; the third stays pending.
    assert auth_client.get("/api/v1/stock/detections").get_json()["total"] == 1
    eggs = next(s for s in auth_client.get("/api/v1/stock").get_json()["items"]
                if s["product"]["name"] == "Eggs")
    assert eggs["quantity"] == 18.0   # override applied


def test_bulk_confirm_skips_bad_ids(auth_client, client):
    r = auth_client.post("/api/v1/stock/detect",
                         json={"items": [{"name": "Eggs"}]}).get_json()
    ok_id = r["detections"][0]["id"]
    res = auth_client.post("/api/v1/stock/detections/confirm",
                           json={"ids": [ok_id, "does-not-exist"]}).get_json()
    assert res["added"] == 1 and res["skipped"] == 1


def test_bulk_confirm_bad_override_leaves_no_phantom_product(auth_client):
    """A skipped bulk item (bad locationId override) must not persist a phantom
    Product — _resolve_product flushes one before the location check fails, so the
    per-item savepoint has to roll it back."""
    r = auth_client.post("/api/v1/stock/detect",
                         json={"items": [{"name": "PhantomThing"}]}).get_json()
    did = r["detections"][0]["id"]

    res = auth_client.post("/api/v1/stock/detections/confirm", json={
        "ids": [did], "overrides": {did: {"locationId": "does-not-exist"}},
    }).get_json()

    assert res["added"] == 0 and res["skipped"] == 1
    names = [p["name"] for p in auth_client.get("/api/v1/products").get_json()]
    assert "PhantomThing" not in names  # savepoint rolled the flushed product back
