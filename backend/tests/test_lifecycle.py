"""Flexible bulk-add, perishable lifecycle outcomes + personalized shelf-life
learning, barcode resolve, and the chat assistant (rules backend)."""
from datetime import datetime, timezone, timedelta


def _iso_days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _add(client, name, **kw):
    body = {"productName": name, **kw}
    return client.post("/api/v1/stock", json=body).get_json()


# --- generic bulk add -------------------------------------------------------
def test_bulk_add_shared_and_overrides(auth_client):
    r = auth_client.post("/api/v1/stock/bulk", json={
        "shared": {"storageMethod": "frozen", "category": "meat", "source": "farm"},
        "items": [
            {"name": "Ribeye", "quantity": 2, "unit": "pack"},
            {"name": "Ground beef", "quantity": 1, "unit": "kg",
             "storageMethod": "vacuum_sealed"},
        ],
    })
    assert r.status_code == 201
    j = r.get_json()
    assert j["created"] == 2
    by_name = {i["product"]["name"]: i for i in j["items"]}
    assert by_name["Ribeye"]["storageMethod"] == "frozen"          # from shared
    assert by_name["Ground beef"]["storageMethod"] == "vacuum_sealed"  # per-item override
    assert by_name["Ribeye"]["source"] == "farm"
    assert all(i["expiryDate"] for i in j["items"])               # auto-estimated


def test_bulk_add_requires_items(auth_client):
    assert auth_client.post("/api/v1/stock/bulk", json={"items": []}).status_code == 422


def test_bulk_add_caps_item_count(auth_client):
    items = [{"name": f"x{i}"} for i in range(501)]
    assert auth_client.post("/api/v1/stock/bulk", json={"items": items}).status_code == 422


# --- location ownership (cross-tenant guard) --------------------------------
def _other_group_location(app):
    """A location that belongs to a *different* group."""
    from app.extensions import db
    from app.models import Group, Location
    with app.app_context():
        g = Group(name="Other")
        db.session.add(g)
        db.session.flush()
        loc = Location(name="SECRET-FREEZER", kind="freezer", group_id=g.id)
        db.session.add(loc)
        db.session.commit()
        return loc.id


def test_create_rejects_foreign_location(app, auth_client):
    foreign = _other_group_location(app)
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Milk", "locationId": foreign})
    assert r.status_code == 422


def test_update_rejects_foreign_location(app, auth_client):
    foreign = _other_group_location(app)
    lot = _add(auth_client, "Milk")
    r = auth_client.put(f"/api/v1/stock/{lot['id']}", json={"locationId": foreign})
    assert r.status_code == 422


def test_bulk_rejects_foreign_location_per_item(app, auth_client):
    foreign = _other_group_location(app)
    r = auth_client.post("/api/v1/stock/bulk", json={
        "items": [{"name": "Milk", "locationId": foreign}]})
    assert r.status_code == 422


def test_butcher_still_produces_frozen_lots(auth_client):
    r = auth_client.post("/api/v1/stock/butcher", json={
        "animal": "beef", "cuts": [{"cut": "Brisket", "weightG": 2000, "quantity": 1}]})
    assert r.status_code == 201
    item = r.get_json()["items"][0]
    assert item["storageMethod"] == "vacuum_sealed"
    assert item["attrs"]["cut"] == "Brisket" and item["attrs"]["animal"] == "beef"
    assert "butcherSession" in item["attrs"]


# --- lifecycle state --------------------------------------------------------
def test_state_on_add_and_update(auth_client):
    lot = _add(auth_client, "Avocado", category="produce", state="unripe")
    assert lot["state"] == "unripe"
    upd = auth_client.put(f"/api/v1/stock/{lot['id']}", json={"state": "ripe"}).get_json()
    assert upd["state"] == "ripe"
    meta = auth_client.get("/api/v1/meta").get_json()
    assert "unripe" in meta["lifecycleStates"] and "spoiled" in meta["outcomes"]


