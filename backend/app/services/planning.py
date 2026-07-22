"""Demand ↔ inventory reconciliation.

myMeal knows the *recipes* (what you plan to cook); Edibl knows the *lay of the
land* (what's actually on hand, where, and how fresh). This matches a list of
required ingredients against current stock and reports, per ingredient:
on-hand, needed, shortfall, and any expiry concern — the basis for "what should
I order?" and "what can I make?".
"""
from ..extensions import db
from ..models import StockLot, Product
from ..schemas.serializers import expiry_status


def _on_hand_by_name(gid):
    """Map lower-cased product name → (total_qty, unit, expiring_soon_bool)."""
    lots = db.session.query(StockLot).filter_by(group_id=gid, finished=False).all()
    agg = {}
    for s in lots:
        if not s.product:
            continue
        # Non-food consumables (foil, dishwasher tablets) never satisfy a recipe.
        if (getattr(s.product, "item_type", "food") or "food") == "consumable":
            continue
        key = s.product.name.lower()
        qty, unit, exp = agg.get(key, (0.0, s.unit, False))
        exp = exp or expiry_status(s.expiry_date) in ("expiring", "expired")
        agg[key] = (qty + (s.quantity or 0), s.unit, exp)
    return agg


def analyze_demand(gid, demand):
    """`demand` = [{name, quantity?, unit?}]. Returns per-item availability plus a
    consolidated shortfall list (what to order)."""
    on_hand = _on_hand_by_name(gid)
    items, shortfall = [], []
    for d in demand:
        name = (d.get("name") or "").strip()
        if not name:
            continue
        need = float(d.get("quantity") or 1)
        unit = d.get("unit") or "count"
        # substring match so "milk" matches "Whole milk".
        have_qty, have_unit, expiring = 0.0, unit, False
        for key, (qty, u, exp) in on_hand.items():
            if name.lower() in key or key in name.lower():
                have_qty += qty
                have_unit = u
                expiring = expiring or exp
        missing = round(max(need - have_qty, 0), 2)
        items.append({
            "name": name, "need": need, "unit": unit,
            "onHand": round(have_qty, 2), "onHandUnit": have_unit,
            "have": have_qty >= need, "shortfall": missing,
            "expiryConcern": expiring,
        })
        if missing > 0:
            shortfall.append({"name": name, "quantity": missing, "unit": unit})
    return {
        "items": items,
        "shortfall": shortfall,
        "canMakeAll": len(shortfall) == 0,
    }


def demand_from_products(gid, product_ids):
    """Helper for recipe checks that reference Edibl product ids."""
    out = []
    for pid in product_ids:
        p = db.session.get(Product, pid)
        if p and p.group_id == gid:
            out.append({"name": p.name, "quantity": 1, "unit": p.default_unit})
    return out
