"""Core Edibl flows: stock + expiry estimation, butchering, consumption,
shopping export, dashboards, and the ingredient query."""
from datetime import datetime, timezone, timedelta


def _iso_days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# --- health -----------------------------------------------------------------
def test_status_and_ready(client):
    assert client.get("/api/v1/status").get_json()["health"] is True
    r = client.get("/api/v1/ready")
    assert r.status_code == 200 and r.get_json()["ready"] is True


def test_auth_required(client):
    assert client.get("/api/v1/stock").status_code == 401


def test_register_rejects_short_password(client):
    r = client.post("/api/v1/users/register",
                    json={"email": "a@b.com", "password": "short"})
    assert r.status_code == 422


# --- locations (nested) -----------------------------------------------------
def test_nested_locations_and_cycle_guard(auth_client):
    home = auth_client.post("/api/v1/locations",
                            json={"name": "Home", "kind": "site"}).get_json()
    fridge = auth_client.post("/api/v1/locations",
                              json={"name": "Fridge", "kind": "fridge",
                                    "parentId": home["id"]}).get_json()
    assert fridge["parent"]["id"] == home["id"]
    # cycle: home under its own child fridge → 422
    r = auth_client.put(f"/api/v1/locations/{home['id']}",
                        json={"parentId": fridge["id"]})
    assert r.status_code == 422


# --- stock + auto expiry estimation ----------------------------------------
def test_add_stock_estimates_expiry_from_shelf_life(auth_client):
    # Fresh milk (dairy, refrigerated) bought today → ~10 day estimate.
    r = auth_client.post("/api/v1/stock", json={
        "productName": "Whole milk", "category": "dairy",
        "storageMethod": "refrigerated", "quantity": 1, "unit": "l"})
    assert r.status_code == 201
    lot = r.get_json()
    assert lot["expiryEstimated"] is True
    assert lot["expiryDate"] is not None
    assert lot["expiryStatus"] in ("fresh", "expiring")


def test_vacuum_sealed_meat_gets_long_expiry(auth_client):
    r = auth_client.post("/api/v1/stock", json={
        "productName": "Ribeye", "category": "meat",
        "storageMethod": "vacuum_sealed", "quantity": 2, "unit": "pack"})
    lot = r.get_json()
    # meat + vacuum_sealed default = 730 days → far in the future, fresh.
    assert lot["daysToExpiry"] > 300
    assert lot["expiryStatus"] == "fresh"


def test_explicit_expiry_not_marked_estimated(auth_client):
    r = auth_client.post("/api/v1/stock", json={
        "productName": "Yogurt", "category": "dairy",
        "expiryDate": _iso_days_ago(-2)})  # 2 days from now
    lot = r.get_json()
    assert lot["expiryEstimated"] is False
    assert lot["expiryStatus"] == "expiring"


# --- butchering workflow ----------------------------------------------------
def test_butcher_session_creates_vacuum_sealed_frozen_lots(auth_client):
    freezer = auth_client.post("/api/v1/locations",
                               json={"name": "Chest Freezer", "kind": "freezer"}).get_json()
    r = auth_client.post("/api/v1/stock/butcher", json={
        "source": "half a cow", "animal": "beef", "locationId": freezer["id"],
        "cuts": [
            {"cut": "Ribeye", "weightG": 4000, "quantity": 8},
            {"cut": "Ground beef", "weightG": 10000, "quantity": 10},
            {"cut": "Brisket", "weightG": 5000, "quantity": 2},
        ]})
    assert r.status_code == 201
    body = r.get_json()
    assert body["created"] == 3
    for lot in body["items"]:
        assert lot["storageMethod"] == "vacuum_sealed"
        assert lot["attrs"]["animal"] == "beef"
        assert lot["attrs"]["butcherSession"] == body["session"]
        assert lot["daysToExpiry"] > 300
    # freezer view surfaces them
    fz = auth_client.get("/api/v1/dashboard/freezer").get_json()
    assert fz["total"] == 3


# --- consumption + runout ---------------------------------------------------
def test_consume_records_event_and_finishes_lot(auth_client):
    lot = auth_client.post("/api/v1/stock",
                           json={"productName": "Eggs", "quantity": 12}).get_json()
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/consume", json={"quantity": 12})
    assert r.get_json()["finished"] is True
    # a fully-consumed product with 0 remaining is suggested for reorder
    s = auth_client.post("/api/v1/shopping/suggest").get_json()
    assert any(i["name"] == "Eggs" for i in s["items"])


# --- shopping list + delivery export ---------------------------------------
def test_shopping_export_is_paste_friendly(auth_client):
    auth_client.post("/api/v1/shopping", json={"name": "Whole milk", "quantity": 2})
    auth_client.post("/api/v1/shopping",
                     json={"name": "Ground beef", "quantity": 500, "unit": "g"})
    text = auth_client.get("/api/v1/shopping/export").get_data(as_text=True)
    assert "2x Whole milk" in text
    assert "500 g Ground beef" in text


# --- ingredient query (myMeal / MCP surface) --------------------------------
def test_have_ingredient_query(auth_client):
    auth_client.post("/api/v1/stock",
                     json={"productName": "Whole milk", "quantity": 2, "unit": "l"})
    r = auth_client.get("/api/v1/have?ingredient=milk").get_json()
    assert r["have"] is True and r["onHand"] == 2


# --- group isolation --------------------------------------------------------
def test_stock_is_group_isolated(app, auth_client):
    auth_client.post("/api/v1/stock", json={"productName": "Secret cheese"})
    other = app.test_client()
    other.post("/api/v1/users/register",
               json={"email": "o@t.com", "password": "password"})
    tok = other.post("/api/v1/users/login",
                     json={"email": "o@t.com", "password": "password"}).get_json()["token"]
    other.environ_base["HTTP_AUTHORIZATION"] = tok
    assert other.get("/api/v1/stock").get_json()["total"] == 0
