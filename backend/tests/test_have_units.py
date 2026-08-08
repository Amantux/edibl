"""'Do I have X' must not sum across incompatible units.

Both the /have endpoint and the assistant's h_do_i_have summed every matching
lot's raw quantity regardless of unit, so 2 kg + 500 g read as 2.5 and 3 cans +
500 g read as 503. Only truly additive lots combine now; the rest are reported
per unit.
"""
from app.extensions import db
from app.models import Group, Product, StockLot


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
