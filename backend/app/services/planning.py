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
    """Map lower-cased product name → list of (qty, unit, expiring_soon_bool),
    ONE entry per lot. Kept per-lot (not pre-summed) so the caller can convert
    each lot into the demanded unit before adding — summing g and kg lots into a
    single number, as this used to, silently inflates on-hand by 1000x."""
    lots = db.session.query(StockLot).filter_by(group_id=gid, finished=False).all()
    agg: dict[str, list] = {}
    for s in lots:
        if not s.product:
            continue
        # Non-food consumables (foil, dishwasher tablets) never satisfy a recipe.
        if (getattr(s.product, "item_type", "food") or "food") == "consumable":
            continue
        key = s.product.name.lower()
        exp = expiry_status(s.expiry_date) in ("expiring", "expired")
        agg.setdefault(key, []).append((s.quantity or 0, s.unit, exp))
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
        # substring match so "milk" matches "Whole milk". Convert each lot into
        # the demanded unit before summing; a lot in an incompatible dimension
        # (mass vs the demanded volume) can't satisfy it and is skipped, so
        # on-hand is measured in the SAME unit as the need — no 2 kg-reads-as-2
        # blindness, no cross-dimension inflation.
        from ..services.quantity import convert
        have_qty, have_unit, expiring = 0.0, unit, False
        for key, lots in on_hand.items():
            if not (name.lower() in key or key in name.lower()):
                continue
            for qty, u, exp in lots:
                conv = convert(qty, u, unit)
                if conv is None:
                    continue  # different dimension — doesn't count toward this need
                have_qty += conv
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
