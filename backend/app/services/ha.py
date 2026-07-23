"""Home Assistant integration: a flat sensor-friendly snapshot of the kitchen, and
a push to HA's persistent-notification service.

The snapshot is what the Edibl HACS integration (or an HA `rest` sensor) polls to
create entities like sensor.edibl_expiring_soon. The notify helper lets an HA
automation ("every day at 17:00") tell you what's expiring / to restock.
"""
import logging
import os

from ..extensions import db
from ..models import StockLot
from ..schemas.serializers import expiry_status, _days_to_expiry
from .reorder import reorder_suggestions

_LOGGER = logging.getLogger("edibl.ha")


def _active(gid):
    return db.session.query(StockLot).filter_by(group_id=gid, finished=False).all()


def sensor_snapshot(gid):
    """Flat metrics + small attribute lists, shaped for HA sensors."""
    lots = _active(gid)
    expiring, expired, open_pkgs = [], 0, 0
    soonest = None
    for s in lots:
        st = expiry_status(s.expiry_date)
        if (s.package_state or "sealed") == "opened":
            open_pkgs += 1
        if st == "expired":
            expired += 1
        if st in ("expiring", "expired"):
            d = _days_to_expiry(s.expiry_date)
            expiring.append({"name": s.product.name if s.product else "item",
                             "days": d, "location": s.location.name if s.location else None})
            if d is not None and (soonest is None or d < soonest["days"]):
                soonest = {"days": d, "name": s.product.name if s.product else "item"}
    expiring.sort(key=lambda e: (e["days"] is None, e["days"] if e["days"] is not None else 9999))
    restock = reorder_suggestions(gid)

    bits = []
    if len(expiring):
        bits.append(f"{len(expiring)} expiring")
    if expired:
        bits.append(f"{expired} expired")
    if restock:
        bits.append(f"{len(restock)} to restock")
    attention = " · ".join(bits) or "All good"

    return {
        "items_in_stock": len(lots),
        "expiring_soon": len(expiring),
        "expired": expired,
        "open_packages": open_pkgs,
        "to_restock": len(restock),
        "next_expiry_days": soonest["days"] if soonest else None,
        "next_expiry_item": soonest["name"] if soonest else None,
        "attention": attention,
        "expiring": expiring[:15],
        "restock": [{"name": r["name"], "suggested": r["suggestedQuantity"],
                     "unit": r["unit"]} for r in restock[:15]],
    }


def notification_message(snap):
    """Human summary for a persistent notification. None if nothing to report."""
    lines = []
    if snap["expiring_soon"]:
        top = ", ".join(f"{e['name']}"
                        + (f" ({e['days']}d)" if e["days"] is not None else "")
                        for e in snap["expiring"][:6])
        lines.append(f"⏰ Use soon: {top}")
    if snap["to_restock"]:
        top = ", ".join(f"{r['name']}" for r in snap["restock"][:6])
        lines.append(f"🛒 Restock: {top}")
    return "\n".join(lines) or None


def push_notification(gid, *, title="Edibl kitchen"):
    """Create/update a persistent notification in Home Assistant via the Supervisor-
    proxied core API (needs homeassistant_api). Returns {sent, reason}."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {"sent": False, "reason": "not running under the Home Assistant Supervisor"}
    snap = sensor_snapshot(gid)
    msg = notification_message(snap)
    if not msg:
        return {"sent": False, "reason": "nothing to report"}
    import httpx
    try:
        with httpx.Client(base_url="http://supervisor", timeout=8.0,
                          headers={"Authorization": f"Bearer {token}"}) as c:
            r = c.post("/core/api/services/persistent_notification/create",
                       json={"title": title, "message": msg, "notification_id": "edibl_kitchen"})
            r.raise_for_status()
        return {"sent": True, "message": msg}
    except Exception as e:  # noqa: BLE001 — best-effort
        _LOGGER.warning("HA notify failed: %s", e)
        return {"sent": False, "reason": str(e)}
