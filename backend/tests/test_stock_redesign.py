"""Phase-1 stock redesign — the 'Milk' vertical slice + the Quantity value object.

Proves the architecture spine end-to-end: dimension-safe/unknown-safe quantities,
the append-only inventory-event ledger, the shared command layer (REST + assistant
agree), orthogonal package_state, reversal that preserves history, idempotency,
household isolation of events, and the migration's opening-balance events.
"""
from decimal import Decimal

import pytest

from app.services.quantity import Quantity, aggregate


# --------------------------------------------------------------------------- #
# Quantity value object (pure) — invariants
# --------------------------------------------------------------------------- #
def test_quantity_converts_within_a_dimension():
    assert Quantity(value=1, unit="kg").add(Quantity(value=500, unit="g")).value == Decimal("1.5")


def test_quantity_refuses_to_sum_incompatible_dimensions():
    with pytest.raises(ValueError):
        Quantity(value=1, unit="g").add(Quantity(value=1, unit="ml"))


def test_quantity_packages_sum_only_unit_for_unit():
    # a carton and a bottle are both 'package' but never sum (no standard size)
    assert not Quantity(value=1, unit="carton").can_add(Quantity(value=1, unit="bottle"))
    assert Quantity(value=1, unit="carton").add(Quantity(value=1, unit="carton")).value == Decimal("2")


def test_quantity_unknown_is_not_zero_and_stays_unknown():
    u = Quantity.unknown("carton")
    assert u.value is None and u.kind == "unknown"
    summed = Quantity(value=2, unit="carton").add(u)
    assert summed.kind == "unknown" and summed.value is None  # never invents a total


def test_quantity_presence_reads_as_some():
    assert Quantity.present("bunch").describe() == "some"


def test_quantity_describe_distinguishes_exact_and_estimated():
    assert Quantity(value=6, unit="count").describe() == "6"
    assert Quantity(value=2, unit="carton", kind="estimated").describe() == "about 2 carton"


def test_aggregate_never_mixes_dimensions():
    out = aggregate([Quantity(value=1, unit="kg"), Quantity(value=500, unit="g"),
                     Quantity(value=2, unit="carton")])
    texts = sorted(q.describe() for q in out)
    assert texts == ["1.5 kg", "2 carton"]  # mass rolled up, package kept separate


# --------------------------------------------------------------------------- #
# The Milk slice (through the REST API + shared command layer)
# --------------------------------------------------------------------------- #
def _add_carton(c, **over):
    body = {"name": "Whole milk", "quantity": 1, "unit": "carton", "family": "Milk"}
    body.update(over)
    return c.post("/api/v1/stock", json=body).get_json()


def _group(c, name="Milk"):
    groups = c.get("/api/v1/stock/grouped").get_json()["groups"]
    return next(g for g in groups if g["group"] == name)


def test_new_lot_is_sealed_and_gets_an_add_event(auth_client):
    lot = _add_carton(auth_client)
    assert lot["packageState"] == "sealed"
    assert lot["quantityKind"] == "exact"
    assert lot["eventId"]  # add was logged to the ledger


def test_two_cartons_open_one_aggregates_open_and_sealed(auth_client):
    a = _add_carton(auth_client)
    _add_carton(auth_client)
    r = auth_client.post(f"/api/v1/stock/{a['id']}/open").get_json()
    assert r["packageState"] == "opened"

    g = _group(auth_client)
    assert g["openCount"] == 1 and g["sealedCount"] == 1
    assert "open" in g["summary"] and "sealed" in g["summary"]
    assert g["totalQuantity"] == 2.0


def test_consume_part_of_open_conserves_total(auth_client):
    a = _add_carton(auth_client)          # sealed
    b = _add_carton(auth_client)          # will open + partially use
    auth_client.post(f"/api/v1/stock/{b['id']}/open")
    r = auth_client.post(f"/api/v1/stock/{b['id']}/consume", json={"quantity": 0.5}).get_json()
    assert r["quantity"] == 0.5 and r["consumedAmount"] == 0.5

    g = _group(auth_client)
    assert g["totalQuantity"] == 1.5       # 1 sealed + 0.5 open
    assert g["openCount"] == 1 and g["sealedCount"] == 1
    assert a["id"] and b["id"]


def test_reverse_consumption_restores_amount_and_preserves_history(auth_client):
    b = _add_carton(auth_client)
    auth_client.post(f"/api/v1/stock/{b['id']}/open")
    consumed = auth_client.post(f"/api/v1/stock/{b['id']}/consume",
                                json={"quantity": 0.5}).get_json()
    event_id = consumed["eventId"]

    rev = auth_client.post(f"/api/v1/inventory/events/{event_id}/reverse").get_json()
    assert rev["lot"]["quantity"] == 1.0   # restored exactly

    ledger = auth_client.get(f"/api/v1/inventory/events?positionId={b['id']}").get_json()["events"]
    types = [e["type"] for e in ledger]
    assert "consume" in types and "reverse" in types      # history is append-only
    reverse_ev = next(e for e in ledger if e["type"] == "reverse")
    assert reverse_ev["reversalOf"] == event_id            # points back, not a rewrite


