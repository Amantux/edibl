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


# Score tiers. The gaps are deliberately wide so a stronger KIND of evidence
# always beats a weaker one no matter how many weak hits pile up: any name/alias
# evidence outranks brand, category and description hits.
#
# MEANINGFUL is the floor for "real evidence the user meant this product".
# Brand/category/description sit BELOW it: they enrich ranking (and let "the
# Califia one" or "that creamy soup base" find something at all) but on their own
# they are never enough to pick between several products.
SCORE_EXACT = 1.0
SCORE_ALIAS = 0.9
SCORE_FAMILY = 0.8
SCORE_SUBSTRING = 0.5
MEANINGFUL = SCORE_SUBSTRING
SCORE_BRAND = 0.4
SCORE_BRAND_CONTAINS = 0.3
SCORE_CATEGORY = 0.3
SCORE_DESCRIPTION = 0.2


def match_products(gid, query, *, item_types=None, allow_substring=True) -> list[Candidate]:
    """Rank a household's products against `query`. Higher score = better.
      exact name/barcode 1.0 · alias/concept 0.9 · family 0.8 · substring 0.5
      · brand 0.4/0.3 · category 0.3 · description 0.2.
    The last three are WEAK signals — see MEANINGFUL. They improve ranking and
    surface products a bare name match would miss, but never resolve a mutation
    on their own. `item_types` (e.g. {"food","beverage"}) filters out
    non-matching kinds so a recipe never matches a non-food consumable."""
    nq = normalize(query)
    if not nq:
        return []
    out: list[Candidate] = []
    for p in db.session.query(Product).filter_by(group_id=gid).all():
        if item_types and (p.item_type or "food") not in item_types:
            continue
        np = normalize(p.name)
        nbrand = normalize(p.brand or "")
        score, reasons = 0.0, []
        if (query or "").strip().lower() == (p.barcode or "").strip().lower() and p.barcode:
            score, reasons = SCORE_EXACT, ["barcode"]
        elif np == nq:
            score, reasons = SCORE_EXACT, ["exact name"]
        elif nq in _concept_terms(p.concept):
            score, reasons = SCORE_ALIAS, ["alias/concept"]
        elif p.family and normalize(p.family) == nq:
            score, reasons = SCORE_FAMILY, ["family"]
        # The reverse containment (product name inside the query) is what lets
        # "whole milk" find "Milk", but it needs a length floor: a product named
        # "A" is a substring of almost every query, which used to let it outrank
        # genuine matches.
        elif allow_substring and (nq in np or (len(np) >= 3 and np in nq)):
            score, reasons = SCORE_SUBSTRING, ["substring"]
        elif nbrand and nbrand == nq:
            score, reasons = SCORE_BRAND, ["brand"]
        elif nbrand and nq in nbrand:
            score, reasons = SCORE_BRAND_CONTAINS, ["brand"]
        elif normalize(p.category or "") == nq:
            score, reasons = SCORE_CATEGORY, ["category"]
        # `search_text` is the AI-generated searchable description; `notes` is the
        # household's own. Both are free text, so both are weakest-tier evidence.
        elif nq in normalize(f"{p.search_text or ''} {p.notes or ''}"):
            score, reasons = SCORE_DESCRIPTION, ["description"]
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

    # Weak-only evidence (brand/category/description) never picks BETWEEN
    # products — "creamy" matching three descriptions is a question, not an
    # answer. Alone it still resolves, so "the Califia one" works.
    if top.score < MEANINGFUL:
        return Resolution(top.product, False, cands) if len(cands) == 1 \
            else Resolution(None, True, cands)

    # A UNIQUE exact name/barcode match is unambiguous by KIND: exact identity
    # beats softer evidence, so an alias (0.9) or family (0.8) rival within the
    # 0.3 dominance gap must not turn it into a question. Only another EXACT
    # match (two products literally named the same) is real ambiguity.
    if top.score >= SCORE_EXACT:
        exact_rivals = [c for c in cands[1:] if c.score >= SCORE_EXACT]
        if not exact_rivals:
            return Resolution(top.product, False, cands)

    # Judge dominance only against REAL rivals: a description-only hit is
    # supplementary ranking info, not a competing interpretation, so it must not
    # turn an otherwise-clear match into a question.
    contenders = [c for c in cands[1:] if c.score >= MEANINGFUL]
    runner = contenders[0].score if contenders else 0.0
    # Confident when the top is a strong match AND clearly ahead of the next.
    if top.score >= SCORE_FAMILY and (top.score - runner) >= 0.3:
        return Resolution(top.product, False, cands)
    if not contenders:
        return Resolution(top.product, False, cands)
    return Resolution(None, True, cands)
