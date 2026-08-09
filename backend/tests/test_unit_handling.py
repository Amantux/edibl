"""Units are a dimension, not a label — on both the consuming and the reporting
side of the same policy.

2 kg on hand against a 500 g need must draw 0.5 kg (not the whole lot, and not
report a shortfall), and 2 kg + 500 g on hand must read as 2.5 kg — while 3 cans
+ 500 g stay two buckets, because there is no principled factor between them.
Both surfaces convert through services/quantity.py so they cannot disagree.
"""

from app.api.integrations import cook_ingredients
from app.extensions import db
from app.models import Group, Product, StockLot


# ---- consuming: convert the demand into each lot's unit ----

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


# ---- reporting: aggregate on-hand without merging dimensions ----

def _seed(app, lots):
    with app.app_context():
        gid = db.session.query(Group).first().id
        p = Product(name="Flour", category="dry_goods", group_id=gid)
        db.session.add(p)
        db.session.flush()
        for qty, unit in lots:
            db.session.add(StockLot(product_id=p.id, quantity=qty, unit=unit,
                                    group_id=gid, finished=False))
        db.session.commit()
        return gid


def test_have_converts_within_a_dimension(auth_client, app):
    _seed(app, [(2, "kg"), (500, "g")])
    r = auth_client.get("/api/v1/have?ingredient=Flour").get_json()
    assert r["have"] is True
    # 2 kg + 500 g = 2.5 kg (dominant unit kg), not 2.5 nor 502
    assert r["unit"] == "kg"
    assert r["onHand"] == 2.5
    assert r["byUnit"] == {"kg": 2.5}


def test_have_does_not_merge_incompatible_units(auth_client, app):
    _seed(app, [(3, "can"), (500, "g")])
    r = auth_client.get("/api/v1/have?ingredient=Flour").get_json()
    assert r["have"] is True
    # two separate buckets, never 503
    assert r["byUnit"] == {"can": 3, "g": 500}
    assert r["onHand"] != 503


def test_assistant_have_reports_each_unit(auth_client, app):
    gid = _seed(app, [(2, "kg"), (500, "g")])
    from app.services.assistant import h_do_i_have
    with app.app_context():
        msg = h_do_i_have(gid, "Flour")
    assert "2.5 kg" in msg, msg
    assert "502" not in msg
