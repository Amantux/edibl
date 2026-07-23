"""Multi-fact expiry: keep the raw date facts, derive one effective forecast, and
explain how it was derived (so an estimate never masquerades as a printed date)."""


def test_best_by_sets_basis_and_explanation(auth_client):
    r = auth_client.post("/api/v1/stock", json={"name": "Milk", "bestBy": "2026-08-12"}).get_json()
    assert r["expiryBasis"] == "best_by"
    assert r["bestBy"][:10] == "2026-08-12"
    assert r["expiryEstimated"] is False
    assert r["expiryExplain"].startswith("Best by")


def test_use_by_wins_over_best_by(auth_client):
    r = auth_client.post("/api/v1/stock", json={
        "name": "Yogurt", "bestBy": "2026-08-20", "useBy": "2026-08-12"}).get_json()
    assert r["expiryBasis"] == "use_by"
    assert r["expiryDate"][:10] == "2026-08-12"   # the hard date drives the forecast
    assert r["expiryExplain"].startswith("Use by")


def test_estimated_is_labelled_as_an_estimate(auth_client):
    r = auth_client.post("/api/v1/stock", json={"name": "Spinach", "category": "produce"}).get_json()
    assert r["expiryBasis"] == "estimated" and r["expiryEstimated"] is True
    assert r["expiryExplain"].startswith("Est.")
    assert 0 < r["expiryConfidence"] <= 1


def test_freeze_records_frozen_basis(auth_client):
    lot = auth_client.post("/api/v1/stock", json={"name": "Bread", "storageMethod": "refrigerated"}).get_json()
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/freeze").get_json()
    assert r["expiryBasis"] == "frozen"
    assert "Frozen" in r["expiryExplain"]


def test_editing_use_by_updates_basis(auth_client):
    lot = auth_client.post("/api/v1/stock", json={"name": "Cheese"}).get_json()
    r = auth_client.put(f"/api/v1/stock/{lot['id']}", json={"useBy": "2026-09-01"}).get_json()
    assert r["expiryBasis"] == "use_by" and r["expiryDate"][:10] == "2026-09-01"
