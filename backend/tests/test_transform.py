"""Phase-4 kitchen transformations: acquisition lots (batch identity separate from
placement), freeze/thaw preservation, and transform (cook/portion) with lineage."""


def _add(c, name="Chicken", quantity=2, unit="lb", **over):
    body = {"name": name, "quantity": quantity, "unit": unit, "category": "meat"}
    body.update(over)
    return c.post("/api/v1/stock", json=body).get_json()


# --- acquisition lots ------------------------------------------------------ #
def test_add_links_an_acquisition_lot(auth_client):
    lot = _add(auth_client, source="Costco")
    assert lot["acquisitionLotId"]
    assert lot["acquisition"]["source"] == "Costco"


def test_split_shares_the_acquisition_lot(auth_client):
    lot = _add(auth_client, quantity=5)
    j = auth_client.post(f"/api/v1/stock/{lot['id']}/split",
                         json={"quantity": 2}).get_json()
    # both positions come from the same purchase
    assert j["new"]["acquisitionLotId"] == lot["acquisitionLotId"]


# --- freeze / thaw --------------------------------------------------------- #
def test_freeze_changes_preservation_and_reverses(auth_client):
    lot = _add(auth_client, storageMethod="refrigerated")
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/freeze").get_json()
    assert r["storageMethod"] == "frozen"
    assert r["attrs"].get("freezeDate")

    auth_client.post(f"/api/v1/inventory/events/{r['eventId']}/reverse")
    back = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert back["storageMethod"] == "refrigerated"


def test_thaw_changes_preservation(auth_client):
    lot = _add(auth_client, storageMethod="frozen")
    r = auth_client.post(f"/api/v1/stock/{lot['id']}/thaw").get_json()
    assert r["storageMethod"] == "refrigerated"
    assert r["attrs"].get("thawDate")


# --- transform (cook / portion) with lineage ------------------------------- #
def test_transform_consumes_sources_and_produces_with_lineage(auth_client):
    chicken = _add(auth_client, name="Raw chicken", quantity=2, unit="lb")
    r = auth_client.post("/api/v1/stock/transform", json={
        "sources": [{"lotId": chicken["id"], "quantity": 2}],
        "products": [{"name": "Cooked chicken", "quantity": 4, "unit": "portion",
                      "category": "meat"}],
    })
    assert r.status_code == 201
    j = r.get_json()
    assert len(j["produced"]) == 1 and j["produced"][0]["quantity"] == 4.0

    src = auth_client.get(f"/api/v1/stock/{chicken['id']}").get_json()
    assert src["finished"] is True  # fully used up
    lineage = j["produced"][0]["acquisition"]["derivedFrom"].get("acquisitions", [])
    assert chicken["acquisitionLotId"] in lineage  # lineage preserved


def test_transform_reverses_as_one_batch(auth_client):
    chicken = _add(auth_client, name="Raw chicken", quantity=2, unit="lb")
    j = auth_client.post("/api/v1/stock/transform", json={
        "sources": [{"lotId": chicken["id"], "quantity": 2}],
        "products": [{"name": "Cooked chicken", "quantity": 4, "unit": "portion"}],
    }).get_json()
    produced_id = j["produced"][0]["id"]

    auth_client.post(f"/api/v1/inventory/transformations/{j['batchId']}/reverse")

    assert auth_client.get(f"/api/v1/stock/{chicken['id']}").get_json()["quantity"] == 2.0
    prod = auth_client.get(f"/api/v1/stock/{produced_id}").get_json()
    assert prod["finished"] is True  # produced item archived on undo


def test_transform_requires_a_product(auth_client):
    chicken = _add(auth_client)
    assert auth_client.post("/api/v1/stock/transform", json={
        "sources": [{"lotId": chicken["id"], "quantity": 1}], "products": [],
    }).status_code == 422


def test_migration_backfills_acquisition_lots_idempotently(app):
    from app import _backfill_acquisition_lots
    from app.extensions import db
    from app.models import Group, Product, StockLot, AcquisitionLot
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        p = Product(name="Rice", group_id=g.id)
        db.session.add(p)
        db.session.flush()
        lot = StockLot(product_id=p.id, quantity=3, unit="kg", group_id=g.id)
        db.session.add(lot)
        db.session.commit()
        assert lot.acquisition_lot_id is None

        _backfill_acquisition_lots()
        db.session.refresh(lot)
        assert lot.acquisition_lot_id is not None
        assert db.session.query(AcquisitionLot).count() == 1

        _backfill_acquisition_lots()  # re-run
        assert db.session.query(AcquisitionLot).count() == 1
