"""Use up stock from a SPECIFIC location.

POST /stock/consume drew across every lot of a product by policy, with no way to
say "use the milk in the garage fridge, not the kitchen one". Scoping is strict:
a location scope never spills to another location (that would consume the wrong
stock), it includes the location's sub-locations, and an unknown/foreign
location is refused rather than silently ignored.
"""


def _loc(c, name, parent=None):
    body = {"name": name}
    if parent:
        body["parentId"] = parent
    return c.post("/api/v1/locations", json=body).get_json()


def _stock(c, name, quantity, location_id, unit="count"):
    return c.post("/api/v1/stock",
                  json={"name": name, "quantity": quantity, "unit": unit,
                        "category": "dairy", "locationId": location_id}).get_json()


def _qty(c, lot_id):
    return c.get(f"/api/v1/stock/{lot_id}").get_json()["quantity"]


def test_consume_scoped_to_a_location_leaves_other_locations_alone(auth_client):
    kitchen, garage = _loc(auth_client, "Kitchen"), _loc(auth_client, "Garage")
    k_lot = _stock(auth_client, "Milk", 5, kitchen["id"])
    g_lot = _stock(auth_client, "Milk", 5, garage["id"])

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Milk", "quantity": 2,
                               "locationId": garage["id"]})

    assert r.status_code == 200, r.get_data(as_text=True)
    assert _qty(auth_client, g_lot["id"]) == 3, "did not draw from the named location"
    assert _qty(auth_client, k_lot["id"]) == 5, "consumed stock in ANOTHER location"


def test_scope_does_not_spill_outside_the_location(auth_client):
    kitchen, garage = _loc(auth_client, "Kitchen"), _loc(auth_client, "Garage")
    k_lot = _stock(auth_client, "Milk", 10, kitchen["id"])
    g_lot = _stock(auth_client, "Milk", 2, garage["id"])

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Milk", "quantity": 5,
                               "locationId": garage["id"]})
    body = r.get_json()

    assert _qty(auth_client, k_lot["id"]) == 10, "spilled into another location"
    assert _qty(auth_client, g_lot["id"]) == 0
    assert body["consumed"] == 2 and body["shortfall"] == 3


def test_scope_includes_sub_locations(auth_client):
    garage = _loc(auth_client, "Garage")
    fridge = _loc(auth_client, "Garage Fridge", parent=garage["id"])
    lot = _stock(auth_client, "Milk", 4, fridge["id"])

    auth_client.post("/api/v1/stock/consume",
                     json={"name": "Milk", "quantity": 1,
                           "locationId": garage["id"]})

    assert _qty(auth_client, lot["id"]) == 3, "sub-location stock was not in scope"


def test_unknown_location_is_refused_not_ignored(auth_client):
    kitchen = _loc(auth_client, "Kitchen")
    lot = _stock(auth_client, "Milk", 5, kitchen["id"])

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Milk", "quantity": 2,
                               "locationId": "no-such-location"})

    assert r.status_code == 422, "a typo'd location must not mean 'everywhere'"
    assert _qty(auth_client, lot["id"]) == 5, "consumed despite a bad location"


def test_no_location_still_draws_across_all_lots(auth_client):
    kitchen, garage = _loc(auth_client, "Kitchen"), _loc(auth_client, "Garage")
    _stock(auth_client, "Milk", 2, kitchen["id"])
    _stock(auth_client, "Milk", 2, garage["id"])

    body = auth_client.post("/api/v1/stock/consume",
                            json={"name": "Milk", "quantity": 4}).get_json()

    assert body["consumed"] == 4 and body["shortfall"] == 0


def test_another_groups_location_is_refused(auth_client, app):
    """IDOR: a location id belonging to another household must not scope (or
    widen) a consume — it is refused, and nothing is consumed."""
    kitchen = _loc(auth_client, "Kitchen")
    lot = _stock(auth_client, "Milk", 5, kitchen["id"])

    # A SEPARATE client: the auth_client fixture mutates the shared client's
    # headers, so reusing it would swap identity for the assertion below too.
    other = app.test_client()
    other.post("/api/v1/users/register",
               json={"email": "b@b.com", "password": "password", "name": "B"})
    tok = other.post("/api/v1/users/login",
                     json={"username": "b@b.com", "password": "password"}
                     ).get_json()["token"]
    other.environ_base["HTTP_AUTHORIZATION"] = tok
    foreign = other.post("/api/v1/locations", json={"name": "Their Pantry"}).get_json()

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "Milk", "quantity": 2,
                               "locationId": foreign["id"]})

    assert r.status_code == 422, "accepted another tenant's location"
    assert _qty(auth_client, lot["id"]) == 5, "consumed using a foreign location scope"


# ---- chat assistant surface ------------------------------------------------

def test_assistant_consume_is_scoped_to_the_named_location(auth_client, app):
    """The chat surface must obey the same strict scope as REST and MCP."""
    from app.extensions import db
    from app.models import Group
    from app.services.assistant import h_record_consumption

    kitchen, garage = _loc(auth_client, "Kitchen"), _loc(auth_client, "Garage")
    k_lot = _stock(auth_client, "Milk", 5, kitchen["id"])
    g_lot = _stock(auth_client, "Milk", 5, garage["id"])

    with app.app_context():
        gid = db.session.query(Group).first().id
        h_record_consumption(gid, "Milk", quantity=2, location="Garage")
        db.session.commit()

    assert _qty(auth_client, g_lot["id"]) == 3
    assert _qty(auth_client, k_lot["id"]) == 5, "chat consumed another location's stock"


def test_assistant_consume_reports_when_the_location_has_none(auth_client, app):
    from app.extensions import db
    from app.models import Group
    from app.services.assistant import h_record_consumption

    kitchen = _loc(auth_client, "Kitchen")
    _loc(auth_client, "Garage")
    k_lot = _stock(auth_client, "Milk", 5, kitchen["id"])

    with app.app_context():
        gid = db.session.query(Group).first().id
        msg = h_record_consumption(gid, "Milk", quantity=1, location="Garage")

    assert "Garage" in (msg if isinstance(msg, str) else msg[0])
    assert _qty(auth_client, k_lot["id"]) == 5, "fell back to another location"
