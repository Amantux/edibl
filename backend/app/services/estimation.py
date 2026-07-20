"""Shelf-life estimation + runout prediction.

Expiry estimation: when a lot has a purchase date but no printed expiry, estimate
it from typical shelf life for (category, storage_method). Precedence:
  product.shelf_life_days  →  DB ShelfLifeProfile  →  DEFAULT_SHELF_LIFE.
Vacuum-sealing + freezing dramatically extend life — that's why the meat
butchering workflow (vacuum_sealed + frozen) yields long expiries.
"""
from datetime import datetime, timedelta
from statistics import median

from ..extensions import db
from ..models import (ShelfLifeProfile, ConsumptionEvent, Product,
                      GOOD_OUTCOMES, LOSS_OUTCOMES, utcnow)

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


# Storage-only fallback (days) — used for user-defined categories with no
# category-specific profile, so custom categories still get a sane estimate.
STORAGE_FALLBACK = {
    "fresh": 5, "refrigerated": 10, "frozen": 240, "vacuum_sealed": 365,
    "pantry": 180, "opened": 5,
}


def typical_days(category: str, storage_method: str):
    """Look up typical shelf life: DB profile → built-in (category, storage) table
    → storage-only fallback (so free-form categories still estimate)."""
    row = (
        db.session.query(ShelfLifeProfile)
        .filter_by(category=category, storage_method=storage_method)
        .first()
    )
    if row:
        return row.typical_days
    days = DEFAULT_SHELF_LIFE.get((category, storage_method))
    if days is not None:
        return days
    return STORAGE_FALLBACK.get(storage_method)


def _outcome(e):
    """Normalized outcome for a consumption event, tolerating legacy rows that
    only set `reason` (used/expired/discarded)."""
    o = (getattr(e, "outcome", None) or "").strip()
    if o:
        return o
    r = (e.reason or "used").strip()
    return "eaten" if r == "used" else r


def learned_shelf_life(group_id, product_id):
    """Personalized shelf life (days) for a product, learned from how long its
    lots actually lasted before they *went bad* (spoiled/expired/discarded).

    We only shorten based on losses — if you always eat something before it turns,
    we don't second-guess the default. Returns (days, sample_count) or (None, 0).
    """
    if not product_id:
        return None, 0
    events = (
        db.session.query(ConsumptionEvent)
        .filter(ConsumptionEvent.group_id == group_id,
                ConsumptionEvent.product_id == product_id,
                ConsumptionEvent.days_kept.isnot(None))
        .all()
    )
    losses = [int(e.days_kept) for e in events
              if _outcome(e) in LOSS_OUTCOMES and e.days_kept is not None and e.days_kept >= 0]
    if len(losses) < 2:
        return None, len(losses)
    return int(round(median(losses))), len(losses)


def estimate_expiry(purchase_date, category, storage_method, product_shelf_life=None,
                    group_id=None, product_id=None):
    """Return (expiry_date, estimated_bool). Precedence: explicit per-product
    override → learned-from-history → category/storage table."""
    if not purchase_date:
        return None, False
    days = product_shelf_life
    if not days and group_id and product_id:
        days, _n = learned_shelf_life(group_id, product_id)
    if not days:
        days = typical_days(category, storage_method)
    if not days:
        return None, False
    return purchase_date + timedelta(days=int(days)), True


def product_insights(group_id, product_id):
    """Per-item lifecycle summary + a plain-language suggestion. Drives the
    'your bananas usually last ~5 days' style hints."""
    p = db.session.get(Product, product_id) if product_id else None
    events = (
        db.session.query(ConsumptionEvent)
        .filter(ConsumptionEvent.group_id == group_id,
                ConsumptionEvent.product_id == product_id)
        .all()
    ) if product_id else []
    good = [e for e in events if _outcome(e) in GOOD_OUTCOMES]
    loss = [e for e in events if _outcome(e) in LOSS_OUTCOMES]
    kept = [int(e.days_kept) for e in events if e.days_kept is not None and e.days_kept >= 0]
    learned, n_loss = learned_shelf_life(group_id, product_id)
    total = len(events)
    waste_rate = round(len(loss) / total, 2) if total else 0.0
    name = p.name if p else "this item"

    suggestion = ""
    if learned:
        suggestion = f"Based on your history, {name} tends to last about {learned} days here."
        if n_loss >= 3 and waste_rate >= 0.4:
            suggestion += " You lose it fairly often — buy less at a time or store it colder."
    elif len(loss) >= 2:
        suggestion = f"You've lost {name} {len(loss)}× — consider smaller quantities."
    elif good:
        suggestion = f"You usually finish {name} in time. 👍"

    return {
        "productId": product_id,
        "productName": name,
        "events": total,
        "eaten": len(good),
        "wasted": len(loss),
        "wasteRate": waste_rate,
        "avgDaysKept": round(sum(kept) / len(kept), 1) if kept else None,
        "learnedShelfLifeDays": learned,
        "lossSamples": n_loss,
        "suggestion": suggestion,
    }


def waste_insights(group_id, limit=6):
    """Group-wide 'what am I wasting' feed for the dashboard: the products with the
    most losses, each with a suggestion."""
    rows = (
        db.session.query(ConsumptionEvent)
        .filter(ConsumptionEvent.group_id == group_id)
        .all()
    )
    by_product = {}
    for e in rows:
        if not e.product_id:
            continue
        by_product.setdefault(e.product_id, []).append(e)
    out = []
    for pid, evs in by_product.items():
        loss = [e for e in evs if _outcome(e) in LOSS_OUTCOMES]
        if not loss:
            continue
        out.append(product_insights(group_id, pid))
    out.sort(key=lambda d: (d["wasted"], d["wasteRate"]), reverse=True)
    return out[:limit]


def predict_runout(group_id, product_id, on_hand):
    """Estimate days until a product runs out from its recent consumption rate.

    Returns (days_left, daily_rate) or (None, None) with too little history.
    Uses the last 60 days of consumption events for the product, and only counts
    food that was actually *eaten* (not thrown out) toward the burn rate.
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
    events = [e for e in events if _outcome(e) in GOOD_OUTCOMES]
    total = sum(e.quantity or 0 for e in events)
    if total <= 0 or len(events) < 2:
        return None, None
    # Span from the first event to now, at least 1 day to avoid div-by-zero.
    # Stored timestamps come back tz-naive from SQLite, so compare naive-to-naive.
    def _naive(dt):
        return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt
    span_days = max((datetime.utcnow() - min(_naive(e.at) for e in events)).days, 1)
    daily = total / span_days
    if daily <= 0:
        return None, None
    return round(on_hand / daily, 1), round(daily, 3)
