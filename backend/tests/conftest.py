import pytest

from app import create_app
from app.config import Config
from app.extensions import db


@pytest.fixture()
def app(tmp_path):
    class TestConfig(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/test.db"
        DISABLE_AUTH = False
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        RATELIMIT_ENABLED = False
        PROXY_HOPS = 0
        SEED_DEFAULTS = False   # clean baseline; the seeding test opts in explicitly
        WORKER_ENABLED = False  # tests drive job functions directly, no poller thread

    app = create_app(TestConfig)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    client.post("/api/v1/users/register",
                json={"email": "t@t.com", "password": "password", "name": "T"})
    token = client.post("/api/v1/users/login",
                        json={"email": "t@t.com", "password": "password"}).get_json()["token"]
    client.environ_base["HTTP_AUTHORIZATION"] = token
    return client


@pytest.fixture()
def gid(app, auth_client):
    """The group the authenticated test client is acting as.

    Resolved through the USER, deliberately. The hand-rolled copies came in two
    flavours — `User(email=...).group_id` (13 files) and `Group.first().id`
    (2 files) — which agree only while a test has exactly one household. The
    moment one registers a second, `Group.first()` returns whichever row the DB
    hands back first, so a cross-tenant test can quietly assert against the
    wrong household. The user-scoped definition is the one that stays true."""
    from app.models import User
    with app.app_context():
        return db.session.query(User).filter_by(
            email="t@t.com").first().group_id


@pytest.fixture()
def second_client(app):
    """A SEPARATE client authenticated as another household.

    Note the trap this exists to avoid: `auth_client` mutates the shared `client`
    fixture's headers, so registering a second user through it swaps identity for
    the *first* user too — a cross-tenant test written the obvious way silently
    asserts the wrong thing (it did, until a failing IDOR test exposed it)."""
    other = app.test_client()
    other.post("/api/v1/users/register",
               json={"email": "b@b.com", "password": "password", "name": "B"})
    token = other.post("/api/v1/users/login",
                       json={"email": "b@b.com", "password": "password"}
                       ).get_json()["token"]
    other.environ_base["HTTP_AUTHORIZATION"] = token
    return other


@pytest.fixture()
def add_stock(auth_client):
    """Create a stock lot and return its serialized row.

    `add_stock("Milk", 2, unit="kg")` — the defaults match what most tests want;
    anything else passes through as JSON. Replaces a per-file `_stock`/`_add`
    helper that existed in ~18 variants with slightly different defaults."""
    def _add(name, quantity=5, unit="count", category="other", **kw):
        return auth_client.post("/api/v1/stock", json={
            "name": name, "quantity": quantity, "unit": unit,
            "category": category, **kw}).get_json()
    return _add


@pytest.fixture()
def add_location(auth_client):
    """Create a location (optionally nested) and return its serialized row."""
    def _add(name, parent=None, **kw):
        body = {"name": name, **kw}
        if parent:
            body["parentId"] = parent
        return auth_client.post("/api/v1/locations", json=body).get_json()
    return _add


@pytest.fixture()
def lot_quantity(auth_client):
    """Re-read a lot's current quantity — the usual 'did that mutate?' assertion."""
    def _q(lot_id):
        return auth_client.get(f"/api/v1/stock/{lot_id}").get_json()["quantity"]
    return _q
