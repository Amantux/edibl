from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, request, jsonify

from ..extensions import db
from ..models import StockLot, Product, Location, ConsumptionEvent, LOSS_OUTCOMES, utcnow
from ..auth import login_required, current_group
from ..schemas.serializers import stock_out, expiry_status, money_out
from ..services.estimation import predict_runout, waste_insights
from ..services.settings import get_currency

bp = Blueprint("dashboard", __name__)


def _active(gid):
    return db.session.query(StockLot).filter_by(group_id=gid, finished=False).all()


def _month_key(dt):
    return dt.strftime("%Y-%m") if dt else None


def _typical_unit_prices(gid):
    """Per product: (typical_unit_price, last_unit_price, last_price) as Decimals,
    learned from that product's priced lots. Typical = mean of known unit prices;
    used to value waste and prefill re-adds."""
    lots = (db.session.query(StockLot)
            .filter(StockLot.group_id == gid, StockLot.cost.isnot(None))
            .order_by(StockLot.purchase_date.is_(None).asc(),
                      StockLot.purchase_date.asc(), StockLot.created_at.asc()).all())
    from ..services.units import canonical_unit
    unit_prices, last_unit, last_price, price_unit = defaultdict(list), {}, {}, {}
    for s in lots:
        last_price[s.product_id] = Decimal(str(s.cost))
        if s.quantity and s.quantity > 0:
            up = (Decimal(str(s.cost)) / Decimal(str(s.quantity)))
            unit_prices[s.product_id].append(up)
            last_unit[s.product_id] = up
            price_unit[s.product_id] = canonical_unit(s.unit)  # unit this price is PER
    typical = {}
    for pid, ups in unit_prices.items():
        typical[pid] = (sum(ups) / len(ups)).quantize(Decimal("0.0001"))
    return typical, last_unit, last_price, price_unit


_WASTE_WINDOW_DAYS = 90     # repeat-waste nudges look back this far
_WASTE_TREND_MONTHS = 6     # wasted-value trend length


def _waste_analysis(gid, events, typical, last_unit, price_unit, products, now):
    """Repeat-waste nudges (last 90d, per product) + a 6-month wasted-value trend.

    Each loss event is valued EXACTLY like wasteCost — the product's typical unit
    price, counted only when the event's unit matches the priced unit — so the nudge
    figures and the headline wasteCost never disagree. An unvalued loss (no price /
    unit mismatch) still counts toward the repeat-waste *count*, just not its value.
    Returns (nudges, trend). `events` is the group's loss-outcome events.
    """
    from ..services.units import canonical_unit
    currency = get_currency(gid)

    def value_of(ev):
        up = typical.get(ev.product_id) or last_unit.get(ev.product_id)
        if (up is not None and ev.quantity
                and canonical_unit(ev.unit) == price_unit.get(ev.product_id)):
            return up * Decimal(str(ev.quantity))
        return Decimal("0")

    # ---- Repeat-waste nudges (last 90 days, grouped by product) ----
    cutoff = now - timedelta(days=_WASTE_WINDOW_DAYS)
    per = {}
    for ev in events:
        if not ev.at or ev.at < cutoff or not ev.product_id:
            continue
        d = per.setdefault(ev.product_id, {"count": 0, "value": Decimal("0"), "expired": 0})
        d["count"] += 1
        d["value"] += value_of(ev)
        if ev.outcome == "expired":
            d["expired"] += 1

    nudges = []
    for pid, d in per.items():
        if d["count"] < 2:
            continue  # a one-off isn't a pattern
        p = products.get(pid)
        name = p.name if p else "This item"
        low = name[:1].lower() + name[1:] if name else "it"
        if d["expired"] * 2 >= d["count"]:      # mostly expiry-driven
            suggestion = f"{name} keeps expiring — freeze it or buy less at a time."
        elif d["count"] >= 3:                    # frequent small losses
            suggestion = f"Wasted {low} {d['count']}× lately — try a smaller pack or freeze sooner."
        else:                                    # two losses — nudge toward planning
            suggestion = f"Buy {low} to a plan so it gets used in time."
        nudges.append({
            "productId": pid, "name": name, "count": d["count"],
            "wastedValue": money_out(d["value"].quantize(Decimal("0.01"))),
            "currency": currency, "suggestion": suggestion,
        })
    nudges.sort(key=lambda n: ((n["wastedValue"] or 0), n["count"]), reverse=True)
    nudges = nudges[:5]

    # ---- Dense 6-month wasted-value trend (oldest → newest) ----
    by_month = defaultdict(lambda: Decimal("0"))
    for ev in events:
        if ev.at:
            by_month[_month_key(ev.at)] += value_of(ev)
    trend, cur = [], now.replace(day=1)
    for _ in range(_WASTE_TREND_MONTHS):
        mk = _month_key(cur)
        trend.append({"month": mk, "value": money_out(by_month.get(mk, Decimal("0")))})
        cur = (cur.replace(day=1) - timedelta(days=1)).replace(day=1)
    trend.reverse()
    half = _WASTE_TREND_MONTHS // 2
    prior = sum((t["value"] or 0) for t in trend[:half])
    recent = sum((t["value"] or 0) for t in trend[half:])
    if recent > prior * 1.05:
        direction = "up"
    elif recent < prior * 0.95:
        direction = "down"
    else:
        direction = "flat"
    return nudges, {"months": trend, "direction": direction}