# --- consume with outcome + personalized learning ---------------------------
def test_consume_records_outcome_and_insight(auth_client):
    lot = _add(auth_client, "Strawberries", category="produce",
               storageMethod="fresh", purchaseDate=_iso_days_ago(4))
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/consume",
                         json={"outcome": "spoiled"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["finished"] is True
    # a loss surfaces a suggestion payload (may be empty on first event)
    assert "insight" in body


def test_personalized_shelf_life_learns_from_losses(auth_client):
    # Two spoilage events at ~6 days kept teach a personalized shelf life.
    for _ in range(2):
        lot = _add(auth_client, "Kiwi", category="produce", storageMethod="fresh",
                   purchaseDate=_iso_days_ago(6))
        auth_client.post(f"/api/v1/stock/{lot['id']}/consume",
                         json={"outcome": "spoiled"})
    # A fresh Kiwi with no printed expiry should now estimate ~6 days (learned),
    # not the produce/fresh default of 4.
    fresh = _add(auth_client, "Kiwi", category="produce", storageMethod="fresh")
    assert fresh["expiryEstimated"] is True
    assert fresh["daysToExpiry"] >= 5


def test_product_insights_endpoint(auth_client):
    lot = _add(auth_client, "Spinach", category="produce", storageMethod="fresh",
               purchaseDate=_iso_days_ago(3))
    pid = lot["product"]["id"]
    auth_client.post(f"/api/v1/stock/{lot['id']}/consume", json={"outcome": "spoiled"})
    ins = auth_client.get(f"/api/v1/products/{pid}/insights").get_json()
    assert ins["wasted"] == 1 and ins["productName"] == "Spinach"


def test_dashboard_lifecycle_feed(auth_client):
    lot = _add(auth_client, "Lettuce", category="produce", storageMethod="fresh",
               purchaseDate=_iso_days_ago(2))
    auth_client.post(f"/api/v1/stock/{lot['id']}/consume", json={"outcome": "discarded"})
    feed = auth_client.get("/api/v1/dashboard/lifecycle").get_json()["items"]
    assert any(r["productName"] == "Lettuce" for r in feed)


def test_runout_forecast_survives_tz(auth_client):
    # >=2 eaten events + remaining stock previously crashed the dashboard with a
    # tz-aware/naive subtraction. It must return 200 and forecast the product.
    lot = _add(auth_client, "Coffee", category="beverage", quantity=10,
               purchaseDate=_iso_days_ago(10))
    auth_client.post(f"/api/v1/stock/{lot['id']}/consume",
                     json={"quantity": 3, "outcome": "eaten"})
    auth_client.post(f"/api/v1/stock/{lot['id']}/consume",
                     json={"quantity": 3, "outcome": "eaten"})
    r = auth_client.get("/api/v1/dashboard/runout")
    assert r.status_code == 200
    assert any(i["product"]["name"] == "Coffee" for i in r.get_json()["items"])


def test_eaten_outcome_is_not_waste(auth_client):
    lot = _add(auth_client, "Yogurt", category="dairy",
               purchaseDate=_iso_days_ago(2))
    auth_client.post(f"/api/v1/stock/{lot['id']}/consume", json={"outcome": "eaten"})
    feed = auth_client.get("/api/v1/dashboard/lifecycle").get_json()["items"]
    assert not any(r["productName"] == "Yogurt" for r in feed)


# --- barcode ----------------------------------------------------------------
def test_barcode_resolves_known_product(auth_client):
    _add(auth_client, "Cola", category="beverage", barcode="0123456789012")
    r = auth_client.get("/api/v1/products/barcode/0123456789012").get_json()
    assert r["found"] is True and r["product"]["name"] == "Cola"


def test_barcode_unknown(auth_client):
    r = auth_client.get("/api/v1/products/barcode/9999999999999").get_json()
    assert r["found"] is False


# --- assistant (requires an LLM provider) -----------------------------------
def test_assistant_config_needs_provider(auth_client):
    cfg = auth_client.get("/api/v1/assistant/config").get_json()
    assert cfg["enabled"] is False and cfg["provider"] == "none"
    assert cfg["setup"]  # setup guidance present


def test_assistant_chat_without_provider_returns_setup(auth_client):
    r = auth_client.post("/api/v1/assistant/chat", json={"message": "do I have milk?"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["enabled"] is False and body["provider"] == "none"
    assert body["actions"] == [] and "LLM" in body["reply"]


def test_assistant_requires_message(auth_client):
    assert auth_client.post("/api/v1/assistant/chat", json={}).status_code == 422
