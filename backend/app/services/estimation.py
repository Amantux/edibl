"""Shelf-life estimation + runout prediction.

Expiry estimation: when a lot has a purchase date but no printed expiry, estimate
it from typical shelf life for (category, storage_method). Precedence:
  product.shelf_life_days  →  DB ShelfLifeProfile  →  DEFAULT_SHELF_LIFE.
Vacuum-sealing + freezing dramatically extend life — that's why the meat
butchering workflow (vacuum_sealed + frozen) yields long expiries.
"""
from datetime import timedelta

from ..extensions import db
from ..models import ShelfLifeProfile, ConsumptionEvent, utcnow

# (category, storage_method) -> typical days. Conservative, food-safety-ish
# defaults; users can override per product or edit the DB table.
DEFAULT_SHELF_LIFE = {
    ("produce", "refrigerated"): 7, ("produce", "fresh"): 4, ("produce", "frozen"): 240,
    ("dairy", "refrigerated"): 10, ("dairy", "opened"): 5, ("dairy", "frozen"): 90,
    ("meat", "refrigerated"): 3, ("meat", "frozen"): 180, ("meat", "vacuum_sealed"): 730,
    ("seafood", "refrigerated"): 2, ("seafood", "frozen"): 180, ("seafood", "vacuum_sealed"): 365,
    ("bakery", "pantry"): 4, ("bakery", "frozen"): 90,
    ("frozen", "frozen"): 300,
    ("beverage", "refrigerated"): 21, ("beverage", "pantry"): 180,
    ("wine", "wine_cellar"): 3650, ("wine", "pantry"): 730,
    ("spirits", "pantry"): 3650, ("beer", "refrigerated"): 120,
    ("dry_goods", "pantry"): 365, ("condiment", "refrigerated"): 180,
    ("condiment", "pantry"): 365, ("snack", "pantry"): 120, ("other", "refrigerated"): 14,
    ("other", "pantry"): 90, ("other", "frozen"): 180,
}

DEFAULT_SHELF_LIFE_ROWS = [
    {"category": c, "storage_method": s, "typical_days": d}
    for (c, s), d in DEFAULT_SHELF_LIFE.items()
]


def typical_days(category: str, storage_method: str):
    """Look up typical shelf life: DB profile first, then the built-in table."""
    row = (
        db.session.query(ShelfLifeProfile)
        .filter_by(category=category, storage_method=storage_method)
        .first()
    )
    if row:
        return row.typical_days
    return DEFAULT_SHELF_LIFE.get((category, storage_method))


def estimate_expiry(purchase_date, category, storage_method, product_shelf_life=None):
    """Return (expiry_date, estimated_bool) or (None, False) if not estimable."""
    if not purchase_date:
        return None, False
    days = product_shelf_life or typical_days(category, storage_method)
    if not days:
        return None, False
    return purchase_date + timedelta(days=int(days)), True


def predict_runout(group_id, product_id, on_hand):
    """Estimate days until a product runs out from its recent consumption rate.

    Returns (days_left, daily_rate) or (None, None) with too little history.
    Uses the last 60 days of consumption events for the product.
    """
    if on_hand <= 0:
        return 0.0, None
    since = utcnow() - timedelta(days=60)
    events = (
        db.session.query(ConsumptionEvent)
        .filter(
            ConsumptionEvent.group_id == group_id,
            ConsumptionEvent.product_id == product_id,
            ConsumptionEvent.at >= since,
        )
        .all()
    )
    total = sum(e.quantity or 0 for e in events)
    if total <= 0 or len(events) < 2:
        return None, None
    # Span from the first event to now, at least 1 day to avoid div-by-zero.
    span_days = max((utcnow() - min(e.at for e in events)).days, 1)
    daily = total / span_days
    if daily <= 0:
        return None, None
    return round(on_hand / daily, 1), round(daily, 3)
