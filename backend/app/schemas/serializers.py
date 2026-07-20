"""JSON serialization with computed freshness fields."""
from datetime import datetime, date

from ..models import utcnow

# Days-to-expiry threshold for the "expiring soon" bucket.
EXPIRING_SOON_DAYS = 5


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
    return {
        "id": p.id, "name": p.name, "brand": p.brand, "category": p.category,
        "barcode": p.barcode, "defaultUnit": p.default_unit,
        "shelfLifeDays": p.shelf_life_days, "notes": p.notes,
        "createdAt": iso(p.created_at),
    }


def stock_out(s):
    return {
        "id": s.id,
        "product": {"id": s.product.id, "name": s.product.name,
                    "category": s.product.category, "brand": s.product.brand}
        if s.product else None,
        "location": {"id": s.location.id, "name": s.location.name, "kind": s.location.kind}
        if s.location else None,
        "quantity": s.quantity, "unit": s.unit, "storageMethod": s.storage_method,
        "purchaseDate": iso(s.purchase_date), "openedDate": iso(s.opened_date),
        "expiryDate": iso(s.expiry_date), "expiryEstimated": s.expiry_estimated,
        "daysToExpiry": _days_to_expiry(s.expiry_date),
        "expiryStatus": expiry_status(s.expiry_date),
        "cost": s.cost, "source": s.source, "lotCode": s.lot_code,
        "finished": s.finished, "notes": s.notes, "attrs": s.attrs or {},
        "createdAt": iso(s.created_at),
    }


def shopping_out(i):
    return {
        "id": i.id, "name": i.name, "quantity": i.quantity, "unit": i.unit,
        "note": i.note, "status": i.status, "source": i.source,
        "productId": i.product_id, "createdAt": iso(i.created_at),
    }
