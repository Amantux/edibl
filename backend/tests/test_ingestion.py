"""Ingestion fixes: barcode dedupe/back-fill, quantity-0, bulk via the event-logged
model (idempotent), detection-match reuse, and the (group_id, barcode) uniqueness."""
import pytest

from app.extensions import db
from app.models import Detection, InventoryEvent, Product, StockLot, User


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def test_scan_same_barcode_reuses_product_no_duplicate(auth_client, app):
    assert auth_client.post("/api/v1/stock",
                            json={"productName": "Beans", "barcode": "5000", "quantity": 2}).status_code == 201
    # A later scan of the same code, even with a different name from the online lookup.
    assert auth_client.post("/api/v1/stock",
                            json={"productName": "Tinned Beans", "barcode": "5000", "quantity": 1}).status_code == 201
    with app.app_context():
        assert db.session.query(Product).filter_by(barcode="5000").count() == 1


def test_barcode_backfills_onto_name_matched_product(auth_client, app):
    auth_client.post("/api/v1/stock", json={"productName": "Milk", "quantity": 1})           # no barcode
    auth_client.post("/api/v1/stock", json={"productName": "Milk", "barcode": "7777", "quantity": 1})
    with app.app_context():
        rows = db.session.query(Product).filter_by(name="Milk").all()
        assert len(rows) == 1 and rows[0].barcode == "7777"


def test_quantity_zero_is_preserved(auth_client, app):
    r = auth_client.post("/api/v1/stock",
                         json={"productName": "Eggs", "quantity": 0, "quantityKind": "exact"})
    assert r.status_code == 201
    with app.app_context():
        assert db.session.get(StockLot, r.get_json()["id"]).quantity == 0.0


def test_bulk_import_logs_events_and_is_idempotent(auth_client, app):
    body = {"idempotencyKey": "receipt-abc", "items": [{"name": "Rice"}, {"name": "Oil"}]}
    assert auth_client.post("/api/v1/stock/bulk", json=body).get_json()["created"] == 2
    auth_client.post("/api/v1/stock/bulk", json=body)  # a re-submit must NOT duplicate
    with app.app_context():
        assert db.session.query(StockLot).count() == 2
        assert db.session.query(InventoryEvent).filter_by(type="add").count() == 2  # events logged


def test_detection_confirm_reuses_the_matched_product(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        uid = db.session.query(User).filter_by(email="t@t.com").first().id
        prod = Product(name="Cheddar", family="Cheese", group_id=gid)
        db.session.add(prod)
        db.session.flush()
        det = Detection(name="block of cheese", matched_product_id=prod.id,
                        group_id=gid, created_by=uid)
        db.session.add(det)
        db.session.commit()
        det_id = det.id
        before = db.session.query(Product).count()
    r = auth_client.post(f"/api/v1/stock/detections/{det_id}/confirm", json={})
    assert r.status_code == 200
    with app.app_context():
        assert db.session.query(Product).count() == before   # reused the match, no new product
        assert db.session.get(StockLot, r.get_json()["id"]).product.name == "Cheddar"


def test_unique_barcode_per_group_is_enforced(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        db.session.add(Product(name="A", barcode="9999", group_id=gid))
        db.session.commit()
        db.session.add(Product(name="B", barcode="9999", group_id=gid))
        with pytest.raises(Exception):   # IntegrityError from the partial unique index
            db.session.commit()
        db.session.rollback()
    # Empty barcodes stay unconstrained (partial index): two blank-barcode products are fine.
    with app.app_context():
        db.session.add_all([Product(name="C", barcode="", group_id=gid),
                            Product(name="D", barcode="", group_id=gid)])
        db.session.commit()
