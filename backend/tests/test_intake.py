"""Default-household seeding, server-side item autocomplete, and the freshness
scale exposed for the add form."""

from app import create_app
from app.config import Config


def _seeded_app(tmp_path):
    class C(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/seed.db"
        DISABLE_AUTH = False
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        RATELIMIT_ENABLED = False
        PROXY_HOPS = 0
        SEED_DEFAULTS = True
    return create_app(C)


def test_new_household_starts_with_kitchen_fridge_freezer(tmp_path):
    app = _seeded_app(tmp_path)
    c = app.test_client()
    c.post("/api/v1/users/register", json={"email": "a@a.com", "password": "password", "name": "A"})
    tok = c.post("/api/v1/users/login",
                 json={"email": "a@a.com", "password": "password"}).get_json()["token"]
    locs = c.get("/api/v1/locations", headers={"Authorization": tok}).get_json()
    names = {loc["name"] for loc in locs}
    assert {"Kitchen", "Fridge", "Freezer"} <= names
    fridge = next(loc for loc in locs if loc["name"] == "Fridge")
    assert fridge["parent"] and fridge["parent"]["name"] == "Kitchen"  # nested under Kitchen


def test_seeding_is_disabled_by_default_in_tests(auth_client):
    # conftest sets SEED_DEFAULTS=False, so a normal test household stays empty.
    assert auth_client.get("/api/v1/locations").get_json() == []


def test_autocomplete_returns_matching_product_names(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Whole milk", "quantity": 1})
    auth_client.post("/api/v1/stock", json={"name": "Almond milk", "quantity": 1})
    auth_client.post("/api/v1/stock", json={"name": "Bread", "quantity": 1})
    names = auth_client.get("/api/v1/products/autocomplete?q=milk").get_json()["names"]
    assert "Whole milk" in names and "Almond milk" in names and "Bread" not in names


def test_autocomplete_empty_query_returns_nothing(auth_client):
    assert auth_client.get("/api/v1/products/autocomplete?q=").get_json()["names"] == []


def test_autocomplete_is_household_scoped(auth_client, client):
    auth_client.post("/api/v1/stock", json={"name": "Saffron", "quantity": 1})
    client.post("/api/v1/users/register",
                json={"email": "b@b.com", "password": "password", "name": "B"})
    tok = client.post("/api/v1/users/login",
                      json={"email": "b@b.com", "password": "password"}).get_json()["token"]
    names = client.get("/api/v1/products/autocomplete?q=saffron",
                       headers={"Authorization": tok}).get_json()["names"]
    assert names == []  # another household can't see our products


def test_meta_exposes_the_freshness_scale(auth_client):
    scale = auth_client.get("/api/v1/meta").get_json()["freshnessScale"]
    assert len(scale) == 5
    assert scale[0]["level"] == 5 and scale[0]["key"] == "fresh"
    assert scale[-1]["level"] == 1 and scale[-1]["label"] == "Going off"
