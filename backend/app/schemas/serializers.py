"""JSON serialization with computed freshness fields."""
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from ..models import utcnow

# Days-to-expiry threshold for the "expiring soon" bucket.
EXPIRING_SOON_DAYS = 5


def to_money(x):
    """Coerce a user/JSON value to a 2dp Decimal, or None. Money is Decimal, never
    float — this is the single coercion point used at ingestion + aggregation."""
    if x is None or x == "":
        return None
    try:
        v = Decimal(str(x)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # Column is Numeric(10, 2): a negative price is nonsense and an out-of-range one
    # would raise DataError on Postgres (uncaught 500). Treat either as "no price".
    if not v.is_finite() or v < 0 or v >= Decimal("100000000"):
        return None
    return v


def money_out(x):
    """Render a Decimal money value for JSON (no Decimal JSON type). Kept as a float
    at the wire boundary only; all arithmetic upstream stays Decimal."""
    return float(x) if x is not None else None


def _unit_price(cost, quantity):
    """Price per unit = cost / quantity, as a 4dp Decimal, when both are usable
    (quantity > 0). None otherwise — presence/unknown lots have no per-unit price."""
    if cost is None or not quantity or quantity <= 0:
        return None
    try:
        return (Decimal(str(cost)) / Decimal(str(quantity))).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return None


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, (datetime, date)):
        return dt.isoformat()
    return dt


def _days_to_expiry(expiry):
    if not expiry:
        return None
    return (expiry.date() - utcnow().date()).days


def expiry_status(expiry):
    d = _days_to_expiry(expiry)
    if d is None:
        return "unknown"
    if d < 0:
        return "expired"
    if d <= EXPIRING_SOON_DAYS:
        return "expiring"
    return "fresh"


def _fmt_date(d):
    return d.strftime("%b ") + str(d.day) if d else None


def expiry_explain(s):
    """Plain-language basis for a lot's effective expiry — so estimates never
    masquerade as printed facts. e.g. 'Best by Aug 12', 'Est. good until Aug 9
    (rough — no purchase date)'."""
    d = getattr(s, "expiry_date", None)
    if not d:
        return "No expiry tracked"
    basis = getattr(s, "expiry_basis", "") or ("estimated" if s.expiry_estimated else "user")
    conf = getattr(s, "expiry_confidence", None)
    date = _fmt_date(d)
    if basis == "use_by":
        return f"Use by {date}"
    if basis == "best_by":
        return f"Best by {date}"
    if basis in ("frozen",):
        return f"Frozen — good until ~{date}"
    if basis in ("thawed",):
        return f"Thawed — use by ~{date}"
    if basis == "estimated":
        note = " (rough — no purchase date)" if (conf is not None and conf < 0.5) else ""
        return f"Est. good until {date}{note}"
    return f"Expires {date}"


def _numeric_kind(s):
    """True when the lot's amount is a real number (exact/estimated/approximate),
    False for presence/unknown (where `quantity` must not surface a value)."""
    return (getattr(s, "quantity_kind", "exact") or "exact") in (
        "exact", "estimated", "approximate")


def _quantity_text(s):
    """Natural-language amount for a lot via the Quantity value object, so
    presence/unknown never render as a misleading number."""
    from ..services.quantity import Quantity
    kind = getattr(s, "quantity_kind", "exact") or "exact"
    return Quantity(value=s.quantity, unit=s.unit, kind=kind).describe()


def event_out(ev):
    """An inventory-ledger entry, with its plain-language summary."""
    return {
        "id": ev.id, "type": ev.type, "at": iso(ev.at), "summary": ev.summary,
        "sourceApp": ev.source_app, "reason": ev.reason,
        "srcPositionId": ev.src_position_id, "dstPositionId": ev.dst_position_id,
        "deltaValue": ev.delta_value, "deltaUnit": ev.delta_unit,
        "stateChanges": ev.state_changes or {}, "provenance": ev.provenance,
        "confidence": ev.confidence, "reversalOf": ev.reversal_of,
    }


def location_out(loc, with_counts=True):
    data = {
        "id": loc.id, "name": loc.name, "kind": loc.kind, "tempC": loc.temp_c,
        "notes": loc.notes,
        "parent": {"id": loc.parent.id, "name": loc.parent.name} if loc.parent else None,
        "createdAt": iso(loc.created_at),
    }
    if with_counts:
        active = [s for s in loc.stock if not s.finished]
        data["stockCount"] = len(active)
        data["childCount"] = len(loc.children)
    return data


