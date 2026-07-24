"""Behind HA ingress each HA user gets a distinct identity (from X-Remote-User-*),
all sharing one household; first user = owner. The trust boundary is the whole
point: the headers are honored ONLY from the Supervisor peer (172.30.32.2), so a
forged header from a directly-published port must never impersonate."""
import pytest

from app import create_app
from app.config import Config
from app.extensions import db

INGRESS = {"REMOTE_ADDR": "172.30.32.2"}  # the Supervisor ingress proxy peer


@pytest.fixture()
def iapp(tmp_path):
    class C(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/ig.db"
        DISABLE_AUTH = True          # ingress mode
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        RATELIMIT_ENABLED = False
        PROXY_HOPS = 0

    app = create_app(C)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def ic(iapp):
    return iapp.test_client()


def _me(c, ha=None, name=None, ingress=True):
    headers = {}
    if ha:
        headers["X-Remote-User-Id"] = ha
    if name:
        headers["X-Remote-User-Display-Name"] = name
    kw = {"headers": headers}
    if ingress:
        kw["environ_overrides"] = INGRESS
    return c.get("/api/v1/me", **kw).get_json()


def test_ingress_provisions_distinct_users_first_is_owner(ic):
    alice = _me(ic, "alice", "Alice")
    assert alice["name"] == "Alice" and alice["isOwner"] is True and alice["ha"] is True
    bob = _me(ic, "bob", "Bob")
    assert bob["isOwner"] is False and bob["ha"] is True and bob["id"] != alice["id"]
    # Same HA id resolves the same person (idempotent), not a new user.
    assert _me(ic, "alice")["id"] == alice["id"]


def test_forged_header_from_non_ingress_peer_is_ignored(ic):
    # No trusted peer -> X-Remote-User-Id is NOT honored; falls back to the shared
    # local user, identical for any forged id.
    a = _me(ic, "attacker", "Mallory", ingress=False)
    assert a["ha"] is False and a["isOwner"] is True   # the shared default user
    b = _me(ic, "someone-else", "Eve", ingress=False)
    assert b["id"] == a["id"]                            # never per-header


def test_member_403s_on_owner_config_owner_200s(ic):
    _me(ic, "owner1", "Owner")   # first ingress user becomes owner
    member = ic.put("/api/v1/assistant/settings", json={"provider": "ollama"},
                    headers={"X-Remote-User-Id": "member1"}, environ_overrides=INGRESS)
    assert member.status_code == 403
    owner = ic.put("/api/v1/assistant/settings", json={"provider": "ollama"},
                   headers={"X-Remote-User-Id": "owner1"}, environ_overrides=INGRESS)
    assert owner.status_code == 200
    # Members keep full non-config use (e.g. reading stock).
    assert ic.get("/api/v1/stock", headers={"X-Remote-User-Id": "member1"},
                  environ_overrides=INGRESS).status_code == 200


def test_household_is_shared_across_ingress_users(ic):
    # An owner adds stock; a member sees it (one shared group, per-person identity).
    ic.post("/api/v1/stock", json={"productName": "Milk", "quantity": 2},
            headers={"X-Remote-User-Id": "owner1"}, environ_overrides=INGRESS)
    seen = ic.get("/api/v1/stock", headers={"X-Remote-User-Id": "member1"},
                  environ_overrides=INGRESS).get_json()
    assert any((i.get("product") or {}).get("name") == "Milk" for i in seen["items"])


def test_migration_backfills_ownerless_admin(app):
    """A pre-roles install (existing user, is_owner=0) must not be locked out:
    the earliest user of an owner-less household is promoted on startup."""
    from app import _run_data_backfills
    from app.models import User, Group
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        u = User(name="Alex", email="alex@x.com", password_hash="x",
                 is_owner=False, group_id=g.id)
        db.session.add(u)
        db.session.commit()
        _run_data_backfills()   # role promotion moved here in the Alembic refactor
        db.session.refresh(u)
        assert u.is_owner is True


def test_register_rejects_reserved_ha_email(client):
    r = client.post("/api/v1/users/register",
                    json={"email": "ha:alice", "password": "password"})
    assert r.status_code == 422


def test_stock_attribution_only_for_ha_users(ic):
    # HA user's lot is attributed…
    ic.post("/api/v1/stock", json={"productName": "Eggs", "quantity": 6},
            headers={"X-Remote-User-Id": "alice", "X-Remote-User-Display-Name": "Alice"},
            environ_overrides=INGRESS)
    ha_items = ic.get("/api/v1/stock", headers={"X-Remote-User-Id": "alice"},
                      environ_overrides=INGRESS).get_json()["items"]
    eggs = next(i for i in ha_items if (i.get("product") or {}).get("name") == "Eggs")
    assert eggs["addedBy"] == "Alice"
    # …a lot added by the shared local user (no HA identity) is NOT attributed.
    ic.post("/api/v1/stock", json={"productName": "Salt"})  # no ingress peer
    local_items = ic.get("/api/v1/stock").get_json()["items"]
    salt = next(i for i in local_items if (i.get("product") or {}).get("name") == "Salt")
    assert salt["addedBy"] is None
