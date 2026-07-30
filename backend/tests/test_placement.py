"""Smart default placement + per-area descriptions. Offline-first: history → area
descriptions → storage-method heuristic, all working with no LLM configured."""
import importlib.util
import os

from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.extensions import db
from app.models import Location, Product, StockLot, User
from app.services.placement import suggest_location


def _gid():
    return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _loc(gid, name, kind, description=""):
    loc = Location(name=name, kind=kind, description=description, group_id=gid)
    db.session.add(loc)
    db.session.flush()
    return loc


def test_history_wins(auth_client, app):
    with app.app_context():
        gid = _gid()
        fridge = _loc(gid, "Fridge", "fridge")
        _loc(gid, "Pantry", "pantry")
        p = Product(name="Milk", group_id=gid)
        db.session.add(p)
        db.session.flush()
        db.session.add(StockLot(product_id=p.id, group_id=gid, location_id=fridge.id,
                                quantity=1, unit="l", quantity_kind="exact"))
        db.session.commit()
        # Even though milk is refrigerated (→ fridge anyway), history is the reason.
        s = suggest_location(gid, "Milk", storage_method="refrigerated")
        assert s["locationId"] == fridge.id and s["reason"] == "with the others"


def test_new_frozen_item_goes_to_freezer(auth_client, app):
    with app.app_context():
        gid = _gid()
        _loc(gid, "Pantry", "pantry")
        fz = _loc(gid, "Deep Freeze", "freezer")
        db.session.commit()
        s = suggest_location(gid, "Ice cream", storage_method="frozen")
        assert s["locationId"] == fz.id


def test_description_keyword_match(auth_client, app):
    with app.app_context():
        gid = _gid()
        _loc(gid, "Fridge", "fridge")
        rack = _loc(gid, "Cellar", "wine_cellar", description="Wine, beer and cider")
        db.session.commit()
        s = suggest_location(gid, "Craft beer")   # no storage_method → description match
        assert s["locationId"] == rack.id


def test_offline_returns_a_sensible_area(auth_client, app):
    # No LLM in tests: an unknown item with no signal still lands somewhere real.
    with app.app_context():
        gid = _gid()
        pantry = _loc(gid, "Pantry", "pantry")
        db.session.commit()
        s = suggest_location(gid, "Mystery thing")
        assert s is not None and s["locationId"] == pantry.id


def test_group_scoped(auth_client, app):
    from app.models import Group
    with app.app_context():
        gid = _gid()
        mine = _loc(gid, "My Pantry", "pantry")
        # A SEPARATE household with its own freezer + a Milk lot living there.
        other = Group(name="Other household")
        db.session.add(other)
        db.session.flush()
        foreign = _loc(other.id, "Their Freezer", "freezer")
        fp = Product(name="Milk", group_id=other.id)
        db.session.add(fp)
        db.session.flush()
        db.session.add(StockLot(product_id=fp.id, group_id=other.id, location_id=foreign.id,
                                quantity=1, unit="l", quantity_kind="exact"))
        db.session.commit()
        # Suggesting for MY group must never see the other group's freezer or its history —
        # even for the same item name and a frozen method (I have no freezer → my pantry).
        s = suggest_location(gid, "Milk", storage_method="frozen")
        assert s["locationId"] == mine.id
        assert s["locationId"] != foreign.id


def test_classify_returns_suggested_location(auth_client, app):
    with app.app_context():
        gid = _gid()
        _loc(gid, "Freezer", "freezer")
        db.session.commit()
    r = auth_client.post("/api/v1/stock/classify", json={"name": "Frozen peas"})
    assert r.status_code == 200
    body = r.get_json()
    assert "suggestedLocation" in body  # present (may be null if no locations, but here set)
    assert body["suggestedLocation"] is None or "locationId" in body["suggestedLocation"]


def test_describe_endpoint_persists(auth_client, app):
    with app.app_context():
        gid = _gid()
        fz = _loc(gid, "Freezer", "freezer")
        db.session.commit()
        lid = fz.id
    r = auth_client.post(f"/api/v1/locations/{lid}/describe")
    assert r.status_code == 200 and r.get_json()["description"]
    with app.app_context():
        assert db.session.get(Location, lid).description  # persisted


def test_describe_area_chat_tool_and_undo(auth_client, app):
    from app.services.assistant import h_describe_area, apply_undo
    with app.app_context():
        gid = _gid()
        fz = _loc(gid, "Freezer", "freezer", description="old")
        db.session.commit()
        lid = fz.id
        msg, undo = h_describe_area(gid, "Freezer", description="Ice cream and frozen veg")
        assert "Ice cream" in msg
        assert db.session.get(Location, lid).description == "Ice cream and frozen veg"
        apply_undo(gid, undo)
        assert db.session.get(Location, lid).description == "old"   # reverted


def _load_mig(app):
    path = os.path.join(os.path.dirname(app.root_path),
                        "migrations", "versions", "0010_location_description.py")
    spec = importlib.util.spec_from_file_location("mig0010", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0010_idempotent(app):
    with app.app_context():
        mod = _load_mig(app)
        assert mod.down_revision == "0009_product_nutrition"
        ctx = MigrationContext.configure(db.session.connection())
        with Operations.context(ctx):
            mod.upgrade()  # column already present (metadata) → no-op, no raise
        assert "description" in {c["name"] for c in
                                 db.inspect(db.session.connection()).get_columns("locations")}


# ---- ingestion auto-placement + confidence (location_estimated) ---------------
def test_suggest_location_reports_confidence(auth_client, app):
    with app.app_context():
        gid = _gid()
        _loc(gid, "Deep Freeze", "freezer")
        db.session.commit()
        assert suggest_location(gid, "Ice cream", storage_method="frozen")["confidence"] == "high"
        # no usable signal + no matching kind → default fallback = low confidence
        assert suggest_location(gid, "Mystery gadget")["confidence"] == "low"


def test_ingestion_high_confidence_auto_places_not_flagged(auth_client, app):
    with app.app_context():
        gid = _gid()
        _loc(gid, "Deep Freeze", "freezer")
        db.session.commit()
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Ice cream", "quantity": 1,
                               "storageMethod": "frozen"}).get_json()
    assert r["location"]["name"] == "Deep Freeze"
    assert r["locationEstimated"] is False          # frozen→freezer is trusted


def test_ingestion_low_confidence_placed_but_flagged(auth_client, app):
    with app.app_context():
        gid = _gid()
        _loc(gid, "Pantry", "pantry")               # no fridge/freezer to match
        db.session.commit()
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Widget", "quantity": 1}).get_json()
    assert r["location"]["name"] == "Pantry"
    assert r["locationEstimated"] is True           # low-confidence guess → review


def test_explicit_location_is_never_estimated(auth_client, app):
    with app.app_context():
        gid = _gid()
        loc = _loc(gid, "Pantry", "pantry")
        db.session.commit()
        lid = loc.id
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Beans", "quantity": 1, "locationId": lid}).get_json()
    assert r["locationEstimated"] is False


def test_confirming_location_clears_estimated(auth_client, app):
    with app.app_context():
        gid = _gid()
        _loc(gid, "Pantry", "pantry")
        c = _loc(gid, "Cupboard", "cupboard")
        db.session.commit()
        cid = c.id
    r = auth_client.post("/api/v1/stock", json={"productName": "Widget", "quantity": 1}).get_json()
    assert r["locationEstimated"] is True
    r2 = auth_client.put(f"/api/v1/stock/{r['id']}", json={"locationId": cid}).get_json()
    assert r2["locationEstimated"] is False and r2["location"]["name"] == "Cupboard"
