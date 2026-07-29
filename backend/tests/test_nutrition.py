"""Nutrition capture (OFF parse) + per-Product storage (migration 0009) + the honest
pantry aggregate. Nutrition lives on Product (a specific packaged item carries the label,
a FoodConcept is too broad)."""
import importlib.util
import os

from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.extensions import db
from app.models import Product, StockLot, User
from app.services.barcode import _nutrition_from_off


def _gid():
    return db.session.query(User).filter_by(email="t@t.com").first().group_id


# ---- OFF parse (pure) ------------------------------------------------------ #
def test_off_parse_normalizes_omits_missing_and_derives_sodium():
    n = _nutrition_from_off({
        "nutriments": {"energy-kcal_100g": 250, "proteins_100g": 12.5,
                       "carbohydrates_100g": 30, "fat_100g": 8,
                       "energy-kcal_serving": 75, "proteins_serving": 3.75,
                       "salt_100g": 1.25},  # no sodium key → derive from salt
        "serving_size": "30 g", "nutrition_data_per": "100g"})
    assert n["basis"] == "100g" and n["source"] == "openfoodfacts"
    assert n["per100"]["kcal"] == 250 and n["per100"]["protein"] == 12.5
    assert "sugar" not in n["per100"]              # absent key omitted, not zero-filled
    assert n["per100"]["sodium"] == 0.5            # 1.25 g salt / 2.5
    assert n["perServing"]["kcal"] == 75
    assert n["servingSize"] == "30 g"


def test_off_parse_none_when_no_usable_nutriments():
    assert _nutrition_from_off({"nutriments": {}}) is None
    assert _nutrition_from_off({}) is None
    assert _nutrition_from_off({"nutriments": "garbage"}) is None


def test_off_parse_infers_ml_basis():
    n = _nutrition_from_off({"nutriments": {"energy-kcal_100g": 42}, "serving_size": "250 ml"})
    assert n["basis"] == "100ml"


def test_off_parse_rejects_negative_and_nan():
    n = _nutrition_from_off({"nutriments": {"energy-kcal_100g": 200, "fat_100g": -3,
                                            "proteins_100g": float("nan")}})
    assert n["per100"]["kcal"] == 200
    assert "fat" not in n["per100"] and "protein" not in n["per100"]


def test_off_parse_rejects_infinity(app):
    # OFF is community-entered and json.loads yields inf for a literal `Infinity`;
    # inf must never enter nutrition (jsonify would emit invalid JSON and break the
    # whole /stock/insights + stock-list response client-side).
    from app.services.barcode import _num
    assert _num(float("inf")) is None and _num(float("-inf")) is None
    n = _nutrition_from_off({"nutriments": {"energy-kcal_100g": float("inf"),
                                            "proteins_100g": 5}})
    assert n["per100"] == {"protein": 5}  # inf omitted, real value kept


# ---- storage + serializer -------------------------------------------------- #
def test_product_nutrition_stored_and_serialized(auth_client, app):
    from app.schemas.serializers import product_out
    with app.app_context():
        p = Product(name="Oats", group_id=_gid(), default_unit="g",
                    nutrition={"basis": "100g", "per100": {"kcal": 380, "protein": 13}})
        db.session.add(p)
        db.session.commit()
        assert product_out(p)["nutrition"]["per100"]["kcal"] == 380


def test_scan_add_stores_nutrition_on_new_product(auth_client, app):
    r = auth_client.post("/api/v1/stock", json={
        "productName": "Cereal", "quantity": 1, "barcode": "5010",
        "nutrition": {"basis": "100g", "per100": {"kcal": 100}}})
    assert r.status_code in (200, 201)
    with app.app_context():
        p = db.session.query(Product).filter_by(group_id=_gid(), name="Cereal").first()
        assert p.nutrition["per100"]["kcal"] == 100