@bp.get("/dashboard")
@login_required
def dashboard():
    gid = current_group().id
    lots = _active(gid)
    buckets = {"fresh": 0, "expiring": 0, "expired": 0, "unknown": 0}
    by_category, by_location = {}, {}
    total_value = Decimal("0")
    open_pkgs = 0
    for s in lots:
        buckets[expiry_status(s.expiry_date)] += 1
        if (s.package_state or "sealed") == "opened":
            open_pkgs += 1
        cat = s.product.category if s.product else "other"
        by_category[cat] = by_category.get(cat, 0) + 1
        loc = s.location.name if s.location else "Unassigned"
        by_location[loc] = by_location.get(loc, 0) + 1
        if s.cost is not None:
            total_value += Decimal(str(s.cost))
    expiring = sorted(
        [s for s in lots if expiry_status(s.expiry_date) in ("expiring", "expired")],
        key=lambda s: (s.expiry_date or s.created_at),
    )
    return jsonify({
        "totals": {
            "lots": len(lots),
            "products": db.session.query(Product).filter_by(group_id=gid).count(),
            "locations": db.session.query(Location).filter_by(group_id=gid).count(),
            "value": money_out(total_value.quantize(Decimal("0.01"))),
            "open": open_pkgs,
            **buckets,
        },
        "byCategory": by_category, "byLocation": by_location,
        "expiring": [stock_out(s) for s in expiring[:20]],
    })


_MACROS = ("kcal", "protein", "carbs", "sugar", "fat", "satFat", "fiber", "sodium")
_TO_GRAMS = {"g": 1.0, "gram": 1.0, "grams": 1.0, "kg": 1000.0}
_TO_ML = {"ml": 1.0, "l": 1000.0, "liter": 1000.0, "litre": 1000.0}


def _pantry_nutrition(active, products):
    """Sum kcal + macros across on-hand stock, but ONLY where a product has per-100g/ml
    nutrition AND its lot quantity is in a matching mass/volume unit, so the total is
    honest. Anything else (no nutrition, or a count/presence unit that can't convert to
    the nutrition basis) is counted as excluded, never guessed. None if nothing sums."""
    from ..services.units import canonical_unit
    totals = {k: 0.0 for k in _MACROS}
    included = excluded = 0
    for s in active:
        p = products.get(s.product_id)
        n = getattr(p, "nutrition", None) if p else None
        per100 = n.get("per100") if isinstance(n, dict) else None
        qty = s.quantity if (s.quantity_kind or "exact") in ("exact", "estimated", "approximate") else None
        unit = canonical_unit(s.unit)
        basis = n.get("basis") if isinstance(n, dict) else None
        grams = None
        if per100 and qty:
            if basis == "100ml" and unit in _TO_ML:
                grams = qty * _TO_ML[unit]
            elif basis != "100ml" and unit in _TO_GRAMS:
                grams = qty * _TO_GRAMS[unit]
        if grams is not None:
            factor = grams / 100.0
            for k in _MACROS:
                v = per100.get(k)
                if isinstance(v, (int, float)):
                    totals[k] += v * factor
            included += 1
        else:
            excluded += 1
    if not included:
        return None  # nothing summable → no card (rather than a misleading all-zero total)
    # Keep genuine zeros (0 g sugar is data, not "unknown").
    kept = {k: round(v, 1) for k, v in totals.items()}
    return {"totals": kept, "itemsIncluded": included, "itemsExcluded": excluded}


