"""Chat tools: group products into a display family, and move items between
locations — both reversible via the assistant's undo."""
from app.extensions import db
from app.models import Location, Product, StockLot, User
from app.services import assistant


def _gid():
    return db.session.query(User).filter_by(email="t@t.com").first().group_id


def test_group_products_sets_family_and_undo_restores(app, auth_client):
    with app.app_context():
        gid = _gid()
        a = Product(name="Whole milk", family="", group_id=gid)
        b = Product(name="Oat milk", family="", group_id=gid)
        db.session.add_all([a, b])
        db.session.commit()
        a_id, b_id = a.id, b.id

        text, undo = assistant.h_group_products(gid, "Milk", ["Whole milk", "Oat milk"])
        db.session.expire_all()
        assert "Milk" in text
        assert db.session.get(Product, a_id).family == "Milk"
        assert db.session.get(Product, b_id).family == "Milk"

        assert undo["op"] == "restore_families"
        assistant.apply_undo(gid, undo)
        db.session.expire_all()
        assert db.session.get(Product, a_id).family == ""
        assert db.session.get(Product, b_id).family == ""


def test_group_products_reports_missing(app, auth_client):
    with app.app_context():
        gid = _gid()
        db.session.add(Product(name="Butter", group_id=gid))
        db.session.commit()
        text, _undo = assistant.h_group_products(gid, "Dairy", ["Butter", "Nope"])
        assert "Butter" in text and "couldn't find" in text.lower() and "Nope" in text


def test_group_products_all_missing_is_a_plain_message(app, auth_client):
    with app.app_context():
        res = assistant.h_group_products(_gid(), "X", ["Ghost"])
        assert isinstance(res, str) and "couldn't find" in res.lower()  # no undo → nothing changed


def test_move_stock_relocates_and_undo_restores(app, auth_client):
    with app.app_context():
        gid = _gid()
        fridge = Location(name="Fridge", group_id=gid)
        freezer = Location(name="Freezer", group_id=gid)
        db.session.add_all([fridge, freezer])
        db.session.commit()
        assistant.h_add_stock(gid, name="Peas", quantity=1, unit="bag", location="Fridge")
        db.session.commit()

        lot = (db.session.query(StockLot).join(Product)
               .filter(Product.name == "Peas", StockLot.group_id == gid).first())
        assert lot.location_id == fridge.id
        lot_id = lot.id

        _text, undo = assistant.h_move_stock(gid, "Peas", "Freezer")
        db.session.expire_all()
        assert db.session.get(StockLot, lot_id).location_id == freezer.id

        assistant.apply_undo(gid, undo)
        db.session.expire_all()
        assert db.session.get(StockLot, lot_id).location_id == fridge.id
