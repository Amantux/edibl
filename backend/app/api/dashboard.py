from flask import Blueprint, request, jsonify

from ..extensions import db
from ..models import StockLot, Product, Location
from ..auth import login_required, current_group
from ..schemas.serializers import stock_out, expiry_status
from ..services.estimation import predict_runout, waste_insights

bp = Blueprint("dashboard", __name__)


def _active(gid):
    return db.session.query(StockLot).filter_by(group_id=gid, finished=False).all()


@bp.get("/dashboard")
@login_required
def dashboard():
    gid = current_group().id
    lots = _active(gid)
    buckets = {"fresh": 0, "expiring": 0, "expired": 0, "unknown": 0}
    by_category, by_location = {}, {}
    total_value = 0.0
    open_pkgs = 0
    for s in lots:
        buckets[expiry_status(s.expiry_date)] += 1
        if (s.package_state or "sealed") == "opened":
            open_pkgs += 1
        cat = s.product.category if s.product else "other"
        by_category[cat] = by_category.get(cat, 0) + 1
        loc = s.location.name if s.location else "Unassigned"
        by_location[loc] = by_location.get(loc, 0) + 1
        total_value += (s.cost or 0)
    expiring = sorted(
        [s for s in lots if expiry_status(s.expiry_date) in ("expiring", "expired")],
        key=lambda s: (s.expiry_date or s.created_at),
    )
    return jsonify({
        "totals": {
            "lots": len(lots),
            "products": db.session.query(Product).filter_by(group_id=gid).count(),
            "locations": db.session.query(Location).filter_by(group_id=gid).count(),
            "value": round(total_value, 2),
            "open": open_pkgs,
            **buckets,
        },
        "byCategory": by_category, "byLocation": by_location,
        "expiring": [stock_out(s) for s in expiring[:20]],
    })


@bp.get("/dashboard/expiring")
@login_required
def expiring():
    days = int(request.args.get("days", 5) or 5)
    lots = _active(current_group().id)
    out = []
    for s in lots:
        st = expiry_status(s.expiry_date)
        if st == "expired" or (st == "expiring" and (s.expiry_date is not None)):
            dt = stock_out(s)
            if dt["daysToExpiry"] is None or dt["daysToExpiry"] <= days:
                out.append(dt)
    out.sort(key=lambda d: (d["daysToExpiry"] if d["daysToExpiry"] is not None else 9999))
    return jsonify({"items": out, "total": len(out)})


@bp.get("/dashboard/runout")
@login_required
def runout():
    """Products predicted to run out soon, from consumption rate."""
    gid = current_group().id
    on_hand = {}
    for s in _active(gid):
        on_hand[s.product_id] = on_hand.get(s.product_id, 0) + (s.quantity or 0)
    out = []
    for pid, qty in on_hand.items():
        days_left, daily = predict_runout(gid, pid, qty)
        if days_left is None:
            continue
        p = db.session.get(Product, pid)
        out.append({"product": {"id": pid, "name": p.name if p else "?",
                                "category": p.category if p else "other"},
                    "onHand": round(qty, 2), "daysLeft": days_left, "dailyRate": daily})
    out.sort(key=lambda d: d["daysLeft"])
    return jsonify({"items": out, "total": len(out)})


@bp.get("/dashboard/wine")
@login_required
def wine():
    """Specialty view: wine / spirits / beer lots with their attrs."""
    lots = [s for s in _active(current_group().id)
            if s.product and s.product.category in ("wine", "spirits", "beer")]
    lots.sort(key=lambda s: (s.product.category, (s.attrs or {}).get("vintage") or ""))
    return jsonify({"items": [stock_out(s) for s in lots], "total": len(lots)})


@bp.get("/dashboard/freezer")
@login_required
def freezer():
    """Specialty view: frozen + vacuum-sealed lots (the meat/long-term workflow)."""
    lots = [s for s in _active(current_group().id)
            if s.storage_method in ("frozen", "vacuum_sealed")]
    lots.sort(key=lambda s: (s.attrs or {}).get("butcherSession") or "")
    return jsonify({"items": [stock_out(s) for s in lots], "total": len(lots)})


@bp.get("/dashboard/lifecycle")
@login_required
def lifecycle():
    """What you tend to waste, learned from consumption outcomes — the basis for
    'you lose bananas often, buy fewer' style personalized suggestions."""
    return jsonify({"items": waste_insights(current_group().id)})


@bp.get("/have")
@login_required
def have():
    """Ingredient query for myMeal / the MCP server: 'do I have X, how much, where?'
    Matches product/lot names, returns on-hand totals + locations."""
    ingredient = (request.args.get("ingredient") or request.args.get("q") or "").strip()
    if not ingredient:
        return jsonify({"error": "ingredient/q required"}), 422
    like = ingredient.lower()
    matches = [s for s in _active(current_group().id)
               if s.product and like in s.product.name.lower()]
    total = sum(s.quantity or 0 for s in matches)
    locations = sorted({s.location.name for s in matches if s.location})
    return jsonify({
        "ingredient": ingredient, "have": total > 0, "onHand": round(total, 2),
        "locations": locations, "lots": [stock_out(s) for s in matches],
    })
