"""Smart default placement — where should a new lot go?

Ranked, group-scoped, offline-first. The order matters: **history wins** (where this
product's kind already lives is the user's own correction, so honour it), then the
per-area descriptions the user/chat/AI curate, then a storage-method → area-kind
heuristic, and only then an optional LLM tiebreak. Every step degrades gracefully to
the next, so a household with no descriptions and no LLM still gets a sensible area.
"""
from ..extensions import db
from ..models import Location, StockLot

# storage_method (on a lot) → the location kind that fits it.
_METHOD_KIND = {
    "frozen": "freezer",
    "refrigerated": "fridge",
    "chilled": "fridge",
    "pantry": "pantry",
    "ambient": "pantry",
    "room_temp": "pantry",
    "cool_dark": "cupboard",
}
# Fallback area order when nothing else matches (best → worst).
_DEFAULT_KIND_ORDER = ("pantry", "cupboard", "fridge", "other")


def _tokens(s):
    return {w for w in "".join(c if c.isalnum() else " " for c in (s or "").lower()).split()
            if len(w) >= 3}


def _out(loc, reason, confidence):
    # confidence: "high" → auto-place and trust it; "low" → place tentatively but flag
    # for review (shown in a distinct colour until the user confirms).
    return {"locationId": loc.id, "locationName": loc.name,
            "reason": reason, "confidence": confidence}


def suggest_location(gid, name, category=None, storage_method=None):
    """Best default location for a new `name` (with optional classified category /
    storage_method). Returns {locationId, locationName, reason} or None when the group
    has no usable locations."""
    locs = [lc for lc in db.session.query(Location).filter_by(group_id=gid).all()
            if lc.kind not in ("site", "room")]  # containers, not places you walk into
    if not locs:
        return None
    by_kind = {}
    for lc in locs:
        by_kind.setdefault(lc.kind, []).append(lc)

    # (a) History — where this product's existing active lots live (the correction loop).
    from . import matching
    cands = matching.match_products(gid, name)
    if cands:
        counts = {}
        for lot in (db.session.query(StockLot)
                    .filter(StockLot.product_id == cands[0].product.id,
                            StockLot.group_id == gid, StockLot.finished.is_(False),
                            StockLot.location_id.isnot(None)).all()):
            counts[lot.location_id] = counts.get(lot.location_id, 0) + 1
        if counts:
            lid = max(counts, key=counts.get)
            loc = next((lc for lc in locs if lc.id == lid), None)
            if loc:
                return _out(loc, "with the others", "high")

    # (b) Description / name keyword match — the curated "what this area stores".
    want = _tokens(name) | _tokens(category)
    best, best_score = None, 0
    for lc in locs:
        hay = _tokens(lc.description) | _tokens(lc.name)
        score = len(want & hay)
        if score > best_score:
            best, best_score = lc, score
    if best is not None:
        return _out(best, f"matches “{best.name}”", "high")

    # (c) storage_method → area-kind heuristic (frozen→freezer, refrigerated→fridge, …).
    kind = _METHOD_KIND.get((storage_method or "").lower())
    if kind and by_kind.get(kind):
        return _out(by_kind[kind][0], f"{storage_method} → {kind}", "high")

    # (d) Optional LLM tiebreak — only when configured; never required.
    picked = _llm_pick(name, category, storage_method, locs)
    if picked is not None:
        return _out(picked, "suggested", "low")

    # Fallback: a sensible default area.
    for k in _DEFAULT_KIND_ORDER:
        if by_kind.get(k):
            return _out(by_kind[k][0], "default area", "low")
    return _out(locs[0], "default area", "low")


def _llm_pick(name, category, storage_method, locs):
    """Ask the configured LLM which area fits; None on any failure / no LLM."""
    try:
        from .assistant import _cfg, _complete, _PROVIDERS
        cfg = _cfg()
        if cfg["provider"] not in _PROVIDERS:
            return None
        catalog = "\n".join(f"{i}. {lc.name} (kind={lc.kind}): {lc.description or '—'}"
                            for i, lc in enumerate(locs))
        system = ("You place a grocery item into the best storage area. Reply with ONLY "
                  "the number of the area, nothing else.")
        user = (f"Item: {name} (category={category or '?'}, storage={storage_method or '?'}).\n"
                f"Areas:\n{catalog}")
        reply = _complete(cfg, system, user) or ""
        digits = "".join(c for c in reply if c.isdigit())
        if digits and int(digits) < len(locs):
            return locs[int(digits)]
    except Exception:  # noqa: BLE001 — best-effort; heuristic already had a fallback
        return None
    return None


def describe_area(gid, loc, *, use_llm=True):
    """Generate 'what this area normally stores' from its kind + the categories/products
    currently in it (LLM when configured, else a heuristic sentence). Returns the text;
    does NOT persist (the caller decides)."""
    lots = (db.session.query(StockLot)
            .filter(StockLot.group_id == gid, StockLot.location_id == loc.id,
                    StockLot.finished.is_(False)).all())
    cats, names = {}, []
    for s in lots:
        p = s.product
        if p:
            if p.category:
                cats[p.category] = cats.get(p.category, 0) + 1
            if p.name and len(names) < 12:
                names.append(p.name)
    top_cats = [c for c, _ in sorted(cats.items(), key=lambda kv: -kv[1])[:4]]

    if use_llm:
        try:
            from .assistant import _cfg, _complete, _PROVIDERS
            cfg = _cfg()
            if cfg["provider"] in _PROVIDERS:
                system = ("Write ONE short sentence (max ~14 words) describing what this "
                          "kitchen storage area normally holds. No preamble.")
                user = (f"Area: {loc.name} (kind={loc.kind}). "
                        f"Currently holds categories: {', '.join(top_cats) or 'none yet'}. "
                        f"Example items: {', '.join(names) or 'none'}.")
                reply = (_complete(cfg, system, user) or "").strip().strip('"')
                if reply:
                    return reply[:240]
        except Exception:  # noqa: BLE001 — fall through to the heuristic
            pass

    # Heuristic sentence.
    kind_hint = {"freezer": "Frozen items", "fridge": "Chilled and perishable items",
                 "pantry": "Dry and ambient staples", "cupboard": "Dry goods and staples",
                 "wine_cellar": "Wine and bottles"}.get(loc.kind, "Assorted items")
    if top_cats:
        return f"{kind_hint} — mostly {', '.join(top_cats)}."
    return f"{kind_hint}."


def location_scope_ids(gid, location_id) -> set:
    """`location_id` plus every location nested under it, as an id set.

    "Use up the milk in the Garage" must also see the Garage Fridge inside it —
    a user names the area they think in, not the leaf. Group-scoped (a foreign
    id yields an empty set, so it can never widen a scope onto another tenant's
    stock) and cycle-guarded, since a malformed parent chain would otherwise
    loop forever. Returns an empty set for an unknown/foreign id — callers treat
    that as "no such location" rather than "everything"."""
    if not location_id:
        return set()
    root = db.session.get(Location, location_id)
    if root is None or root.group_id != gid:
        return set()
    scope, stack = set(), [root]
    while stack:
        loc = stack.pop()
        if loc.id in scope:
            continue  # cycle guard
        scope.add(loc.id)
        stack.extend(c for c in loc.children if c.group_id == gid)
    return scope
