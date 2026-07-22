"""Explicit, safe name→product/concept matching.

Replaces scattered substring matching (planning, assistant, MCP) with one ranked
service that returns candidates + confidence + reasons. Reads may aggregate plausible
matches; mutations must not guess across a material boundary (item_type / allergen).
See docs/stock-redesign/adr/0003-matching-service.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..extensions import db
from ..models import Product


def normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # crude singularization so "eggs" ~ "egg", "scallions" ~ "scallion"
    if len(s) > 3 and s.endswith("s") and not s.endswith("ss"):
        s = s[:-1]
    return s


@dataclass
class Candidate:
    product: object
    score: float
    reasons: list = field(default_factory=list)


def _concept_terms(concept) -> list[str]:
    if not concept:
        return []
    terms = [concept.canonical_name] + list(concept.aliases or [])
    return [normalize(t) for t in terms if t]


def match_products(gid, query, *, item_types=None, allow_substring=True) -> list[Candidate]:
    """Rank a household's products against `query`. Higher score = better.
      exact name/barcode 1.0 · alias/concept 0.9 · family 0.8 · substring 0.5.
    `item_types` (e.g. {"food","beverage"}) filters out non-matching kinds so a
    recipe never matches a non-food consumable."""
    nq = normalize(query)
    if not nq:
        return []
    out: list[Candidate] = []
    for p in db.session.query(Product).filter_by(group_id=gid).all():
        if item_types and (p.item_type or "food") not in item_types:
            continue
        np = normalize(p.name)
        score, reasons = 0.0, []
        if (query or "").strip().lower() == (p.barcode or "").strip().lower() and p.barcode:
            score, reasons = 1.0, ["barcode"]
        elif np == nq:
            score, reasons = 1.0, ["exact name"]
        elif nq in _concept_terms(p.concept):
            score, reasons = 0.9, ["alias/concept"]
        elif p.family and normalize(p.family) == nq:
            score, reasons = 0.8, ["family"]
        elif allow_substring and (nq in np or np in nq):
            score, reasons = 0.5, ["substring"]
        if score > 0:
            out.append(Candidate(product=p, score=score, reasons=reasons))
    out.sort(key=lambda c: (-c.score, c.product.name.lower()))
    return out


@dataclass
class Resolution:
    product: object          # the resolved product, or None
    ambiguous: bool
    candidates: list         # Candidate list (for the caller to disambiguate)


def resolve_for_mutation(gid, query, *, item_types=None) -> Resolution:
    """Pick ONE product for a mutating action. Returns a resolved product only when
    unambiguous — an exact/alias top match clearly ahead of the rest. Otherwise
    ambiguous=True with candidates, so the caller asks instead of guessing across
    materially-different products (ADR-0003)."""
    cands = match_products(gid, query, item_types=item_types)
    if not cands:
        return Resolution(None, False, [])
    top = cands[0]
    runner = cands[1].score if len(cands) > 1 else 0.0
    # Confident only if the top is a strong match AND clearly ahead of the next.
    if top.score >= 0.8 and (top.score - runner) >= 0.3:
        return Resolution(top.product, False, cands)
    if len(cands) == 1 and top.score >= 0.5:
        return Resolution(top.product, False, cands)
    return Resolution(None, True, cands)
