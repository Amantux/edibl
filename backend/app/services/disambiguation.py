"""Low-confidence resolution: explain the choice, preview it, never guess.

When the deterministic scorer (`services.matching`) can't confidently pick ONE
product, a mutation must stop and ask. This module builds what the user needs to
answer: the candidates, a preview of what each choice would actually consume,
and a sentence explaining how they differ.

Two rules hold this together:

* **The scorer decides, the model explains.** An LLM never promotes a LOW match
  to an action on a destructive path. `allow_autoresolve` is applied INSIDE
  `explain_candidates`, so a caller cannot opt into auto-resolution by mistake —
  the capability simply isn't in the returned value. Reads may pass True.
* **It works with no LLM.** No provider (or a provider that fails) degrades to a
  deterministic sentence built from the candidate metadata, exactly like
  `assistant.reconcile_units`/`classify_food`. Nothing here raises.
"""
import json
import logging

from ..schemas.serializers import iso

_LOGGER = logging.getLogger("edibl.disambiguation")

# How many candidates a human can actually choose between (SOP: 3-5).
MAX_CANDIDATES = 5

# The caller-supplied phrase is interpolated into the prompt, so it is bounded:
# a 200KB "name" otherwise became a 200KB prompt on every explain request.
_MAX_PHRASE = 200

# The model's prose is returned to callers (and, on the chat path, back into a
# tool-using model), so it is length-capped too.
_MAX_REASONING = 600


def _soonest_lot(product, scope_ids=None):
    """The lot a mutation would act on: soonest-to-expire, unfinished (FEFO).
    `scope_ids` narrows to a location subtree so the candidate describes the
    stock actually in play — otherwise a scoped consume can show "in Fridge"
    while it draws from the Garage, and location is exactly the field a human
    disambiguates on."""
    lots = [s for s in product.stock if not s.finished]
    if scope_ids:
        lots = [s for s in lots if s.location_id in scope_ids]
    if not lots:
        return None
    lots.sort(key=lambda s: (s.expiry_date is None, s.expiry_date))
    return lots[0]


def candidate_out(cand, scope_ids=None):
    """One product a user can actually choose between: what it is, and where.

    Brand/category/location are what make "Milk (Organic Valley, dairy, in
    Fridge)" distinguishable from "Almond Milk (Califia, dairy-alt, in Pantry)".
    Shared by /stock/resolve and the consume confirmation so both describe a
    candidate identically.
    """
    p = cand.product
    lots = [s for s in p.stock if not s.finished]
    if scope_ids:
        lots = [s for s in lots if s.location_id in scope_ids]
    lot = _soonest_lot(p, scope_ids)
    return {
        "productId": p.id,
        "name": p.name,
        "brand": p.brand or "",
        "category": p.category or "",
        "family": p.family or "",
        "location": (lot.location.name if lot and lot.location else ""),
        "lots": len(lots),
        "lotId": lot.id if lot else None,
        "nextExpiry": iso(lot.expiry_date) if lot and lot.expiry_date else None,
        "matchedOn": cand.reasons,
        "score": cand.score,
    }


def describe_candidate(c: dict) -> str:
    """'Almond Milk (Califia, dairy-alt, in Fridge, 2 lots, next expiry Aug 12)'."""
    bits = [b for b in (c.get("brand"), c.get("category")) if b]
    if c.get("location"):
        bits.append(f"in {c['location']}")
    if c.get("lots"):
        bits.append(f"{c['lots']} lot{'s' if c['lots'] != 1 else ''}")
    if c.get("nextExpiry"):
        bits.append(f"next expiry {str(c['nextExpiry'])[:10]}")
    return f"{c['name']} ({', '.join(bits)})" if bits else c["name"]


def deterministic_reasoning(query: str, candidates: list) -> str:
    """The always-available explanation. This is not a fallback afterthought —
    it is what most households (no LLM configured) actually see."""
    if not candidates:
        return f"Nothing in stock matches '{query}'."
    listed = "; ".join(describe_candidate(c) for c in candidates)
    return (f"'{query}' matches {len(candidates)} different products: {listed}. "
            f"Nothing was changed — say which one you meant.")


