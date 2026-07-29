"""Core / "always in stock" staples → shopping-list auto-restock. No migration: this
builds on the existing Product.staple / reorder_threshold policy fields and the
ShoppingItem.source marker (auto rows use source='auto', the only ones we retract)."""
from app.extensions import db
from app.models import Product, ShoppingItem, StockLot, User
from app.services.core_items import sync_staple_shopping


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _mk_product(gid, name, *, staple=False, threshold=None, min_q=None, on_hand=None):
    p = Product(name=name, group_id=gid, staple=staple,
                reorder_threshold=threshold, min_quantity=min_q, default_unit="count")
    db.session.add(p)
    db.session.flush()
    if on_hand is not None:
        db.session.add(StockLot(product_id=p.id, group_id=gid, quantity=on_hand,
                                unit="count", quantity_kind="exact"))
    db.session.commit()
    return p.id


def _open_items(gid, pid):
    return (db.session.query(ShoppingItem)
            .filter_by(group_id=gid, product_id=pid, status="needed").all())


def test_low_staple_auto_adds_one_idempotent(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        pid = _mk_product(gid, "Butter", staple=True, threshold=1, on_hand=1)  # at level → low
        assert sync_staple_shopping(gid, product_id=pid)["added"] == 1
        items = _open_items(gid, pid)
        assert len(items) == 1 and items[0].source == "auto"
        # re-sync must not create a second row
        assert sync_staple_shopping(gid, product_id=pid)["added"] == 0
        assert len(_open_items(gid, pid)) == 1


def test_restock_removes_only_the_auto_row(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        pid = _mk_product(gid, "Milk", staple=True, threshold=1, on_hand=0)  # empty → low
        sync_staple_shopping(gid, product_id=pid)
        # add a user row + an already-checked (bought) auto row — both must survive
        db.session.add(ShoppingItem(name="Milk", product_id=pid, group_id=gid, source="manual"))
        db.session.add(ShoppingItem(name="Milk", product_id=pid, group_id=gid,
                                    source="auto", status="bought"))
        # restock well above threshold
        db.session.add(StockLot(product_id=pid, group_id=gid, quantity=5, unit="count",
                                quantity_kind="exact"))
        db.session.commit()
        res = sync_staple_shopping(gid, product_id=pid)
        assert res["removed"] == 1  # only the OPEN auto row
        left = {(i.source, i.status) for i in db.session.query(ShoppingItem)
                .filter_by(group_id=gid, product_id=pid).all()}
        assert ("manual", "needed") in left      # user row kept
        assert ("auto", "bought") in left         # checked row kept
        assert ("auto", "needed") not in left     # open auto row retracted


def test_non_staple_never_auto_adds(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        # has a reorder threshold (would be a *suggestion*) but is NOT a staple
        pid = _mk_product(gid, "Sprinkles", staple=False, threshold=1, on_hand=0)
        assert sync_staple_shopping(gid)["added"] == 0
        assert _open_items(gid, pid) == []


def test_threshold_resolution_matches_reorder(auth_client, app):
    from app.services.reorder import reorder_state
    gid = _gid(app)
    with app.app_context():
        # reorder_threshold wins over min_quantity
        p = db.session.get(Product, _mk_product(gid, "Eggs", staple=True,
                                                threshold=6, min_q=2, on_hand=6))
        st = reorder_state(p)
        assert st["threshold"] == 6 and st["isLow"] is True       # 6 <= 6
        # falls back to min_quantity when no reorder_threshold
        p2 = db.session.get(Product, _mk_product(gid, "Flour", staple=True,
                                                 min_q=3, on_hand=4))
        st2 = reorder_state(p2)
        assert st2["threshold"] == 3 and st2["isLow"] is False     # 4 > 3
        # default 1 when neither is set
        p3 = db.session.get(Product, _mk_product(gid, "Salt", staple=True, on_hand=1))
        st3 = reorder_state(p3)
        assert st3["threshold"] == 1 and st3["isLow"] is True       # 1 <= 1


def test_set_staple_tool_adds_and_undo_reverts(auth_client, app):
    from app.services.assistant import h_set_staple, apply_undo
    gid = _gid(app)
    with app.app_context():
        pid = _mk_product(gid, "Coffee", staple=False, on_hand=0)  # empty
        msg, undo = h_set_staple(gid, "Coffee", staple=True)
        assert "staple" in msg.lower()
        assert db.session.get(Product, pid).staple is True
        # marking a low product staple lists it immediately (default threshold 1)
        assert len(_open_items(gid, pid)) == 1
        apply_undo(gid, undo)
        assert db.session.get(Product, pid).staple is False


def test_consume_to_low_triggers_auto_add(auth_client, app):
    """End-to-end through the command layer: consuming a staple down to its threshold
    should add the auto row via the _restock_sync hook."""
    from app.services.inventory import consume_lot
    gid = _gid(app)
    with app.app_context():
        pid = _mk_product(gid, "Yogurt", staple=True, threshold=1, on_hand=2)
        assert _open_items(gid, pid) == []          # not low yet (2 > 1)
        lot = (db.session.query(StockLot)
               .filter_by(group_id=gid, product_id=pid).first())
        consume_lot(lot, amount=1, outcome="eaten")  # 2 → 1 = at threshold
        items = _open_items(gid, pid)
        assert len(items) == 1 and items[0].source == "auto"


def test_name_only_manual_row_blocks_auto_add(auth_client, app):
    """A row the user typed by name (no productId) for a staple must block the auto-add,
    so we don't create a duplicate 'Milk' line."""
    gid = _gid(app)
    with app.app_context():
        pid = _mk_product(gid, "Milk", staple=True, threshold=1, on_hand=0)  # low
        db.session.add(ShoppingItem(name="milk", group_id=gid, source="manual"))  # no product_id
        db.session.commit()
        assert sync_staple_shopping(gid, product_id=pid)["added"] == 0
        # exactly the one user row, no auto row
        rows = db.session.query(ShoppingItem).filter_by(group_id=gid, status="needed").all()
        assert len(rows) == 1 and rows[0].source == "manual"


def test_duplicate_auto_rows_collapse_to_one(auth_client, app):
    """A concurrent double-add can slip two auto rows through the check-then-insert
    window; the next sync must converge back to one."""
    gid = _gid(app)
    with app.app_context():
        pid = _mk_product(gid, "Oats", staple=True, threshold=1, on_hand=0)  # low
        for _ in range(2):  # simulate the raced duplicate
            db.session.add(ShoppingItem(name="Oats", product_id=pid, group_id=gid,
                                        source="auto", status="needed"))
        db.session.commit()
        assert len(_open_items(gid, pid)) == 2
        res = sync_staple_shopping(gid, product_id=pid)
        assert res["removed"] == 1
        assert len(_open_items(gid, pid)) == 1  # converged


def test_grouped_lots_expose_product_staple(auth_client, app):
    """The grouped view needs each lot's product.staple to render the ★ toggle."""
    gid = _gid(app)
    with app.app_context():
        _mk_product(gid, "Butter", staple=True, on_hand=1)
    g = auth_client.get("/api/v1/stock/grouped").get_json()
    lot = g["groups"][0]["lots"][0]
    assert lot["product"]["staple"] is True


def test_unmark_staple_retracts_orphan_auto_row(auth_client, app):
    from app.services.assistant import h_set_staple
    gid = _gid(app)
    with app.app_context():
        pid = _mk_product(gid, "Tea", staple=True, threshold=1, on_hand=0)  # low → auto row
        sync_staple_shopping(gid, product_id=pid)
        assert len(_open_items(gid, pid)) == 1
        # un-mark via the chat tool → the auto row must not linger
        h_set_staple(gid, "Tea", staple=False)
        assert _open_items(gid, pid) == []
