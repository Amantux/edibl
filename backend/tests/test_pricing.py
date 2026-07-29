"""Price capture (Decimal, never float) + the spend/insights rollups: per-lot price,
value-on-hand grouping, spend-by-month, waste cost from a wasted consumption, and
per-product price history. Money assertions use Decimal, not float equality."""
from decimal import Decimal

from app.extensions import db
from app.models import ConsumptionEvent, StockLot, User


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def test_price_stored_as_decimal_not_float(auth_client, app):
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Milk", "quantity": 2, "price": "3.50"})
    assert r.status_code == 201
    lot_id = r.get_json()["id"]
    with app.app_context():
        lot = db.session.get(StockLot, lot_id)
        assert isinstance(lot.cost, Decimal)
        assert lot.cost == Decimal("3.50")
        assert lot.currency  # household currency stamped
    body = r.get_json()
    assert body["price"] == 3.5
    assert body["unitPrice"] == 1.75  # 3.50 / 2


def test_cost_alias_still_accepted(auth_client, app):
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Butter", "quantity": 1, "cost": "2.99"})
    assert r.status_code == 201
    with app.app_context():
        assert db.session.get(StockLot, r.get_json()["id"]).cost == Decimal("2.99")


def test_grouped_value_on_hand(auth_client, app):
    auth_client.post("/api/v1/stock", json={"productName": "Milk", "family": "Milk",
                                            "quantity": 1, "price": "3.00"})
    auth_client.post("/api/v1/stock", json={"productName": "Oat milk", "family": "Milk",
                                            "quantity": 1, "price": "4.00"})
    g = auth_client.get("/api/v1/stock/grouped").get_json()
    assert g["valueOnHand"] == 7.0
    milk = next(x for x in g["groups"] if x["group"] == "Milk")
    assert milk["valueOnHand"] == 7.0


def test_bulk_import_with_per_item_price(auth_client, app):
    r = auth_client.post("/api/v1/stock/bulk", json={"items": [
        {"name": "Rice", "quantity": 1, "price": "5.25"},
        {"name": "Beans", "quantity": 1, "cost": "1.10"},
    ]})
    assert r.status_code == 201
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        lots = db.session.query(StockLot).filter_by(group_id=gid).all()
        prices = sorted(lot.cost for lot in lots if lot.cost is not None)
        assert prices == [Decimal("1.10"), Decimal("5.25")]


def test_insights_spend_by_month_and_value(auth_client, app):
    auth_client.post("/api/v1/stock", json={"productName": "Eggs", "quantity": 1,
                                            "price": "4.00", "category": "dairy"})
    ins = auth_client.get("/api/v1/stock/insights").get_json()
    assert ins["valueOnHand"]["total"] == 4.0
    assert ins["spendThisMonth"] == 4.0
    # dense 12-month series, newest last, this month carries the spend
    assert len(ins["spendByMonth"]) == 12
    assert ins["spendByMonth"][-1]["spend"] == 4.0
    cats = {c["category"]: c["value"] for c in ins["valueOnHand"]["byCategory"]}
    assert cats.get("dairy") == 4.0


def test_waste_cost_from_wasted_consumption(auth_client, app):
    # A priced lot establishes the product's unit price; a spoiled consumption of
    # 2 units should be valued at 2 * unit_price.
    r = auth_client.post("/api/v1/stock", json={"productName": "Berries", "quantity": 4,
                                                "price": "8.00"})
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        pid = db.session.get(StockLot, r.get_json()["id"]).product_id
        db.session.add(ConsumptionEvent(product_id=pid, quantity=2, unit="count",
                                        outcome="spoiled", group_id=gid))
        db.session.commit()
    ins = auth_client.get("/api/v1/stock/insights").get_json()
    # unit price = 8.00 / 4 = 2.00; wasted 2 → 4.00
    assert ins["wasteCost"] == 4.0


def test_price_history_and_prefill(auth_client, app):
    auth_client.post("/api/v1/stock", json={"productName": "Coffee", "quantity": 1,
                                            "price": "6.00"})
    auth_client.post("/api/v1/stock", json={"productName": "Coffee", "quantity": 1,
                                            "price": "7.00"})
    ins = auth_client.get("/api/v1/stock/insights").get_json()
    coffee = next(h for h in ins["priceHistory"] if h["name"] == "Coffee")
    assert [p["price"] for p in coffee["points"]] == [6.0, 7.0]
    assert coffee["lastPrice"] == 7.0
    # products list carries the last price for re-add prefill
    prods = auth_client.get("/api/v1/products").get_json()
    row = next(p for p in prods if p["name"] == "Coffee")
    assert row["lastPrice"] == 7.0


# --------------------------------------------------------------------------- #
# Reviewer should-fixes: price validation + waste-cost unit match
# --------------------------------------------------------------------------- #
def test_to_money_rejects_negative_and_overflow():
    from app.schemas.serializers import to_money
    assert to_money("12.5") == Decimal("12.50")
    assert to_money("-5") is None            # negative price is nonsense → no price
    assert to_money("100000000") is None     # exceeds Numeric(10,2) → would 500 on PG
    assert to_money("99999999.99") == Decimal("99999999.99")  # max in-range value
    assert to_money("nan") is None


def test_negative_price_not_stored_and_not_in_totals(auth_client, app):
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Odd", "quantity": 1, "price": "-9.99"})
    with app.app_context():
        assert db.session.get(StockLot, r.get_json()["id"]).cost is None
    ins = auth_client.get("/api/v1/stock/insights").get_json()
    assert ins["valueOnHand"]["total"] == 0.0


def test_waste_cost_skips_unit_mismatch(auth_client, app):
    # Lot priced per COUNT (8.00 / 4 = 2.00/count). A loss recorded in grams must NOT
    # be valued at the per-count price (that would be a meaningless number).
    r = auth_client.post("/api/v1/stock", json={"productName": "Grapes", "quantity": 4,
                                                "unit": "count", "price": "8.00"})
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        pid = db.session.get(StockLot, r.get_json()["id"]).product_id
        db.session.add(ConsumptionEvent(product_id=pid, quantity=200, unit="g",
                                        outcome="spoiled", group_id=gid))
        db.session.commit()
    ins = auth_client.get("/api/v1/stock/insights").get_json()
    assert ins["wasteCost"] == 0.0  # unit mismatch → not counted


def test_parse_items_extracts_line_price(app):
    # The receipt extractor must carry a per-line price through (copy-paste ingest).
    from app.services.assistant import _parse_items
    with app.app_context():
        items = _parse_items(
            '[{"name":"Milk","quantity":1,"unit":"l","price":3.49},'
            ' {"name":"Bread","quantity":2,"unit":"count","price":"5.00"},'
            ' {"name":"Gum","quantity":1,"unit":"count"}]')
    by_name = {i["name"]: i for i in items}
    assert by_name["Milk"]["price"] == 3.49
    assert by_name["Bread"]["price"] == 5.0        # string coerced
    assert "price" not in by_name["Gum"]           # absent price stays absent