def consume_preview(candidates, products_by_id, quantity, unit=None,
                    scope_ids=None, policy=None) -> list:
    """What each candidate would actually consume, WITHOUT consuming anything.

    Runs the same pure planner the real consume uses (`selection.plan_consumption`)
    with the SAME inputs — including `policy`, which decides *which* lot is drawn
    (prefer-open vs strict FEFO). Previewing under a different policy named a
    different lot than the confirmed call then drew.

    `wouldConsume`/`shortfall` are in the DEMAND unit and the row carries that
    `unit`, because the real response reports `consumed` as a sum of per-lot
    takes in LOT units — "would use 500 (g)" next to "used 0.5 (kg)" is the same
    draw at two scales, and a preview that reads as a different number is worse
    than none.
    """
    from .inventory import selection
    out = []
    for c in candidates:
        product = products_by_id.get(c["productId"])
        if product is None:
            continue
        lots = [s for s in product.stock if not s.finished]
        if scope_ids:
            lots = [s for s in lots if s.location_id in scope_ids]
        picks, shortfall = selection.plan_consumption(
            lots, quantity, demand_unit=unit,
            policy=policy or selection.PREFER_OPEN_FEFO)
        out.append({
            "productId": c["productId"],
            "name": c["name"],
            "wouldConsume": round(max(quantity - shortfall, 0.0), 4),
            "shortfall": round(shortfall, 4),
            # The unit `wouldConsume`/`shortfall` are quoted in: the demand unit
            # when one was given, else the drawn lots' own unit.
            "unit": (unit or (picks[0].lot.unit if picks else
                              (lots[0].unit if lots else ""))),
            "fromLots": [{"lotId": p.lot.id, "take": p.take,
                          "unit": p.lot.unit,
                          "location": (p.lot.location.name
                                       if p.lot.location else "")}
                         for p in picks],
        })
    return out


# ---- LLM reasoning (advisory only) ----------------------------------------

_DISAMBIGUATE_SYSTEM = (
    "You help a household inventory app explain an ambiguous match. "
    "Given a user's phrase and the candidate products they might have meant, "
    "write ONE short sentence (max 40 words) telling them apart using the facts "
    "given — brand, category, location, expiry. Plain language, no invented "
    "facts, never mention scores or matching internals. "
    "Respond ONLY as JSON: "
    '{"reasoning": "...", "order": ["productId", ...], "pick": "productId or null"}. '
    "`order` ranks the candidates most-likely-first. Set `pick` only when one is "
    "unmistakably the intended product; otherwise null."
)


def explain_candidates(gid, query, candidates, *, allow_autoresolve=False,
                       use_llm=True) -> dict:
    """``{"reasoning": str, "order": [productId...], "autoResolved": id|None}``.

    `allow_autoresolve=False` (the default, and what every DESTRUCTIVE caller
    uses) forces `autoResolved` to None before returning — the model's opinion
    can inform the wording and the ordering, never the action. Reads that pass
    True get the model's pick, and must label it as such so it is never confused
    with a deterministic high-confidence match.

    Never raises: no provider, a malformed reply, or a provider failure all fall
    back to `deterministic_reasoning`.
    """
    fallback = {"reasoning": deterministic_reasoning(query, candidates),
                "order": [c["productId"] for c in candidates],
                "autoResolved": None}
    if not candidates or not use_llm:
        return fallback

    from . import assistant
    cfg = None
    try:
        # _cfg reads DB overrides and deliberately does NOT swallow a real DB
        # error — inside the try, so an advisory explanation can never turn a
        # safe 409 into a 500 on a destructive flow.
        cfg = assistant._cfg(gid)
        if cfg["provider"] not in assistant._PROVIDERS:
            return fallback
        payload = json.dumps({
            # Cap the phrase: it is caller-controlled and goes into the prompt.
            "phrase": (query or "")[:_MAX_PHRASE],
            "candidates": [{k: c.get(k) for k in
                            ("productId", "name", "brand", "category",
                             "location", "lots", "nextExpiry")}
                           for c in candidates],
        })
        reply = assistant._complete(cfg, _DISAMBIGUATE_SYSTEM, payload)
        if reply:
            s, e = reply.find("{"), reply.rfind("}")
            if s != -1 and e != -1 and e > s:
                data = json.loads(reply[s:e + 1])
                if isinstance(data, dict):
                    return _shape(data, candidates, fallback, allow_autoresolve)
    except Exception as exc:  # noqa: BLE001 — advisory only; degrade, never fail
        # cfg may be None when _cfg itself raised, so don't index it blindly.
        _LOGGER.info("disambiguation via '%s' failed, using deterministic "
                     "reasoning: %s",
                     (cfg or {}).get("provider", "?"), exc)
    return fallback


def _shape(data, candidates, fallback, allow_autoresolve) -> dict:
    """Validate the model's reply against the REAL candidates — a model may name
    a product that isn't on the list, and that must never become a selection."""
    valid = {c["productId"] for c in candidates}
    reasoning = data.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        reasoning = fallback["reasoning"]
    reasoning = reasoning[:_MAX_REASONING]

    order = [pid for pid in (data.get("order") or []) if pid in valid]
    # Anything the model dropped keeps its deterministic rank, at the back.
    order += [pid for pid in fallback["order"] if pid not in order]

    pick = data.get("pick")
    auto = pick if (allow_autoresolve and isinstance(pick, str)
                    and pick in valid) else None
    return {"reasoning": reasoning.strip(), "order": order, "autoResolved": auto}
