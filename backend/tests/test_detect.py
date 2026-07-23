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
