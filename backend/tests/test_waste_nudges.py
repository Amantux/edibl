"""Waste-reduction nudges + trend on /stock/insights. Reads existing loss-outcome
ConsumptionEvents and values each loss exactly like wasteCost (unit-matched typical
price) so the figures agree. No migration."""
from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models import ConsumptionEvent, Group, Product, StockLot, User, utcnow


def _gid():
    return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _priced_product(gid, name, *, unit_price, qty=4, unit="count"):
    """A product with one priced lot establishing a typical unit price of `unit_price`."""
    p = Product(name=name, group_id=gid, default_unit=unit)
    db.session.add(p)
    db.session.flush()
    db.session.add(StockLot(product_id=p.id, group_id=gid, quantity=qty, unit=unit,
                            quantity_kind="exact", cost=Decimal(str(unit_price)) * qty))
    db.session.commit()
    return p.id


def _loss(gid, pid, *, qty=1, unit="count", outcome="spoiled", days_ago=0):
    db.session.add(ConsumptionEvent(product_id=pid, group_id=gid, quantity=qty, unit=unit,
                                    outcome=outcome, at=utcnow() - timedelta(days=days_ago)))
    db.session.commit()


def _insights(auth_client):
    return auth_client.get("/api/v1/stock/insights").get_json()


def test_repeat_waste_nudges_and_single_loss_ignored(auth_client, app):
    with app.app_context():
        gid = _gid()
        berries = _priced_product(gid, "Berries", unit_price=2.00)  # 2.00 / count
        _loss(gid, berries, qty=1)
        _loss(gid, berries, qty=1)                                  # 2 losses → nudge
        milk = _priced_product(gid, "Milk", unit_price=3.00)
        _loss(gid, milk, qty=1)                                     # 1 loss → no nudge
    ins = _insights(auth_client)
    nudges = {n["name"]: n for n in ins["wasteNudges"]}
    assert "Berries" in nudges and "Milk" not in nudges
    assert nudges["Berries"]["count"] == 2
    assert nudges["Berries"]["wastedValue"] == 4.0                  # 2 × (1 count × 2.00)
    assert nudges["Berries"]["suggestion"]                          # a non-empty tip


def test_wasted_value_matches_wastecost(auth_client, app):
    # Losses on a single product → the nudge's wastedValue must equal the headline
    # wasteCost, proving both use the same unit-matched valuation.
    with app.app_context():
        gid = _gid()
        pid = _priced_product(gid, "Yogurt", unit_price=1.50)
        _loss(gid, pid, qty=2)
        _loss(gid, pid, qty=1)                                      # 3 count → 4.50
    ins = _insights(auth_client)
    assert ins["wasteCost"] == 4.5
    assert ins["wasteNudges"][0]["wastedValue"] == 4.5


def test_unit_mismatch_counts_but_not_valued(auth_client, app):
    # A loss in a unit that doesn't match the priced unit still counts toward the
    # repeat-waste count, but adds no bogus value — same rule as wasteCost.
    with app.app_context():
        gid = _gid()
        pid = _priced_product(gid, "Cheese", unit_price=2.00, unit="count")
        _loss(gid, pid, qty=100, unit="g")
        _loss(gid, pid, qty=100, unit="g")
    ins = _insights(auth_client)
    n = ins["wasteNudges"][0]
    assert n["count"] == 2 and n["wastedValue"] == 0.0
    assert ins["wasteCost"] == 0.0


def test_trend_windowing_and_shape(auth_client, app):
    with app.app_context():
        gid = _gid()
        pid = _priced_product(gid, "Bread", unit_price=1.00)
        _loss(gid, pid, qty=1, days_ago=100)   # in the 6-month trend, OUTSIDE the 90d nudge window
        _loss(gid, pid, qty=3, days_ago=1)
        _loss(gid, pid, qty=3, days_ago=2)     # recent
    ins = _insights(auth_client)
    tr = ins["wasteTrend"]
    assert len(tr["months"]) == 6
    assert sum(m["value"] or 0 for m in tr["months"]) >= 6.0        # all 3 losses valued in trend
    assert tr["direction"] in ("up", "down", "flat")
    # the 100-day-old loss is outside the 90-day nudge window → count is the 2 recent only
    assert ins["wasteNudges"][0]["count"] == 2


def test_empty_when_no_losses(auth_client, app):
    with app.app_context():
        gid = _gid()
        _priced_product(gid, "Rice", unit_price=1.00)              # stock, no losses
    ins = _insights(auth_client)
    assert ins["wasteNudges"] == []
    assert ins["wasteCost"] == 0.0


def test_group_scoped(auth_client, app):
    with app.app_context():
        gid = _gid()
        mine = _priced_product(gid, "MyBerries", unit_price=2.00)
        _loss(gid, mine, qty=1)
        _loss(gid, mine, qty=1)
        other = Group(name="Other")
        db.session.add(other)
        db.session.flush()
        theirs = _priced_product(other.id, "TheirMilk", unit_price=5.00)
        _loss(other.id, theirs, qty=1)
        _loss(other.id, theirs, qty=1)
    ins = _insights(auth_client)
    names = {n["name"] for n in ins["wasteNudges"]}
    assert "MyBerries" in names and "TheirMilk" not in names
