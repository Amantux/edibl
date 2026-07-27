"""AI tooling jobs (ported from HomeHoard): auto-categorize products (assign a
category) and propose family groupings.

Confidence-gated: a high-confidence category within the known CATEGORIES is applied
automatically; anything else (new/unknown category, low confidence, any cluster
grouping) lands in the review queue (``AiSuggestion``, status ``pending``). Review
decisions (accepted/rejected) feed back as few-shot in-context-learning examples to
later runs.
"""
from __future__ import annotations

import json
import logging

from flask import current_app

from ..extensions import db
from ..models import AiSuggestion, CATEGORIES, Product, utcnow
from . import assistant

_LOGGER = logging.getLogger("edibl.tooling")
_CATEGORIZE_MAX = 200
_CLUSTER_ITEMS_MAX = 300


def _threshold() -> float:
    return float(current_app.config.get("AI_CONFIDENCE_THRESHOLD", 0.8))


def _confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, c))


def _cfg_or_raise():
    from .jobs import JobError
    cfg = assistant._cfg()
    if cfg["provider"] not in assistant._PROVIDERS:
        raise JobError("No LLM provider configured (set one in Settings).")
    return cfg


def _complete_obj(cfg, system, user) -> dict:
    """One completion → parsed JSON object, or {} on any failure."""
    try:
        reply = assistant._complete(cfg, system, user)
        s, e = reply.find("{"), reply.rfind("}")
        if s != -1 and e > s:
            data = json.loads(reply[s:e + 1])
            return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 - best-effort
        _LOGGER.info("completion failed: %s", exc)
    return {}


def _example_text(sugg) -> str:
    return sugg.product.name if sugg.product else sugg.label


def icl_examples(gid, kind, limit=8) -> str:
    rows = (db.session.query(AiSuggestion)
            .filter(AiSuggestion.group_id == gid, AiSuggestion.kind == kind,
                    AiSuggestion.status.in_(("accepted", "rejected")))
            .order_by(AiSuggestion.resolved_at.desc()).limit(limit * 3).all())
    pos = [r for r in rows if r.status == "accepted"][:limit]
    neg = [r for r in rows if r.status == "rejected"][:limit]
    parts = []
    if pos:
        parts.append("The user ACCEPTED (do more like these): "
                     + "; ".join(f"'{_example_text(r)}' → {r.label}" for r in pos))
    if neg:
        parts.append("The user REJECTED (avoid these): "
                     + "; ".join(f"'{_example_text(r)}' → {r.label}" for r in neg))
    return "\n".join(parts)


# --- categorize (product.category) ----------------------------------------
_CATEGORIZE_SYSTEM = "You categorize grocery/pantry products. Respond ONLY with JSON."


def run_categorize(job) -> dict:
    from .jobs import bump
    gid = job.group_id
    opts = job.params or {}
    note = str(opts.get("note") or "")
    cfg = _cfg_or_raise()
    examples = icl_examples(gid, "categorize")
    threshold = _threshold()
    cats = ", ".join(CATEGORIES)

    products = (db.session.query(Product)
                .filter(Product.group_id == gid,
                        db.or_(Product.category.is_(None), Product.category == "",
                               Product.category == "other"))
                .order_by(Product.created_at.asc()).limit(_CATEGORIZE_MAX).all())
    bump(job, done=0, total=len(products))
    applied = queued = 0
    for i, p in enumerate(products, 1):
        prompt = (f"Choose the single best category for this product from: {cats}.\n"
                  + (f"{examples}\n" if examples else "")
                  + (f"User guidance: {note}\n" if note else "")
                  + f"Product: {p.name}. Brand: {p.brand or '—'}.\n"
                  'Respond ONLY as JSON: {"category": "<one of the list>", '
                  '"confidence": 0.0-1.0, "rationale": "<short>"}.')
        out = _complete_obj(cfg, _CATEGORIZE_SYSTEM, prompt)
        name = (out.get("category") or "").strip().lower()[:64]
        if name:
            conf = _confidence(out.get("confidence"))
            known = name in CATEGORIES
            common = dict(kind="categorize", label=name, confidence=conf,
                          rationale=(out.get("rationale") or "")[:500],
                          product_id=p.id, group_id=gid)
            if conf >= threshold and known:
                p.category = name
                db.session.add(AiSuggestion(status="accepted", resolved_at=utcnow(), **common))
                applied += 1
            else:
                db.session.add(AiSuggestion(status="pending", **common))
                queued += 1
        bump(job, done=i)
    return {"applied": applied, "queued": queued, "scanned": len(products)}


# --- cluster (product.family) ---------------------------------------------
_CLUSTER_SYSTEM = "You group grocery products into families. Respond ONLY with JSON."


def run_cluster(job) -> dict:
    from .jobs import bump
    gid = job.group_id
    opts = job.params or {}
    note = str(opts.get("note") or "")
    cfg = _cfg_or_raise()
    products = (db.session.query(Product)
                .filter(Product.group_id == gid)
                .order_by(Product.created_at.asc()).limit(_CLUSTER_ITEMS_MAX).all())
    bump(job, done=0, total=1)
    if not products:
        return {"proposed": 0, "scanned": 0}
    listing = "\n".join(f"{idx}: {p.name}" for idx, p in enumerate(products))
    prompt = ("Group these grocery products into a few display families (e.g. 'Milk' "
              "for whole + skimmed milk). Only group items that clearly belong "
              "together; leave singletons out.\n"
              + (f"User guidance: {note}\n" if note else "")
              + f"Products (index: name):\n{listing}\n"
              'Respond ONLY as JSON: {"clusters": [{"name": "...", "indices": [0,3], '
              '"confidence": 0.0-1.0}]}.')
    data = _complete_obj(cfg, _CLUSTER_SYSTEM, prompt)
    valid = {p.id for p in products}
    proposed = 0
    for c in (data.get("clusters") or []):
        name = (c.get("name") or "").strip()[:255]
        seen, idxs = set(), []
        for n in (c.get("indices") or []):
            if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < len(products) and n not in seen:
                seen.add(n)
                idxs.append(n)
        ids = [products[n].id for n in idxs if products[n].id in valid]
        if not name or len(ids) < 2:
            continue
        db.session.add(AiSuggestion(
            kind="cluster", status="pending", label=name,
            confidence=_confidence(c.get("confidence")),
            rationale=(c.get("rationale") or "")[:500],
            payload={"itemIds": ids}, group_id=gid))
        proposed += 1
    bump(job, done=1, total=1)
    return {"proposed": proposed, "scanned": len(products)}


# --- apply / reject --------------------------------------------------------
def apply_suggestion(sugg) -> dict:
    """Accept a pending suggestion: set the product's category (categorize) or the
    shared family on every member product (cluster). Idempotent; group-scoped."""
    gid = sugg.group_id
    changed = 0
    if sugg.kind == "categorize" and sugg.product_id:
        p = db.session.get(Product, sugg.product_id)
        if p and p.group_id == gid:
            p.category = sugg.label
            changed = 1
    elif sugg.kind == "cluster":
        ids = (sugg.payload or {}).get("itemIds", [])
        for p in (db.session.get(Product, i) for i in ids):
            if p and p.group_id == gid:
                p.family = sugg.label
                changed += 1
    sugg.status = "accepted"
    sugg.resolved_at = utcnow()
    db.session.commit()
    return {"applied": changed, "label": sugg.label}


def reject_suggestion(sugg) -> None:
    sugg.status = "rejected"
    sugg.resolved_at = utcnow()
    db.session.commit()
