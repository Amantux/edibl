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
        items.append(
            {
                "name": name,
                "need": need,
                "unit": unit,
                "onHand": round(have_qty, 2),
                "onHandUnit": have_unit,
                "have": have_qty >= need,
                "shortfall": missing,
                "expiryConcern": expiring,
            }
        )
        if missing > 0:
            shortfall.append({"name": name, "quantity": missing, "unit": unit})
    return {
        "items": items,
        "shortfall": shortfall,
        "canMakeAll": len(shortfall) == 0,
    }


def analyze_demand_phased(gid, demand):
    """Time-phased demand reconciliation. `demand` = [{name, quantity?, unit?,
    neededBy?}]. Processes items in neededBy date order (undated last), decrementing
    a running availability ledger so each item sees what earlier items already claimed.

    Returns per-item availability with onHandAtNeed and firstShortfallDate, plus
    shortfall entries that include neededBy."""
    from datetime import date
    from ..services.quantity import convert

    on_hand = _on_hand_by_name(gid)

    # Separate dated and undated demand items
    dated_items = [
        (i, d.get("neededBy")) for i, d in enumerate(demand) if d.get("neededBy")
    ]
    undated_items = [
        (i, d.get("neededBy")) for i, d in enumerate(demand) if not d.get("neededBy")
    ]

    # Sort dated items by neededBy date
    def parse_needed_by(nb):
        if isinstance(nb, str):
            try:
                return date.fromisoformat(nb.replace("Z", "").replace("+00:00", ""))
            except ValueError:
                return None
        elif isinstance(nb, date):
            return nb
        elif hasattr(nb, "date"):
            return nb.date()
        return None

    # Sort dated items by neededBy date, then by original order for ties
    dated_items.sort(key=lambda x: (parse_needed_by(x[1]), x[0]))

    # Process order: dated items first (sorted by date), then undated items
    process_order = [i for i, _ in dated_items] + [i for i, _ in undated_items]

    # Track remaining stock per product: {lower_name: [(qty, unit, expiring), ...]}
    remaining_stock = {}
    for key, lots in on_hand.items():
        remaining_stock[key] = list(lots)

    items, shortfall = [], []

    for idx in process_order:
        d = demand[idx]
        name = (d.get("name") or "").strip()
        if not name:
            continue
        need = float(d.get("quantity") or 1)
        unit = d.get("unit") or "count"
        needed_by = d.get("neededBy")

        # Convert remaining stock to the demanded unit and track availability over time
        expiring = False
        available_lots = []  # [(remaining_qty_in_demand_unit, original_lot_unit, expiring)]

        for key, lots in remaining_stock.items():
            if not (name.lower() in key or key in name.lower()):
                continue
            for qty, u, exp in lots:
                conv = convert(qty, u, unit)
                if conv is None:
                    continue  # different dimension — doesn't count toward this need
                available_lots.append((conv, u, exp))
                expiring = expiring or exp

        # Track when shortfall occurs
        first_shortfall_date = None
        on_hand_at_need = 0.0

        # Process lots chronologically (for dated items) or all at once (undated)
        if needed_by:
            # For dated items, track availability over time
            remaining_need = need
            for conv_qty, u, exp in available_lots:
                if remaining_need <= 0:
                    break
                if conv_qty <= 0:
                    continue
                take = min(conv_qty, remaining_need)
                on_hand_at_need += take
                remaining_need -= take

            if remaining_need > 0:
                # Shortfall detected - record the date
                nb_date = parse_needed_by(needed_by)
                if nb_date:
                    first_shortfall_date = nb_date.isoformat()
        else:
            # Undated items: just sum all available
            for conv_qty, u, exp in available_lots:
                on_hand_at_need += conv_qty

        missing = round(max(need - on_hand_at_need, 0), 2)

        item_result = {
            "name": name,
            "need": need,
            "unit": unit,
            "onHand": round(on_hand_at_need, 2),
            "onHandUnit": unit,
            "have": on_hand_at_need >= need,
            "shortfall": missing,
            "expiryConcern": expiring,
        }

        # Add phased-specific fields
        if needed_by:
            item_result["onHandAtNeed"] = round(on_hand_at_need, 2)
            if first_shortfall_date:
                item_result["firstShortfallDate"] = first_shortfall_date

        items.append(item_result)

        if missing > 0:
            shortfall_entry = {"name": name, "quantity": missing, "unit": unit}
            if needed_by:
                shortfall_entry["neededBy"] = needed_by
            shortfall.append(shortfall_entry)

        # Deduct consumed stock from remaining_stock (for dated items only)
        if needed_by and on_hand_at_need > 0:
            # Deduct from available lots
            remaining_to_deduct = on_hand_at_need
            for key, lots in remaining_stock.items():
                if not (name.lower() in key or key in name.lower()):
                    continue
                for lot_info in list(lots):
                    if remaining_to_deduct <= 0:
                        break
                    qty, u, exp = lot_info
                    conv = convert(qty, u, unit)
                    if conv is None:
                        continue
                    take = min(conv, remaining_to_deduct)
                    remaining_in_lot = qty - take
                    if remaining_in_lot <= 0:
                        lots.remove(lot_info)
                    else:
                        # Update lot with remaining quantity
                        lots[lots.index(lot_info)] = (remaining_in_lot, u, exp)
                    remaining_to_deduct -= take

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
