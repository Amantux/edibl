"""Bidirectional myMeal integration + the planning/targeting surface.

myMeal propagates the ingredients its recipes/meal-plans need → Edibl tracks
them as PlannedItems and reconciles against real stock. The result answers
"what do I need / what should I order / what can I make right now?".
"""
from datetime import datetime

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import PlannedItem, ShoppingItem
from ..auth import login_required, current_group
from ..schemas.serializers import iso, shopping_out
from ..services.planning import analyze_demand
from ..services import integrations as integ
from ..services.integrations import integration_status, mymeal_get
from ..services.settings import set_mymeal

bp = Blueprint("integrations", __name__)


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return None


def _planned_out(p):
    return {"id": p.id, "name": p.name, "quantity": p.quantity, "unit": p.unit,
            "neededBy": iso(p.needed_by), "source": p.source, "sourceRef": p.source_ref,
            "meal": p.meal, "createdAt": iso(p.created_at)}


@bp.get("/integrations/status")
@login_required
def status():
    return jsonify(integration_status())


@bp.get("/integrations/mymeal")
@login_required
def mymeal_settings():
    """myMeal connection view for the UI (URL + whether a token is on file)."""
    return jsonify(integ.mymeal_public())


@bp.put("/integrations/mymeal")
@login_required
def set_mymeal_settings():
    """Connect Edibl to myMeal from the UI. Body: { url, token? }. A blank/omitted
    token is left unchanged; overrides the add-on/env default and is remembered."""
    data = request.get_json(force=True) or {}
    kwargs = {}
    if "url" in data:
        kwargs["url"] = str(data["url"] or "")
    if data.get("token"):
        kwargs["token"] = str(data["token"])
    set_mymeal(current_group().id, **kwargs)
    return jsonify(integ.mymeal_public())


@bp.post("/integrations/mymeal/test")
@login_required
def test_mymeal():
    """Test the myMeal connection without changing anything."""
    return jsonify(integ.mymeal_test())


@bp.post("/integrations/mymeal/discover")
@login_required
def discover_mymeal():
    """Find myMeal running as a Home Assistant add-on (via the Supervisor) so it can
    be connected on the internal network without knowing the hostname."""
    return jsonify(integ.discover_mymeal())


@bp.get("/integrations/mymeal/discover/debug")
@login_required
def discover_mymeal_debug():
    """Read-only diagnostics for 'Find myMeal': what the Supervisor returned and
    every candidate host tried, with per-host probe results. No secrets."""
    return jsonify(integ.discover_mymeal_debug())


@bp.post("/integrations/mymeal/plan")
@login_required
def ingest_plan():
    """myMeal pushes planned ingredients here. Upserts by (sourceRef, name) so a
    re-push of the same recipe/plan updates rather than duplicates.

    Body: { meal?, source?, items: [{name, quantity?, unit?, neededBy?, sourceRef?}] }
    """
    data = request.get_json(force=True) or {}
    items = data.get("items") or data.get("ingredients") or []
    if not items:
        return jsonify({"error": "items[] required"}), 422
    gid = current_group().id
    meal = data.get("meal", "")
    source = data.get("source", "mymeal")
    upserted = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        ref = it.get("sourceRef") or data.get("sourceRef") or ""
        existing = None
        if ref:
            existing = (db.session.query(PlannedItem)
                        .filter_by(group_id=gid, source_ref=ref, name=name).first())
        p = existing or PlannedItem(group_id=gid, name=name)
        p.quantity = float(it.get("quantity") or 1)
        p.unit = it.get("unit") or "count"
        p.needed_by = _parse_dt(it.get("neededBy"))
        p.source = source
        p.source_ref = ref
        p.meal = it.get("meal") or meal
        if existing is None:
            db.session.add(p)
        upserted.append(p)
    db.session.commit()
    return jsonify({"upserted": len(upserted),
                    "items": [_planned_out(p) for p in upserted]}), 201


@bp.get("/plan")
@login_required
def plan():
    """Current planned demand reconciled against on-hand stock."""
    rows = (db.session.query(PlannedItem).filter_by(group_id=current_group().id)
            .order_by(PlannedItem.needed_by.is_(None).asc(),
                      PlannedItem.needed_by.asc()).all())
    demand = [{"name": p.name, "quantity": p.quantity, "unit": p.unit} for p in rows]
    analysis = analyze_demand(current_group().id, demand)
    return jsonify({"planned": [_planned_out(p) for p in rows], **analysis})


@bp.post("/plan/check")
@login_required
def check():
    """Stateless: given ingredients (e.g. one recipe from myMeal), report
    availability + shortfall without persisting. Body: {ingredients:[{name,...}]}."""
    data = request.get_json(force=True) or {}
    demand = data.get("ingredients") or data.get("items") or []
    if not demand:
        return jsonify({"error": "ingredients[] required"}), 422
    return jsonify(analyze_demand(current_group().id, demand))


@bp.post("/plan/order")
@login_required
def order():
    """Turn the current plan's shortfall into shopping-list items (skips dupes)."""
    gid = current_group().id
    rows = db.session.query(PlannedItem).filter_by(group_id=gid).all()
    demand = [{"name": p.name, "quantity": p.quantity, "unit": p.unit} for p in rows]
    shortfall = analyze_demand(gid, demand)["shortfall"]
    on_list = {i.name.lower() for i in db.session.query(ShoppingItem)
               .filter_by(group_id=gid, status="needed").all()}
    added = []
    for s in shortfall:
        if s["name"].lower() in on_list:
            continue
        i = ShoppingItem(name=s["name"], quantity=s["quantity"], unit=s["unit"],
                         source="recipe", group_id=gid)
        db.session.add(i)
        added.append(i)
    db.session.commit()
    return jsonify({"added": len(added), "items": [shopping_out(i) for i in added]})


@bp.delete("/plan/<item_id>")
@login_required
def delete_planned(item_id):
    p = db.session.get(PlannedItem, item_id)
    if not p or p.group_id != current_group().id:
        abort(404)
    db.session.delete(p)
    db.session.commit()
    return "", 204


@bp.post("/plan/clear")
@login_required
def clear():
    n = (db.session.query(PlannedItem).filter_by(group_id=current_group().id)
         .delete())
    db.session.commit()
    return jsonify({"cleared": n})


@bp.post("/integrations/mymeal/pull")
@login_required
def pull_from_mymeal():
    """Pull the upcoming meal plan FROM myMeal (outbound) and ingest it. Requires
    EDIBL_MYMEAL_URL/TOKEN. Expects myMeal to expose the planned ingredients at
    the documented path; degrades gracefully when not configured."""
    res = mymeal_get("/api/v1/plan/ingredients")
    if not res.get("configured"):
        return jsonify({"error": "myMeal not configured (set EDIBL_MYMEAL_URL)"}), 400
    if not res.get("reachable"):
        return jsonify({"error": "myMeal unreachable", "detail": res.get("error")}), 502
    items = (res.get("data") or {}).get("items", [])
    gid = current_group().id
    added = 0
    for it in items:
        if not (it.get("name") or "").strip():
            continue
        db.session.add(PlannedItem(
            group_id=gid, name=it["name"].strip(),
            quantity=float(it.get("quantity") or 1), unit=it.get("unit") or "count",
            needed_by=_parse_dt(it.get("neededBy")), source="mymeal",
            source_ref=it.get("sourceRef") or "", meal=it.get("meal") or ""))
        added += 1
    db.session.commit()
    return jsonify({"pulled": added})
