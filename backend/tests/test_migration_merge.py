"""The 0007 migration's product-merge is the only data-destroying code in the
ingestion work. The suite runs migrations on an EMPTY DB (merge = no-op), so this
seeds the pre-migration duplicate-barcode state and asserts the merge re-points
children onto the survivor and drops the duplicate (nothing orphaned)."""
import importlib.util
import os

import sqlalchemy as sa

from app.extensions import db
from app.models import AiSuggestion, Product, StockLot, User


def _load_migration(app):
    path = os.path.join(os.path.dirname(app.root_path),
                        "migrations", "versions", "0007_dedupe_product_barcode.py")
    spec = importlib.util.spec_from_file_location("mig0007", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_repoints_children_and_deletes_duplicate(app, auth_client):
    with app.app_context():
        gid = db.session.query(User).filter_by(email="t@t.com").first().group_id
        # Drop the unique index so we can recreate the pre-migration duplicate state.
        db.session.execute(sa.text("DROP INDEX IF EXISTS uq_products_group_barcode"))
        db.session.commit()

        keep = Product(name="Milk", barcode="0123", group_id=gid)
        db.session.add(keep)
        db.session.flush()                      # keep is created first → the survivor
        dupe = Product(name="Semi milk", barcode="0123", group_id=gid)
        db.session.add(dupe)
        db.session.flush()

        lot = StockLot(product_id=dupe.id, group_id=gid, quantity=1, unit="ct")
        sugg = AiSuggestion(kind="categorize", label="dairy", product_id=dupe.id, group_id=gid)
        db.session.add_all([lot, sugg])
        db.session.commit()
        keep_id, dupe_id, lot_id, sugg_id = keep.id, dupe.id, lot.id, sugg.id

        _load_migration(app)._merge_duplicates(db.session.connection())
        db.session.commit()
        db.session.expire_all()   # the merge used raw SQL; drop the stale identity map

        assert db.session.query(Product).filter_by(id=dupe_id).first() is None   # merged away
        assert db.session.query(Product).filter_by(id=keep_id).first() is not None  # survivor kept
        relot = db.session.query(StockLot).filter_by(id=lot_id).first()
        assert relot is not None and relot.product_id == keep_id                 # re-pointed, not orphaned
        assert db.session.query(AiSuggestion).filter_by(id=sugg_id).first() is None  # dropped
