"""Bidirectional myMeal integration + the planning/targeting surface.

myMeal propagates the ingredients its recipes/meal-plans need → Edibl tracks
them as PlannedItems and reconciles against real stock. The result answers
"what do I need / what should I order / what can I make right now?".
"""
from datetime import datetime

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import PlannedItem, ShoppingItem
from ..auth import login_required, owner_required, current_group
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


def _map_recipe_ingredients(recipe):
    """A myMeal recipe dict → Edibl ingredient list [{name, quantity, unit}]. The two
    apps share no ingredient ids, so match by name string: prefer the parsed
    ``food.name``, else the original free-text ``display``; unit prefers the parsed
    abbreviation. Sub-recipe (refRecipe) lines have no food/qty and are skipped."""
    # Aggregate duplicate lines by (name, unit): a recipe that lists "Flour" for the
    # dough and again for dusting is one demand for the summed amount — so cook deducts
    # it once (not twice) and shop adds a single row.
    out = {}
    for ing in (recipe.get("ingredients") or []):
        food = ing.get("food") or {}
        unit = ing.get("unit") or {}
        name = (food.get("name") or ing.get("display") or "").strip()
        if not name:
            continue
        u = unit.get("abbreviation") or "count"
        try:
            q = float(ing.get("quantity") or 1)
        except (TypeError, ValueError):
            q = 1.0
        key = (name.lower(), u)
        if key in out:
            out[key]["quantity"] += q
        else:
            out[key] = {"name": name, "quantity": q, "unit": u}
    return list(out.values())