def test_consume_with_idempotency_key_applies_once(auth_client):
    b = _add_carton(auth_client)
    body = {"quantity": 0.5, "idempotencyKey": "same-key-123"}
    auth_client.post(f"/api/v1/stock/{b['id']}/consume", json=body)
    r2 = auth_client.post(f"/api/v1/stock/{b['id']}/consume", json=body).get_json()
    assert r2["quantity"] == 0.5           # decremented once, not twice


def test_add_presence_quantity_is_not_coerced_to_a_number(auth_client):
    lot = auth_client.post("/api/v1/stock",
                           json={"name": "Cilantro", "quantityKind": "presence"}).get_json()
    assert lot["quantityKind"] == "presence"
    assert lot["quantity"] is None        # never surfaces a fake number...
    assert lot["quantityText"] == "some"  # ...only the honest human form


def test_presence_lot_does_not_inflate_a_group_total(auth_client):
    _add_carton(auth_client, quantity=2)  # 2 cartons, exact
    # a presence lot in the SAME group must not add a phantom 1 to the total
    auth_client.post("/api/v1/stock",
                     json={"name": "Skim milk", "family": "Milk", "unit": "carton",
                           "quantityKind": "presence"})
    g = _group(auth_client)
    assert g["totalQuantity"] == 2.0
    assert g["lotCount"] == 2


def test_aggregate_does_not_merge_unregistered_units():
    out = aggregate([Quantity(value=2, unit="gizmo"), Quantity(value=3, unit="widget")])
    assert len(out) == 2  # two unknown units never fuse into one fabricated total


def test_reversing_an_add_event_is_rejected(auth_client):
    lot = _add_carton(auth_client)
    r = auth_client.post(f"/api/v1/inventory/events/{lot['eventId']}/reverse")
    assert r.status_code == 422  # add isn't reversible; not a silent no-op


def test_reversing_a_consumption_twice_restores_once(auth_client):
    b = _add_carton(auth_client)
    consumed = auth_client.post(f"/api/v1/stock/{b['id']}/consume",
                                json={"quantity": 0.5}).get_json()
    ev = consumed["eventId"]
    auth_client.post(f"/api/v1/inventory/events/{ev}/reverse")
    auth_client.post(f"/api/v1/inventory/events/{ev}/reverse")  # double-undo
    lot = auth_client.get(f"/api/v1/stock/{b['id']}").get_json()
    assert lot["quantity"] == 1.0  # restored exactly once, not 1.5


def test_reverse_event_of_another_group_is_404(auth_client, client):
    b = _add_carton(auth_client)
    consumed = auth_client.post(f"/api/v1/stock/{b['id']}/consume",
                                json={"quantity": 0.5}).get_json()
    # A second household must not reverse our event.
    client.post("/api/v1/users/register",
                json={"email": "b@b.com", "password": "password", "name": "B"})
    tok = client.post("/api/v1/users/login",
                      json={"email": "b@b.com", "password": "password"}).get_json()["token"]
    r = client.post(f"/api/v1/inventory/events/{consumed['eventId']}/reverse",
                    headers={"Authorization": tok})
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Assistant ↔ REST parity (same command layer)
# --------------------------------------------------------------------------- #
def test_assistant_consume_uses_the_same_ledger(app):
    from app.extensions import db
    from app.models import Group, Product, StockLot, InventoryEvent
    from app.services.assistant import h_record_consumption
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        p = Product(name="Whole milk", group_id=g.id)
        db.session.add(p)
        db.session.flush()
        lot = StockLot(product_id=p.id, quantity=1, unit="carton", group_id=g.id)
        db.session.add(lot)
        db.session.commit()

        msg, undo = h_record_consumption(g.id, "milk", quantity=0.5)
        db.session.refresh(lot)
        assert lot.quantity == 0.5
        assert undo["op"] == "reverse_event"
        ev = db.session.query(InventoryEvent).filter_by(group_id=g.id, type="consume").one()
        assert ev.src_position_id == lot.id     # assistant wrote the ledger too


# --------------------------------------------------------------------------- #
# Migration backfill
# --------------------------------------------------------------------------- #
def test_migration_backfills_one_import_event_per_lot_idempotently(app):
    from app import _backfill_inventory_events
    from app.extensions import db
    from app.models import Group, Product, StockLot, InventoryEvent
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        p = Product(name="Eggs", group_id=g.id)
        db.session.add(p)
        db.session.flush()
        lot = StockLot(product_id=p.id, quantity=6, unit="count",
                       storage_method="opened", group_id=g.id)
        db.session.add(lot)
        db.session.commit()

        _backfill_inventory_events()
        events = db.session.query(InventoryEvent).filter_by(type="import").all()
        assert len(events) == 1
        assert events[0].dst_position_id == lot.id
        db.session.refresh(lot)
        assert lot.package_state == "opened"    # derived from legacy storage_method

        _backfill_inventory_events()            # re-run
        assert db.session.query(InventoryEvent).filter_by(type="import").count() == 1