@bp.get("/stock/insights")
@login_required
def spend_insights():
    """Spend + value analytics for the household, all money in Decimal → float only
    at the wire. Query: `months` (spend-over-time / category window, default 12).

    Returns: value on hand (by category + total + expired-unused), spend by month
    (from lot purchase dates), spend by category over the window, waste cost (loss-
    outcome consumption events valued at each product's typical unit price, counted
    only when the event's unit matches the priced unit; currently-expired on-hand
    value is reported separately as valueOnHand.expiredUnused), and per-product price
    history (with typical/last price for re-add prefill)."""
    gid = current_group().id
    months = max(1, min(int(request.args.get("months", 12) or 12), 36))
    # DB datetimes are naive (SQLite/Postgres columns) — compare in naive UTC.
    now = utcnow().replace(tzinfo=None)
    window_start = (now - timedelta(days=months * 31)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    products = {p.id: p for p in db.session.query(Product).filter_by(group_id=gid).all()}

    # ---- Value on hand (active lots) ----
    active = _active(gid)
    value_by_cat, total_value, expired_value = defaultdict(lambda: Decimal("0")), Decimal("0"), Decimal("0")
    for s in active:
        if s.cost is None:
            continue
        c = Decimal(str(s.cost))
        cat = products[s.product_id].category if s.product_id in products else "other"
        value_by_cat[cat] += c
        total_value += c
        if expiry_status(s.expiry_date) == "expired":
            expired_value += c

    # ---- Spend over time + by category (all priced lots, keyed by purchase date) ----
    priced = (db.session.query(StockLot)
              .filter(StockLot.group_id == gid, StockLot.cost.isnot(None)).all())
    by_month, spend_by_cat = defaultdict(lambda: Decimal("0")), defaultdict(lambda: Decimal("0"))
    for s in priced:
        when = s.purchase_date or s.created_at
        c = Decimal(str(s.cost))
        mk = _month_key(when)
        if mk:
            by_month[mk] += c
        if when and when >= window_start:
            cat = products[s.product_id].category if s.product_id in products else "other"
            spend_by_cat[cat] += c
    # Dense last-N-months series (zero-filled), oldest → newest.
    series, cur = [], now.replace(day=1)
    for _ in range(months):
        series.append({"month": _month_key(cur), "spend": money_out(by_month.get(_month_key(cur), Decimal("0")))})
        cur = (cur.replace(day=1) - timedelta(days=1)).replace(day=1)
    series.reverse()

    # ---- Waste cost: loss-outcome consumption valued at the product's unit price ----
    from ..services.units import canonical_unit
    typical, last_unit, last_price, price_unit = _typical_unit_prices(gid)
    waste_cost = Decimal("0")
    events = (db.session.query(ConsumptionEvent)
              .filter(ConsumptionEvent.group_id == gid,
                      ConsumptionEvent.outcome.in_(tuple(LOSS_OUTCOMES))).all())
    for ev in events:
        up = typical.get(ev.product_id) or last_unit.get(ev.product_id)
        # Only value the loss when the event's unit matches the unit the price is per —
        # a per-count price times a gram quantity would be a meaningless number.
        if (up is not None and ev.quantity
                and canonical_unit(ev.unit) == price_unit.get(ev.product_id)):
            waste_cost += up * Decimal(str(ev.quantity))

    # ---- Per-product price history (only products with a priced lot) ----
    hist = defaultdict(list)
    for s in sorted(priced, key=lambda s: (s.purchase_date or s.created_at)):
        when = s.purchase_date or s.created_at
        unit = None
        if s.quantity and s.quantity > 0:
            unit = money_out((Decimal(str(s.cost)) / Decimal(str(s.quantity))).quantize(Decimal("0.0001")))
        hist[s.product_id].append({"at": when.isoformat() if when else None,
                                   "price": money_out(Decimal(str(s.cost))), "unitPrice": unit})
    price_history = []
    for pid, points in hist.items():
        p = products.get(pid)
        price_history.append({
            "productId": pid, "name": p.name if p else "?",
            "category": p.category if p else "other",
            "points": points,
            "lastPrice": money_out(last_price.get(pid)),
            "typicalUnitPrice": money_out(typical.get(pid)),
        })
    price_history.sort(key=lambda h: len(h["points"]), reverse=True)

    waste_nudges, waste_trend = _waste_analysis(
        gid, events, typical, last_unit, price_unit, products, now)

    this_month = money_out(by_month.get(_month_key(now), Decimal("0")))
    return jsonify({
        "currency": get_currency(gid),
        "valueOnHand": {
            "total": money_out(total_value.quantize(Decimal("0.01"))),
            "expiredUnused": money_out(expired_value.quantize(Decimal("0.01"))),
            "byCategory": [{"category": c, "value": money_out(v.quantize(Decimal("0.01")))}
                           for c, v in sorted(value_by_cat.items(), key=lambda kv: kv[1], reverse=True)],
        },
        "spendThisMonth": this_month,
        "spendByMonth": series,
        "windowMonths": months,
        "spendByCategory": [{"category": c, "spend": money_out(v.quantize(Decimal("0.01")))}
                            for c, v in sorted(spend_by_cat.items(), key=lambda kv: kv[1], reverse=True)],
        "wasteCost": money_out(waste_cost.quantize(Decimal("0.01"))),
        "wasteNudges": waste_nudges,
        "wasteTrend": waste_trend,
        "priceHistory": price_history[:40],
        "pantryNutrition": _pantry_nutrition(active, products),
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
        # Only count lots with a real number — a presence/unknown lot has no
        # amount to forecast against, and would otherwise read as "0 / ~0 days".
        if (s.quantity_kind or "exact") not in ("exact", "estimated", "approximate"):
            continue
        on_hand[s.product_id] = on_hand.get(s.product_id, 0) + (s.quantity or 0)
    out = []
    for pid, qty in on_hand.items():
        if qty <= 0:
            continue  # already out — not "running low"
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