def cook_ingredients(gid, ingredients, *, source_app="plan", idempotency_key=None):
    """Deduct each ingredient from real stock — match a food/beverage product and
    consume the needed amount across its lots (prefer-open, then first-expiring,
    spilling). Returns [{name, consumed, shortfall, matched}]. Shared by /plan/cook and
    the recipe-cook route; never guesses across food types (an unmatched item is
    reported, not consumed).

    `idempotency_key` (from a client per-press token) is threaded into each consume so
    a retried cook request replays instead of deducting twice. Best-effort: it dedupes
    an exact resend; a retry after only PARTIAL success re-plans against the changed
    stock and so isn't fully covered (would need an action-level guard)."""
    from ..services import matching
    from ..services.inventory import selection, consume_lot
    results = []
    for idx, it in enumerate(ingredients):
        name = (it.get("name") or "").strip()
        if not name:
            continue
        try:
            qty = float(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        # resolve_for_mutation, NOT match_products[0]: consuming stock is a
        # mutation and must not GUESS across materially-different products
        # (ADR-0003). An ambiguous or weak-only match reports unmatched rather
        # than silently eating the wrong product — which is what this function's
        # own docstring already promises.
        resolution = matching.resolve_for_mutation(
            gid, name, item_types={"food", "beverage"})
        product = resolution.product
        consumed, shortfall = 0.0, qty
        if product:
            lots = [s for s in product.stock if not s.finished]
            picks, shortfall = selection.plan_consumption(lots, qty)
            for pk in picks:
                key = f"{idempotency_key}:{idx}:{pk.lot.id}" if idempotency_key else None
                consume_lot(pk.lot, amount=pk.take, outcome="eaten",
                            actor_user_id=None, source_app=source_app,
                            idempotency_key=key)
                consumed += pk.take
        results.append({"name": name, "consumed": round(consumed, 4),
                        "shortfall": round(shortfall, 4), "matched": bool(product)})
    return results


def add_shortfall_to_shopping(gid, ingredients):
    """Add each ingredient's shortfall (what's not on hand) to the shopping list as a
    source='recipe' row, skipping anything already 'needed'. Returns the added rows."""
    shortfall = analyze_demand(gid, ingredients)["shortfall"]
    on_list = {i.name.lower() for i in db.session.query(ShoppingItem)
               .filter_by(group_id=gid, status="needed").all()}
    added = []
    for s in shortfall:
        key = s["name"].lower()
        if key in on_list:
            continue
        on_list.add(key)  # track within the batch too, so a repeated shortfall name
        # in a single call doesn't add two identical rows (analyze_demand doesn't merge)
        i = ShoppingItem(name=s["name"], quantity=s["quantity"], unit=s["unit"],
                         source="recipe", group_id=gid)
        db.session.add(i)
        added.append(i)
    db.session.commit()
    return added


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
@owner_required
def set_mymeal_settings():
    """Connect Edibl to myMeal from the UI. Body: { url, token? }. A blank/omitted
    token is left unchanged; overrides the add-on/env default and is remembered."""
    data = request.get_json(force=True) or {}
    kwargs = {}
    if "url" in data:
        url = str(data["url"] or "")
        # Reject a blocked URL at save with a clear 422, mirroring the LLM URL.
        # The point-of-use guard in integrations._get/_write is the real
        # boundary (env/options bypass this), but a save-time error is better
        # UX than a silent later failure.
        from ..services.url_guard import llm_url_ok
        ok, err = llm_url_ok(url)
        if not ok:
            return jsonify({"error": err or "URL is not allowed"}), 422
        kwargs["url"] = url
    if data.get("token"):
        kwargs["token"] = str(data["token"])
    gid = current_group().id
    set_mymeal(gid, **kwargs)
    _auto_sync_units(gid)  # best-effort: share the unit vocabulary on connect
    return jsonify(integ.mymeal_public())


@bp.post("/integrations/mymeal/test")
@owner_required
def test_mymeal():
    """Test the myMeal connection without changing anything."""
    return jsonify(integ.mymeal_test())


@bp.post("/integrations/mymeal/discover")
@owner_required
def discover_mymeal():
    """Find myMeal running as a Home Assistant add-on (via the Supervisor) so it can
    be connected on the internal network without knowing the hostname."""
    return jsonify(integ.discover_mymeal())


@bp.get("/integrations/mymeal/discover/debug")
@owner_required
def discover_mymeal_debug():
    """Read-only diagnostics for 'Find myMeal': what the Supervisor returned and
    every candidate host tried, with per-host probe results. No secrets."""
    return jsonify(integ.discover_mymeal_debug())


@bp.post("/integrations/mymeal/plan")
@login_required
def ingest_plan():
    """myMeal pushes planned ingredients here. Upserts by (sourceRef, name) so a
    re-push of the same recipe/plan updates rather than duplicates.

    ``sourceRef`` identifies the RECIPE (``mymeal:recipe:<id>``) and is shared by
    all of its ingredients, so ``name`` has to be part of the key to tell them
    apart. The consequence is that a renamed ingredient does not match its old
    row: re-pushing a recipe whose "flour" became "plain flour" used to leave
    BOTH rows, doubling that recipe's demand and putting two lines on the
    shopping order. So a push is treated as the authoritative statement of what
    a recipe needs — any row for a sourceRef in this payload whose name is no
    longer in it is stale, and is removed.

    Only sourceRefs present in THIS payload are touched: a recipe that isn't
    being pushed is left completely alone.

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
    # ref -> the names this push says that recipe still needs.
    kept: dict[str, set[str]] = {}
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        ref = it.get("sourceRef") or data.get("sourceRef") or ""
        existing = None
        if ref:
            existing = (db.session.query(PlannedItem)
                        .filter_by(group_id=gid, source_ref=ref, name=name).first())
            kept.setdefault(ref, set()).add(name)
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

    # No explicit flush: a row added above is by definition in `kept`, so
    # `notin_` excludes it whether or not it has been INSERTed yet. (An earlier
    # version flushed here defensively; removing it failed no test, and a line
    # no test can justify is a line that will confuse the next reader.)
    pruned = 0
    for ref, names in kept.items():
        # `kept` only ever gains non-empty refs, so this cannot mass-delete the
        # hand-added items that carry no sourceRef at all.
        pruned += (db.session.query(PlannedItem)
                   .filter(PlannedItem.group_id == gid,
                           PlannedItem.source_ref == ref,
                           PlannedItem.name.notin_(sorted(names)))
                   .delete(synchronize_session=False))
    db.session.commit()
    return jsonify({"upserted": len(upserted), "pruned": pruned,
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
    added = add_shortfall_to_shopping(gid, demand)
    return jsonify({"added": len(added), "items": [shopping_out(i) for i in added]})


@bp.post("/plan/cook")
@login_required
def cook():
    """"I made this" — deduct a meal's ingredients from real stock. For each
    ingredient, match a food/beverage product and consume the needed amount across
    its lots (prefer-open, then first-expiring, spilling). Body: {ingredients?:
    [{name, quantity?, unit?}], clear?: bool}. With no ingredients, uses the current
    plan. Returns per-ingredient consumed/shortfall; `clear` removes satisfied
    planned items. Never guesses across food types — an unmatched item is reported."""
    gid = current_group().id
    data = request.get_json(force=True) or {}
    ingredients = data.get("ingredients")
    planned_by_key = {}
    if not ingredients:
        rows = db.session.query(PlannedItem).filter_by(group_id=gid).all()
        ingredients = []
        for p in rows:
            ingredients.append({"name": p.name, "quantity": p.quantity, "unit": p.unit})
            planned_by_key[p.name.lower()] = p

    results = cook_ingredients(gid, ingredients)

    cleared = 0
    if data.get("clear"):
        for r in results:
            p = planned_by_key.get(r["name"].lower())
            if p is not None and r["shortfall"] <= 0:
                db.session.delete(p)
                cleared += 1
        if cleared:
            db.session.commit()

    return jsonify({"cooked": results, "cleared": cleared,
                    "meal": data.get("meal", "")})


@bp.get("/cook/suggestions")
@login_required
def cook_suggestions():
    """Recipe suggestions from myMeal, ranked by Edibl's own stock (myMeal fetches it
    via /have). ``?mode=make`` = what you can cook now; ``?mode=useup`` = recipes that
    use soon-expiring items (?days, ?limit). Proxies myMeal's ranking and degrades
    gracefully to {configured/reachable:false, suggestions:[]} when myMeal is off —
    never a 500."""
    mode = (request.args.get("mode") or "make").lower()
    if mode == "useup":
        res = integ.mymeal_use_it_up(days=request.args.get("days", type=int),
                                     limit=request.args.get("limit", type=int))
    else:
        res = integ.mymeal_suggest()
    out = {"mode": mode, "configured": res.get("configured", False),
           "reachable": res.get("reachable", False), "suggestions": []}
    if res.get("reachable"):
        data = res.get("data") or {}
        out["suggestions"] = data.get("suggestions") or []
        out["expiring"] = data.get("expiring") or []
    elif res.get("error"):
        out["error"] = res["error"]
    return jsonify(out)


def _recipe_or_error(recipe_id):
    """Fetch a myMeal recipe → (ingredients, recipe, error_response). error_response is
    a ready (json, status) tuple when myMeal is unreachable or the recipe is unusable."""
    res = integ.mymeal_recipe(recipe_id)
    if not res.get("reachable"):
        return None, None, (jsonify({
            "configured": res.get("configured", False), "reachable": False,
            "error": res.get("error") or "myMeal is not reachable"}), 502)
    recipe = res.get("data") or {}
    ingredients = _map_recipe_ingredients(recipe)
    if not ingredients:
        return None, None, (jsonify({"error": "recipe has no usable ingredients"}), 422)
    return ingredients, recipe, None


@bp.post("/cook/recipe/<recipe_id>")
@login_required
def cook_recipe(recipe_id):
    """"I cooked this myMeal recipe" — fetch its ingredients and deduct them from real
    stock (same consumption as /plan/cook). Returns per-ingredient consumed/shortfall."""
    ingredients, recipe, err = _recipe_or_error(recipe_id)
    if err:
        return err
    # A per-press token from the client makes a retried cook replay instead of
    # deducting twice (the button is also disabled client-side during the request).
    token = str((request.get_json(silent=True) or {}).get("idempotencyKey") or "").strip()
    idem = f"cook:{recipe_id}:{token}" if token else None
    results = cook_ingredients(current_group().id, ingredients, source_app="recipe",
                               idempotency_key=idem)
    return jsonify({"recipe": recipe.get("name") or "", "cooked": results})


@bp.post("/cook/recipe/<recipe_id>/shop")
@login_required
def shop_recipe(recipe_id):
    """Add a myMeal recipe's missing ingredients to the shopping list (source='recipe',
    skips anything already on the list)."""
    ingredients, recipe, err = _recipe_or_error(recipe_id)
    if err:
        return err
    added = add_shortfall_to_shopping(current_group().id, ingredients)
    return jsonify({"recipe": recipe.get("name") or "",
                    "added": len(added), "items": [shopping_out(i) for i in added]})


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
@owner_required
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


class _SyncUnavailable(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


def _sync_units_core(gid, *, use_llm):
    """Two-way units sync with a connected myMeal — each side's missing units are
    added to the other. LLM-contextual reconciliation (maps equivalents by meaning,
    e.g. count≈each) when use_llm and a provider is set; else a deterministic
    canonical-name union. Raises _SyncUnavailable if myMeal isn't connected."""
    from ..models import Unit
    from ..services import assistant
    from ..services.integrations import mymeal_post
    from ..services.units import canonical_unit
    from .units import _upsert_unit, unit_out

    res = mymeal_get("/units")
    if not res.get("configured"):
        raise _SyncUnavailable(409, "myMeal isn't connected — set it up first.")
    if not res.get("reachable"):
        raise _SyncUnavailable(502, "myMeal is configured but unreachable.")
    # Defend against a malformed /units response (strings / non-list / version skew).
    raw = res.get("data")
    mymeal_units = [u for u in raw if isinstance(u, dict) and u.get("name")] \
        if isinstance(raw, list) else []
    edibl_units = [unit_out(u) for u in
                   db.session.query(Unit).filter_by(group_id=gid).all()]

    plan = assistant.reconcile_units(edibl_units, mymeal_units) if use_llm else None
    reconciler = "llm" if plan is not None else "union"
    if plan is None:
        e_names = {canonical_unit(u["name"]) for u in edibl_units}
        m_names = {canonical_unit(u.get("name")) for u in mymeal_units}
        plan = {
            "toEdibl": [u for u in mymeal_units
                        if canonical_unit(u.get("name")) not in e_names],
            "toMyMeal": [u for u in edibl_units
                         if canonical_unit(u["name"]) not in m_names],
        }

    # Normalize whatever the reconciler produced to well-formed {name, …} dicts.
    def _clean(items):
        return [u for u in (items or []) if isinstance(u, dict) and (u.get("name") or "").strip()]
    plan = {"toEdibl": _clean(plan.get("toEdibl")), "toMyMeal": _clean(plan.get("toMyMeal"))}

    before = db.session.query(Unit).filter_by(group_id=gid).count()
    for u in plan["toEdibl"]:
        _upsert_unit(gid, u.get("name"), plural=u.get("pluralName") or "",
                     abbreviation=u.get("abbreviation") or "")
    db.session.commit()
    added_to_edibl = db.session.query(Unit).filter_by(group_id=gid).count() - before

    pushed_to_mymeal = 0
    for u in plan["toMyMeal"]:
        if not (u.get("name") or "").strip():
            continue
        r = mymeal_post("/units", {"name": u["name"],
                                   "pluralName": u.get("pluralName") or "",
                                   "abbreviation": u.get("abbreviation") or ""})
        if r.get("reachable"):
            pushed_to_mymeal += 1
    return {"reconciler": reconciler, "addedToEdibl": added_to_edibl,
            "pushedToMyMeal": pushed_to_mymeal}


@bp.post("/integrations/mymeal/sync-units")
@owner_required
def sync_units():
    """Manual two-way unit sync (LLM-reconciled when a provider is configured)."""
    try:
        return jsonify(_sync_units_core(current_group().id, use_llm=True))
    except _SyncUnavailable as e:
        return jsonify({"error": e.msg}), e.status


def _auto_sync_units(gid):
    """Best-effort deterministic unit sync fired when the connection is (re)set, so
    units share automatically on connect. Swallows everything — never blocks setup."""
    try:
        _sync_units_core(gid, use_llm=False)
    except Exception:  # noqa: BLE001 - best-effort; myMeal may be unreachable/absent
        db.session.rollback()
