"""Storing and reading a packaged product's LABEL allergen verdicts.

`services/food_reference.py` turns Open Food Facts' two declaration fields into
foodcore's four-state verdicts. This module is the only thing that puts those on
a `Product` and the only thing that reads them back, so the honesty rules below
hold in exactly one place.

**Not Edibl's allergen lexicon.** `FoodConcept.allergens` is a material boundary
used to stop matching crossing it (ADR-0003); these verdicts are regulatory
labelling read off one package. Coconut carries no allergen in a material lexicon
and *is* a tree nut under FDA labelling, so merging the two produces one list that
is wrong for one of its consumers. Nothing here reads, writes or reconciles
`FoodConcept.allergens`, and nothing should start (ADR-0005).

Three rules this module exists to enforce
-----------------------------------------
1. **Never screened is not "nothing found".** `Product.label_allergens` is NULL
   until a label has actually been read. A screened product stores an explicit
   verdict for every ruleset key, so a label that declares nothing is twelve
   `unknown` verdicts — never an empty list, and never an absent record. The two
   are different answers to different questions and both stay expressible.
2. **An empty or absent record is never a clearance.** There is no code path here
   that turns "no verdicts" into any kind of "free from". `stored_allergens`
   returns None, and None means *we do not know*, which the UI must say out loud.
3. **A verdict is only as interpretable as its ruleset version.** A blob stored
   without one cannot be re-read later against the rules that produced it, which
   makes it worse than no verdict: it looks like data and cannot be checked.
   `stored_allergens` refuses it.

There is deliberately **no boolean anywhere** — no `safe`, no `free`, no `ok`.
foodcore's `AllergenVerdict.__bool__` raises `TypeError` on purpose, because
whether a state is acceptable depends on a policy (a medical allergy excludes
`unknown`, a dislike does not) that only the caller holds. This module reports
states and never judges them.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger("edibl.allergens")

SOURCE_OFF = "openfoodfacts"

# `allergen_source` marker for "a lookup completed and produced nothing storable"
# — Open Food Facts has no record for this barcode, or foodcore was unavailable.
# It keeps the backfill from re-querying a free community service forever, and it
# is deliberately NOT a verdict: `label_allergens` stays NULL, so
# `stored_allergens` still reports the product as never screened. The parallel
# with nutrition's `{}` sentinel is intentional, but the sentinel had to move OFF
# the verdict column — an allergen record that exists and says nothing would
# render as "screened, nothing found", which is a false clearance.
SCREEN_ATTEMPTED = "none"

# Evidence kinds that carry no information: foodcore emits one per `unknown`
# verdict and every copy is the same constant sentence plus the same provenance.
# Storing nine identical copies per product cost ~6 kB where the compact form
# costs well under one, so they are dropped on the way IN and re-stated once, at
# the API boundary, by `block_out`. The verdicts themselves are never dropped —
# an explicit `unknown` per ruleset key is the whole point (ADR-0005).
_ABSENT = "absent"


def compact(block: dict | None) -> dict | None:
    """A `food_reference.allergens_from_declarations` block, compacted for storage.

    Keeps every verdict and everything that varies between them; drops only what
    is constant across the whole block (license, ruleset version, provenance) by
    hoisting it to the top, and the boilerplate evidence attached to `unknown`.

    None in → None out: foodcore being unavailable is "we did not screen", and it
    must not be stored as though we had.
    """
    if not isinstance(block, dict):
        return None
    verdicts = block.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        # A screening that produced no verdicts at all did not happen. Storing it
        # would create a record that renders as a screened product with nothing
        # found — the exact false clearance this module exists to prevent.
        return None

    # Every verdict carries the same provenance; take it from the first that has
    # one rather than storing twelve copies.
    retrieved, source_id = "", ""
    for v in verdicts:
        for e in (v.get("evidence") or []):
            prov = e.get("provenance") or {}
            retrieved = retrieved or str(prov.get("retrieved") or "")
            source_id = source_id or str(prov.get("sourceId") or "")
        if retrieved and source_id:
            break

    out_verdicts = []
    for v in verdicts:
        if not isinstance(v, dict) or not v.get("allergen"):
            continue
        row = {"allergen": v["allergen"], "label": v.get("label") or v["allergen"],
               "state": v.get("state") or "unknown",
               "rulesetKeyed": bool(v.get("rulesetKeyed")),
               # foodcore's own property, stored rather than recomputed: it is
               # part of what the ruleset said at screening time, and deriving it
               # here would be a second opinion on foodcore's semantics.
               "needsReview": bool(v.get("needsReview"))}
        terms, notes = [], []
        for e in (v.get("evidence") or []):
            if e.get("kind") == _ABSENT:
                continue
            term = (e.get("matchedTerm") or "").strip()
            if term and term not in terms:
                terms.append(term)
            note = (e.get("note") or "").strip()
            if note and note not in notes:
                notes.append(note)
        if terms:
            row["terms"] = terms
        if notes:
            # One note: it explains WHY a state is hedged (a declared oat tag
            # softened to `may_contain` gluten by rule), and the first is the
            # strongest-stated one because evidence is appended declared-first.
            row["note"] = notes[0]
        out_verdicts.append(row)

    if not out_verdicts:
        return None
    return {"source": block.get("source") or SOURCE_OFF,
            "license": block.get("license") or "",
            "rulesetVersion": str(block.get("rulesetVersion") or ""),
            "sourceId": source_id, "screenedAt": retrieved,
            "verdicts": out_verdicts,
            "unmappedTags": block.get("unmappedTags") or [],
            "unmappedCount": int(block.get("unmappedCount") or 0)}


def apply_to_product(p, block: dict | None, *, overwrite: bool = False) -> bool:
    """Store a screening on a product. True when something was written.

    `overwrite=False` keeps the first screening: a re-scan of a product that has
    already been screened should not silently replace a verdict a household may
    have acted on. The backfill uses the default; an explicit re-screen passes
    True.

    A block that does not compact to a usable screening writes NOTHING and leaves
    the product NULL — never a `{}` sentinel. Nutrition can use `{}` for "looked
    up cleanly, OFF had none" because missing calories are an absence of data; an
    allergen record that exists but says nothing would render as a screened
    product with no allergens found, which is a false clearance.
    """
    data = compact(block)
    if data is None:
        return False
    # Refuse to store what cannot be re-interpreted later. A verdict without the
    # ruleset that produced it is worse than no verdict, so this is a drop, not a
    # downgrade — and it is logged, because silently discarding a screening would
    # look exactly like OFF having no data.
    if not data.get("rulesetVersion"):
        _LOGGER.warning(
            "refusing to store an allergen screening with no ruleset version "
            "(product=%s)", getattr(p, "id", "?"))
        return False
    if not overwrite and stored_allergens(p) is not None:
        return False
    p.label_allergens = data
    p.allergen_ruleset_version = data["rulesetVersion"][:64]
    p.allergen_source = (data.get("source") or SOURCE_OFF)[:32]
    return True


def stored_allergens(p) -> dict | None:
    """This product's stored screening, or None when it has never been screened.

    None is the ONLY "no data" answer, and it means *we have not read a label for
    this product* — never "this product is free from allergens". A product that
    was screened and had nothing declared comes back as a real block whose
    verdicts are all `unknown`, which is a different answer and renders
    differently.

    A blob with no ruleset version is treated as never screened: it cannot be
    re-interpreted against the rules that produced it, so it is not a verdict.
    """
    data = getattr(p, "label_allergens", None)
    if not isinstance(data, dict):
        return None
    verdicts = data.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        return None
    if not (data.get("rulesetVersion")
            or getattr(p, "allergen_ruleset_version", "")):
        return None
    return data


# Severity order for display: the states that want attention first, `unknown`
# ABOVE the quiet ones. `unknown` is not a quiet state and must never be sorted
# or collapsed into them — "we did not check" belongs where it will be read.
_ORDER = {"contains": 0, "may_contain": 1, "unknown": 2}


def block_out(p) -> dict | None:
    """The product's screening as API JSON, or None when never screened.

    Verdicts come back sorted by severity and carry an explicit `unknown` for
    every ruleset key, because a short list of findings read as a complete one is
    the failure this feature exists to prevent. `declaresNothing` says the label
    was READ and declared nothing — which is why it can only ever appear on a
    block, and why its absence (a None return) is a different statement.
    """
    data = stored_allergens(p)
    if data is None:
        return None
    verdicts = sorted(
        data["verdicts"],
        # `?? -1` equivalent: a state this version does not know about sorts
        # FIRST, not last. Something unrecognised belongs where it will be read.
        key=lambda v: (_ORDER.get(v.get("state"), -1), v.get("label") or ""))
    return {
        "source": data.get("source") or SOURCE_OFF,
        "license": data.get("license") or "",
        "rulesetVersion": data.get("rulesetVersion")
        or getattr(p, "allergen_ruleset_version", ""),
        "screenedAt": data.get("screenedAt") or "",
        "verdicts": verdicts,
        # True when a label was read and declared NOTHING. Not a clearance, and
        # named so it cannot be mistaken for one: it describes the label, not the
        # food. The UI says so in words.
        "declaresNothing": all(v.get("state") == "unknown" for v in verdicts),
        "unmappedTags": data.get("unmappedTags") or [],
        "unmappedCount": int(data.get("unmappedCount") or 0),
        # Stated once here instead of stored nine times per product: foodcore
        # attaches this sentence to every `unknown` verdict and every copy is
        # identical. See `compact`.
        "unknownMeans": ("Open Food Facts declares nothing about this allergen "
                         "for this product; absence from a label's allergen "
                         "list is not a clearance"),
    }
