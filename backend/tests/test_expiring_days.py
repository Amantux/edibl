"""GET /dashboard/expiring?days=N must honour N, including N > 5.

The handler filtered through expiry_status() first, which only calls a lot
"expiring" within a hardcoded 5-day window — so ?days=14 silently dropped a lot
10 days out. The window is the caller's N, not a constant.
"""
from datetime import timedelta

from app.extensions import db
from app.models import Group, Product, StockLot, utcnow


def _seed(app, offsets):
    with app.app_context():
        gid = db.session.query(Group).first().id
        p = Product(name="Yogurt", category="dairy", group_id=gid)
        db.session.add(p)
        db.session.flush()
        for i, off in enumerate(offsets):
            db.session.add(StockLot(
                product_id=p.id, quantity=1, unit="count", group_id=gid,
                finished=False, expiry_date=utcnow() + timedelta(days=off),
                lot_code=f"L{i}"))
        db.session.commit()


def test_days_14_includes_a_lot_10_days_out(auth_client, app):
    _seed(app, [3, 10, 30, -2])   # expired, soon, mid, far
    r = auth_client.get("/api/v1/dashboard/expiring?days=14").get_json()
    days = sorted(i["daysToExpiry"] for i in r["items"])
    assert days == [-2, 3, 10], f"days=14 returned {days}"


def test_days_5_default_window_still_tight(auth_client, app):
    _seed(app, [3, 10, -2])
    r = auth_client.get("/api/v1/dashboard/expiring?days=5").get_json()
    days = sorted(i["daysToExpiry"] for i in r["items"])
    assert days == [-2, 3], f"days=5 returned {days}"


def test_bad_days_falls_back_not_500(auth_client, app):
    _seed(app, [3])
    r = auth_client.get("/api/v1/dashboard/expiring?days=abc")
    assert r.status_code == 200
