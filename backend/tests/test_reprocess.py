"""The 'Update existing data' job: re-classify products stuck in "other", assign
families, recompute ESTIMATED lot expiries — without touching anything the user set.
Idempotent."""
from datetime import datetime

from app.extensions import db
from app.models import Product, StockLot, User
from app.services import jobs, reprocess


def _gid():
    return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _run(gid):
    jobs.enqueue("reprocess", gid)
    jobs.run_job(jobs.claim_one())


def test_reclassifies_other_and_assigns_family(app, auth_client, monkeypatch):
    monkeypatch.setattr(reprocess, "classify_food",
                        lambda name: {"category": "dairy", "family": "Milk"})
    with app.app_context():
        gid = _gid()
        p = Product(name="Whole milk 2L", category="other", family="", group_id=gid)
        db.session.add(p)
        db.session.commit()
        pid = p.id
        _run(gid)
        db.session.expire_all()
        p2 = db.session.get(Product, pid)
        assert p2.category == "dairy" and p2.family == "Milk"


def test_leaves_user_set_category_and_explicit_expiry_untouched(app, auth_client, monkeypatch):
    # classify would say "dairy", but the product already has an explicit category,
    # and its lot has an explicit (non-estimated) expiry — both must survive.
    monkeypatch.setattr(reprocess, "classify_food",
                        lambda name: {"category": "dairy", "family": "Milk"})
    with app.app_context():
        gid = _gid()
        p = Product(name="Grandma's stew", category="prepared", family="Stews", group_id=gid)
        db.session.add(p)
        db.session.flush()
        when = datetime(2030, 1, 1)
        lot = StockLot(product_id=p.id, group_id=gid, quantity=1, unit="ct",
                       expiry_estimated=False, expiry_date=when,
                       purchase_date=datetime(2026, 1, 1))
        db.session.add(lot)
        db.session.commit()
        pid, lid = p.id, lot.id
        _run(gid)
        db.session.expire_all()
        assert db.session.get(Product, pid).category == "prepared"    # not reclassified
        assert db.session.get(StockLot, lid).expiry_date == when       # explicit date kept


def test_is_idempotent(app, auth_client, monkeypatch):
    monkeypatch.setattr(reprocess, "classify_food",
                        lambda name: {"category": "dairy", "family": "Milk"})
    with app.app_context():
        gid = _gid()
        db.session.add(Product(name="Whole milk", category="other", family="", group_id=gid))
        db.session.commit()
        _run(gid)                    # first pass fixes it
        db.session.expire_all()
        # second pass: enqueue + run again → nothing left to change
        jobs.enqueue("reprocess", gid)
        job = jobs.claim_one()
        result = reprocess.run_reprocess(job)
        assert result["reclassified"] == 0 and result["familyAssigned"] == 0 \
            and result["expiryUpdated"] == 0
