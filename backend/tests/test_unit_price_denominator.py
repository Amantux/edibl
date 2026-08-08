"""Learned unit price must divide cost by the ORIGINAL purchased quantity, not
the remaining quantity — else consuming a lot inflates its unit price (10 kg
@ $10 → 1.00; consume 9 kg → 10.00, a 10x drift), poisoning waste/spend
valuations and the wire unitPrice.
"""


def _add(c, **kw):
    body = {"name": "Flour", "quantity": 10, "unit": "kg",
            "category": "dry_goods", "cost": "10.00"}
    body.update(kw)
    return c.post("/api/v1/stock", json=body).get_json()


def test_typical_unit_price_is_stable_across_consumption(auth_client, app):
    lot = _add(auth_client)
    # consume 9 of 10 kg
    auth_client.post(f"/api/v1/stock/{lot['id']}/consume", json={"quantity": 9})

    from app.api.dashboard import _typical_unit_prices
    from app.models import Group
    from app.extensions import db
    with app.app_context():
        gid = db.session.query(Group).first().id
        typical, _last_unit, _last_price, _pu = _typical_unit_prices(gid)
        pid = next(iter(typical))
        # $10 / 10 kg = 1.00, regardless of how much is left
        assert abs(float(typical[pid]) - 1.0) < 1e-6, \
            f"unit price drifted to {typical[pid]} after consumption"


def test_wire_unit_price_stable_across_consumption(auth_client):
    lot = _add(auth_client)
    before = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json().get("unitPrice")
    auth_client.post(f"/api/v1/stock/{lot['id']}/consume", json={"quantity": 9})
    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json().get("unitPrice")
    assert before == after == 1.0, f"wire unitPrice drifted {before} -> {after}"
