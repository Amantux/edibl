"""PUT /stock/<id> must not bypass the invariants the command layer enforces:
a cost is coerced through to_money (no negative/NaN/raw-float into Numeric), and
`finished` is recomputed from the edited quantity (0 ⇒ finished, >0 ⇒ active)."""
import math


def _add(c, quantity=5, unit="count"):
    return c.post("/api/v1/stock",
                  json={"name": "Beans", "quantity": quantity, "unit": unit,
                        "category": "canned"}).get_json()


def _put(c, lot_id, **body):
    return c.put(f"/api/v1/stock/{lot_id}", json=body)


def test_negative_cost_is_rejected_to_none(auth_client):
    lot = _add(auth_client)
    r = _put(auth_client, lot["id"], cost=-5)
    assert r.status_code == 200
    assert r.get_json()["cost"] is None, "negative cost stored as-is"


def test_nan_cost_is_rejected_to_none(auth_client):
    lot = _add(auth_client)
    # JSON can't carry NaN natively; send the string the coercion point sees.
    r = _put(auth_client, lot["id"], cost="nan")
    assert r.get_json()["cost"] is None


def test_quantity_to_zero_marks_finished(auth_client):
    lot = _add(auth_client, quantity=5)
    r = _put(auth_client, lot["id"], quantity=0)
    body = r.get_json()
    assert body["finished"] is True, "depleted lot not marked finished"


def test_quantity_above_zero_unfinishes(auth_client):
    lot = _add(auth_client, quantity=5)
    _put(auth_client, lot["id"], quantity=0)          # finish it
    r = _put(auth_client, lot["id"], quantity=3)        # refill
    assert r.get_json()["finished"] is False, "refilled lot still finished"


def test_negative_quantity_is_refused(auth_client):
    lot = _add(auth_client, quantity=5)
    r = _put(auth_client, lot["id"], quantity=-2)
    assert r.status_code == 422
    # unchanged
    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["quantity"] == 5


def test_nan_quantity_is_refused(auth_client):
    lot = _add(auth_client, quantity=5)
    r = _put(auth_client, lot["id"], quantity="nan")
    assert r.status_code == 422
    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["quantity"] == 5 and math.isfinite(after["quantity"])