def product_out(p):
    from ..services.tracking import effective_tracking_mode
    return {
        "id": p.id, "name": p.name, "brand": p.brand, "category": p.category,
        "family": p.family or "",
        "barcode": p.barcode, "defaultUnit": p.default_unit,
        # Explicit tracking mode (may be "") plus the effective one the UI should use.
        "trackingMode": getattr(p, "tracking_mode", "") or "",
        "effectiveTrackingMode": effective_tracking_mode(p),
        "itemType": getattr(p, "item_type", "food") or "food",
        "conceptId": getattr(p, "concept_id", None),
        "concept": {"id": p.concept.id, "canonicalName": p.concept.canonical_name}
        if getattr(p, "concept", None) else None,
        # Replenishment policy (all optional).
        "policy": {
            "minQuantity": p.min_quantity, "targetQuantity": p.target_quantity,
            "reorderThreshold": p.reorder_threshold,
            "staple": bool(p.staple), "doNotSuggest": bool(p.do_not_suggest),
        },
        "shelfLifeDays": p.shelf_life_days, "notes": p.notes,
        "nutrition": p.nutrition or None,
        "createdAt": iso(p.created_at),
    }


def concept_out(c):
    return {
        "id": c.id, "canonicalName": c.canonical_name, "aliases": c.aliases or [],
        "classification": c.classification, "itemType": c.item_type,
        "allergens": c.allergens or [], "substitutionGroup": c.substitution_group,
        "createdAt": iso(c.created_at),
    }


def reservation_out(r):
    return {
        "id": r.id, "productId": r.product_id, "conceptId": r.concept_id,
        "name": r.name, "quantity": r.quantity, "unit": r.unit, "meal": r.meal,
        "sourceRef": r.source_ref, "neededBy": iso(r.needed_by),
        "createdAt": iso(r.created_at),
    }


def stock_out(s):
    return {
        "id": s.id,
        "product": {"id": s.product.id, "name": s.product.name,
                    "category": s.product.category, "brand": s.product.brand,
                    "family": s.product.family or "", "staple": bool(s.product.staple),
                    "nutrition": s.product.nutrition or None}
        if s.product else None,
        # Convenience grouping key: the product's family, else its name.
        "groupKey": (s.product.family or s.product.name) if s.product else "",
        "location": {"id": s.location.id, "name": s.location.name, "kind": s.location.kind}
        if s.location else None,
        # `quantity` is null for presence/unknown lots so no consumer reads a fake
        # number; `quantityText` ("some" / "unknown amount") is the human form.
        "quantity": (s.quantity if _numeric_kind(s) else None),
        "unit": s.unit, "storageMethod": s.storage_method,
        # Orthogonal package facet + how sure we are of the amount. `quantityText`
        # renders "some"/"unknown amount"/"about 2" so the UI never shows a false
        # number for a presence/unknown lot. Raw `quantity` kept for back-compat.
        "packageState": getattr(s, "package_state", "sealed") or "sealed",
        "quantityKind": getattr(s, "quantity_kind", "exact") or "exact",
        "quantityText": _quantity_text(s),
        "provenance": getattr(s, "provenance", "manual") or "manual",
        "confidence": getattr(s, "confidence", None),
        # Who added it — only surfaced for real HA users (multi-user households),
        # so a single-user/standalone install isn't cluttered with "added by Local".
        "addedBy": (s.created_by_user.name
                    if (s.created_by_user and s.created_by_user.ha_user_id) else None),
        # "freshness" is the user-facing name; "state" kept for back-compat.
        "freshness": s.state or "", "state": s.state or "",
        "purchaseDate": iso(s.purchase_date), "openedDate": iso(s.opened_date),
        "expiryDate": iso(s.expiry_date), "expiryEstimated": s.expiry_estimated,
        "bestBy": iso(getattr(s, "best_by", None)), "useBy": iso(getattr(s, "use_by", None)),
        "expiryBasis": getattr(s, "expiry_basis", "") or "",
        "expiryConfidence": getattr(s, "expiry_confidence", None),
        "expiryExplain": expiry_explain(s),
        "daysToExpiry": _days_to_expiry(s.expiry_date),
        "expiryStatus": expiry_status(s.expiry_date),
        # Money: `cost`/`price` are the same lot price (float on the wire, Decimal in
        # DB); `unitPrice` = price / amount when both are known; `currency` is the code.
        "cost": money_out(s.cost), "price": money_out(s.cost),
        "unitPrice": money_out(_unit_price(s.cost, s.quantity if _numeric_kind(s) else None)),
        "currency": getattr(s, "currency", "") or "",
        "source": s.source, "lotCode": s.lot_code,
        "finished": s.finished, "notes": s.notes, "attrs": s.attrs or {},
        "acquisitionLotId": getattr(s, "acquisition_lot_id", None),
        "acquisition": acquisition_out(s.acquisition_lot)
        if getattr(s, "acquisition_lot", None) else None,
        "createdAt": iso(s.created_at),
    }


def acquisition_out(a):
    """The batch a position came from — its purchase/production facts + lineage."""
    return {
        "id": a.id, "source": a.source, "acquiredAt": iso(a.acquired_at),
        "originalQuantity": a.original_quantity, "unit": a.unit,
        "cost": money_out(a.cost), "currency": a.currency, "lotCode": a.lot_code,
        "provenance": a.provenance, "derivedFrom": a.derived_from or {},
    }


def shopping_out(i):
    return {
        "id": i.id, "name": i.name, "quantity": i.quantity, "unit": i.unit,
        "note": i.note, "status": i.status, "source": i.source,
        "productId": i.product_id, "createdAt": iso(i.created_at),
    }