# ---- pantry aggregate ------------------------------------------------------ #
def test_pantry_nutrition_sums_only_convertible_items(auth_client, app):
    with app.app_context():
        gid = _gid()
        rice = Product(name="Rice", group_id=gid, default_unit="g",
                       nutrition={"basis": "100g", "per100": {"kcal": 250, "protein": 5}})
        db.session.add(rice)
        db.session.flush()
        db.session.add(StockLot(product_id=rice.id, group_id=gid, quantity=200, unit="g",
                                quantity_kind="exact"))  # 200 g → 2×per100 → 500 kcal
        eggs = Product(name="Eggs", group_id=gid, default_unit="count",
                       nutrition={"basis": "100g", "per100": {"kcal": 70}})
        db.session.add(eggs)
        db.session.flush()
        db.session.add(StockLot(product_id=eggs.id, group_id=gid, quantity=6, unit="count",
                                quantity_kind="exact"))  # count → can't convert → excluded
        db.session.commit()
    pn = auth_client.get("/api/v1/stock/insights").get_json()["pantryNutrition"]
    assert pn["totals"]["kcal"] == 500.0
    assert pn["totals"]["protein"] == 10.0
    assert pn["itemsIncluded"] == 1
    assert pn["itemsExcluded"] >= 1  # the count-unit eggs (and any other on-hand lots)


def test_pantry_keeps_zero_and_counts_null_quantity_kind(auth_client, app):
    # A legacy lot with quantity but no quantity_kind must still count (mirrors runout's
    # `or "exact"`); and a genuine 0 macro is data, kept as 0.0 not dropped to unknown.
    with app.app_context():
        gid = _gid()
        soda = Product(name="Soda", group_id=gid, default_unit="ml",
                       nutrition={"basis": "100ml", "per100": {"kcal": 40, "sugar": 0}})
        db.session.add(soda)
        db.session.flush()
        lot = StockLot(product_id=soda.id, group_id=gid, quantity=100, unit="ml")
        lot.quantity_kind = None  # legacy row: numeric qty, no kind
        db.session.add(lot)
        db.session.commit()
    pn = auth_client.get("/api/v1/stock/insights").get_json()["pantryNutrition"]
    assert pn["totals"]["kcal"] == 40.0     # None kind treated as exact → included
    assert pn["totals"]["sugar"] == 0.0     # genuine zero kept, not dropped


def test_backfill_sentinels_unfillable_product(auth_client, app, monkeypatch):
    # A barcode OFF has no nutrition for must be marked (sentinel {}) so it drops out of
    # the NULL candidate set instead of being re-fetched every enrich run.
    from app.services import jobs
    with app.app_context():
        gid = _gid()
        p = Product(name="Mystery", group_id=gid, barcode="9999", nutrition=None)
        db.session.add(p)
        db.session.commit()
        pid = p.id
        app.config["BARCODE_LOOKUP"] = True
        monkeypatch.setattr("app.services.barcode.lookup_barcode",
                            lambda code: {"name": "Mystery"})  # a hit, but no nutrition
        assert jobs._backfill_nutrition(gid) == 0
        refreshed = db.session.get(Product, pid)
        assert refreshed.nutrition == {}                       # sentinel written
        # serializes back to null (UI shows nothing), but is no longer a NULL candidate
        from app.schemas.serializers import product_out
        assert product_out(refreshed)["nutrition"] is None


# ---- migration 0009 idempotency ------------------------------------------- #
def _load_mig(app):
    path = os.path.join(os.path.dirname(app.root_path),
                        "migrations", "versions", "0009_product_nutrition.py")
    spec = importlib.util.spec_from_file_location("mig0009", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0009_upgrade_idempotent(app):
    # The column already exists (metadata create_all), so upgrade() must detect it and
    # be a clean no-op rather than erroring — the idempotency guard the migration relies
    # on when re-run. (Downgrade is not exercised: it would drop the column other tests
    # in this session need.)
    with app.app_context():
        mod = _load_mig(app)
        assert mod.down_revision == "0008_lot_price_numeric"
        ctx = MigrationContext.configure(db.session.connection())
        with Operations.context(ctx):
            mod.upgrade()  # column present → no-op, no raise
        assert "nutrition" in {c["name"] for c in
                               db.inspect(db.session.connection()).get_columns("products")}
