"""Cook/plan must convert between the recipe unit and the lot unit.

cook_ingredients + analyze_demand compared/consumed the demanded quantity
(recipe unit) directly against lot quantities (lot unit) with no conversion —
so 2 kg on hand vs a 500 g need consumed the WHOLE 2 kg and still reported a
shortfall; 1 kg need vs 500 g on hand reported canMakeAll. The Quantity value
object does the conversion and was bypassed.
"""
from app.api.integrations import cook_ingredients
from app.extensions import db


def _gid(app):
    from app.models import Group
    with app.app_context():
        return db.session.query(Group).first().id


def _add(c, name, quantity, unit):
    return c.post("/api/v1/stock",
                  json={"name": name, "quantity": quantity, "unit": unit,
                        "category": "dry_goods"}).get_json()


def test_cook_500g_from_a_2kg_lot_consumes_half_a_kg(auth_client, app):
    lot = _add(auth_client, "Flour", 2, "kg")
    gid = _gid(app)
    with app.app_context():
        res = cook_ingredients(gid, [{"name": "Flour", "quantity": 500,
                                      "unit": "g"}])
    r = res[0]
    # consumed/shortfall are in the DEMAND unit (grams): the 500 g need is met.
    assert r["consumed"] == 500, f"consumed {r['consumed']} g (should be 500)"
    assert r["shortfall"] == 0, f"false shortfall {r['shortfall']}"
    # and the lot has 1.5 kg left (0.5 kg drawn), not 0
    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["quantity"] == 1.5


def test_cook_1kg_need_against_500g_reports_the_real_shortfall(auth_client, app):
    _add(auth_client, "Sugar", 500, "g")
    gid = _gid(app)
    with app.app_context():
        res = cook_ingredients(gid, [{"name": "Sugar", "quantity": 1,
                                      "unit": "kg"}])
    r = res[0]
    # consumed the 500 g we had (0.5 kg), short by 0.5 kg
    assert round(r["consumed"], 3) == 0.5
    assert round(r["shortfall"], 3) == 0.5


def test_cook_incompatible_units_does_not_over_consume(auth_client, app):
    # recipe asks for volume, lot is mass — no density, can't convert safely
    lot = _add(auth_client, "Milk", 1000, "g")
    gid = _gid(app)
    with app.app_context():
        cook_ingredients(gid, [{"name": "Milk", "quantity": 2, "unit": "cup"}])
    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["quantity"] == 1000, "consumed an incompatible-unit lot (a guess)"


def test_cook_same_unit_still_works(auth_client, app):
    _add(auth_client, "Eggs", 12, "count")
    gid = _gid(app)
    with app.app_context():
        res = cook_ingredients(gid, [{"name": "Eggs", "quantity": 3, "unit": "count"}])
    assert res[0]["consumed"] == 3


def test_analyze_demand_converts_units(auth_client, app):
    """/plan: a 2 kg lot vs a 500 g need is fully covered, not 'short 498'."""
    from app.services.planning import analyze_demand
    from app.models import Product, StockLot
    gid = _gid(app)
    with app.app_context():
        p = Product(name="Flour", category="dry_goods", group_id=gid)
        db.session.add(p)
        db.session.flush()
        db.session.add(StockLot(product_id=p.id, quantity=2, unit="kg",
                                group_id=gid, finished=False))
        db.session.commit()
        res = analyze_demand(gid, [{"name": "Flour", "quantity": 500, "unit": "g"}])
    item = res["items"][0]
    assert item["onHand"] == 2000, f"onHand {item['onHand']} g (should be 2000)"
    assert item["have"] is True
    assert item["shortfall"] == 0
    assert res["canMakeAll"] is True
