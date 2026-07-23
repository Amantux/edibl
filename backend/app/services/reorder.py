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


def reorder_suggestions(gid):
    """Ranked list of {productId, name, unit, onHand, reserved, available,
    threshold, suggestedQuantity, staple, uncertain} — items at/below their
    reorder level. Non-numeric (presence/unknown) stock is flagged, not counted."""
    reserved = _reserved_by_product(gid)
    out = []
    for p in db.session.query(Product).filter_by(group_id=gid).all():
        if p.do_not_suggest:
            continue
        threshold = p.reorder_threshold if p.reorder_threshold is not None else p.min_quantity
        if threshold is None and not p.staple:
            continue
        lots = [s for s in p.stock if not s.finished]
        numeric = [s for s in lots if (s.quantity_kind or "exact") in _NUMERIC]
        on_hand = round(sum(s.quantity or 0 for s in numeric), 4)
        available = round(on_hand - reserved.get(p.id, 0), 4)
        thr = threshold if threshold is not None else 1
        if available > thr:
            continue
        target = p.target_quantity if p.target_quantity is not None else (thr or 1)
        need = round(max(target - available, thr - available, 1), 4)
        out.append({
            "productId": p.id, "name": p.name, "unit": p.default_unit,
            "onHand": on_hand, "reserved": reserved.get(p.id, 0),
            "available": available, "threshold": thr, "suggestedQuantity": need,
            "staple": bool(p.staple), "uncertain": (len(lots) - len(numeric)) > 0,
        })
    out.sort(key=lambda s: (s["available"], s["name"].lower()))
    return out
