"""Policy-driven reorder suggestions — what to buy from per-product min/target/
threshold policies, accounting for reserved stock and honest about unknown amounts.
Shared by the shopping API and the Home Assistant sensor feed."""
from ..extensions import db
from ..models import Product, Reservation

_NUMERIC = ("exact", "estimated", "approximate")


def _reserved_by_product(gid):
    out = {}
    for r in db.session.query(Reservation).filter_by(group_id=gid).all():
        if r.product_id:
            out[r.product_id] = out.get(r.product_id, 0) + (r.quantity or 0)
    return out


def reorder_state(p, reserved=0.0):
    """Single-product reorder computation — the one source of truth for "is this at/
    below its reorder level", shared by the suggestions list AND the staple auto-
    restock sync. Returns a dict (with `isLow`) or None when the product has no policy
    (not a staple and no threshold set). Threshold = reorder_threshold, else
    min_quantity, else 1 for a staple. Non-numeric (presence/unknown) stock is flagged,
    not counted."""
    threshold = p.reorder_threshold if p.reorder_threshold is not None else p.min_quantity
    if threshold is None and not p.staple:
        return None
    lots = [s for s in p.stock if not s.finished]
    numeric = [s for s in lots if (s.quantity_kind or "exact") in _NUMERIC]
    on_hand = round(sum(s.quantity or 0 for s in numeric), 4)
    available = round(on_hand - (reserved or 0), 4)
    thr = threshold if threshold is not None else 1
    target = p.target_quantity if p.target_quantity is not None else (thr or 1)
    need = round(max(target - available, thr - available, 1), 4)
    return {
        "productId": p.id, "name": p.name, "unit": p.default_unit,
        "onHand": on_hand, "reserved": (reserved or 0), "available": available,
        "threshold": thr, "suggestedQuantity": need, "staple": bool(p.staple),
        "uncertain": (len(lots) - len(numeric)) > 0, "isLow": available <= thr,
    }


def reorder_suggestions(gid):
    """Ranked list of {productId, name, unit, onHand, reserved, available,
    threshold, suggestedQuantity, staple, uncertain} — items at/below their
    reorder level. Non-numeric (presence/unknown) stock is flagged, not counted."""
    reserved = _reserved_by_product(gid)
    out = []
    for p in db.session.query(Product).filter_by(group_id=gid).all():
        if p.do_not_suggest:
            continue
        st = reorder_state(p, reserved.get(p.id, 0))
        if st is None or not st["isLow"]:
            continue
        st.pop("isLow")
        out.append(st)
    out.sort(key=lambda s: (s["available"], s["name"].lower()))
    return out
