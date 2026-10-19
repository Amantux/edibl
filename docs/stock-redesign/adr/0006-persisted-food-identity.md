# ADR-0006: A food identity is chosen once and persisted on `Product`; deductions look it up, never match it

**Status:** accepted · **Date:** 2026-10-19 · **Implemented:** fully — `Product.food_key`,
`Product.food_key_dataset_version`, migration `0014`, and the first production caller of the
food-aware conversion seam.

## Context
ADR-0005 shipped `convert_for_food` with **no production caller**. The arithmetic was never
the blocker. The blocker was **identity**: the only food name available inside
`inventory/selection.py` is the product's free text, and resolving it against USDA *at the
moment of a deduction* is precisely the guess ADR-0003 reserves for the matching service. A
wrong resolution there does not merely return a bad number — it silently deducts the wrong
amount from someone's pantry.

ADR-0005 named the prerequisite exactly: *"a food identity chosen once and persisted on
`Product`"*. This is that.

## Decision

- **Two columns on `Product`.** `food_key` is a foodcore dataset key (`usda:169640`);
  `food_key_dataset_version` is the snapshot it was chosen in (`SR Legacy 2018-04`). Both
  default to `""`, which means *no identity* and is the safe, expected state.
- **Deductions perform an exact lookup, never a match.** `Dataset.food_record(key)` is a
  primary-key read. `services.matching` is not involved, no free text is scored, and there is
  no code path from a consumption request to `resolve_food`.
- **Resolution is an explicit, per-product human act** — `PUT /products/<id>/food-identity` —
  and **not** product creation, not a bulk backfill. A confident match made at create time is
  still a match nobody looked at, derived from text that is usually a brand line ("Tesco
  Finest Clear Honey"), and it would land on thousands of rows where no one would ever see it.
  One product, one decision, a human present.
- **A low-confidence or ambiguous match persists NOTHING.** foodcore refuses to choose between
  raw and cooked rice (185 g vs 158 g per cup); so do we. The endpoint returns **409 with
  candidates** — the same shape `consume` already uses for an ambiguous product — and leaves
  the row untouched. An identity stored from a guess is worse than no identity, because every
  later conversion inherits it invisibly.
- **The stored snapshot version is enforced, not decorative.** A key chosen against a
  different dataset than the one loaded falls back to plain `convert` and is reported as
  `stale`, because foodcore keys are provider ids a rebuild may legitimately re-point.
- **`convert`'s contract is unchanged**, and so is `convert_for_food`'s. The new entry point
  is `convert_for_food_key`, which shares one body and one unit allow-list with its
  free-text sibling so the refusal rules cannot drift apart.
- **The `estimated` / `inferred` refusal stays.** A persisted identity makes the *food*
  certain; it says nothing about whether foodcore applied a convention, and a bare float still
  cannot carry that. Widening it means returning the `Outcome`, not relaxing the check.

## The round-trip, and the claim this ADR corrects

ADR-0005 stated that foodcore's two directions *"do invert exactly (both come off the same
published portion), so the round-trip is not the obstacle."* **That generalised from a single
food.** The round-trip test covered honey, which publishes one cup portion and therefore does
invert. Measured across all 7,793 foods in the shipped snapshot:

| round-trip error | share of 20,322 mass↔volume round-trips |
|---|---|
| exact (<1e-9) | 95.3% |
| <1% | 3.1% |
| 1–5% | 1.1% |
| 5–20% | 0.4% |
| **≥20%** | **0.13%** (worst: agave syrup tsp, **50.5%**) |

The cause is upstream, not arithmetic: USDA publishes per-food portions that contradict one
another. Alfalfa sprouts carry *1 cup = 33 g* **and** *1 tbsp = 3 g*, though 16 tbsp/cup
implies 48 g. foodcore answers `tbsp→g` from the tbsp row and `g→tbsp` from the cup-derived
density, so round-tripping compounds the gap. Errors concentrate in exactly the units recipes
use — `tbsp` 15.9% of foods, `tsp` 6.6%, `cup` 2.0% — while `ml`/`l`/`fl oz`/`kg` are clean.

**So the round-trip was removed, not bounded.** `plan_consumption` converted a lot into the
demand unit and then converted the take back again. That second conversion only existed for
the *full-draw* case — where the answer is already known exactly, because the take simply
*is* the lot's own quantity. Taking `avail` directly eliminates the error class rather than
tolerating it, and is exactly equivalent under plain `convert`, whose round-trip was already
exact. A **partial** draw remains a single forward conversion (never a round-trip) and is
**clamped to the lot's quantity**, because the availability figure came from the opposite
direction and the two can disagree.

**A clamp must reduce what it credits, not just what it takes.** Clamping the take while
still crediting the full outstanding demand drives `remaining` to zero, so the planner
reports `shortfall = 0` and stops spilling — a 4 tbsp request took 10 g from a lot that owed
12 g, left a 1 kg lot untouched, and told the user nothing was short. **A silent under-draw
is as wrong as an over-draw**, and it is the failure mode "skip the lot → visible shortfall"
was supposed to rule out. The credit is therefore the forward conversion *inverted for the
clamped amount* (`remaining × avail ÷ take_lot`), which is exact because the conversion is
linear, and which keeps the take and its credit on one consistent scale rather than mixing in
the direction we just decided not to trust. Caught in adversarial review; the original test
asserted only the take and was green while the bug shipped.

Concretely: a 10 g lot of alfalfa against a large tbsp demand used to compute a take of
**14.5 g from a 10 g lot** — a 45% over-draw, caught only by `consume_lot`'s own clamp. It is
now 10 g.

## Consequences
- **+** "Use 2 cups of honey" draws 678 g from a 1 kg jar instead of reporting a shortfall.
- **+** The failure direction is unchanged and still safe: no identity, a stale one, an
  unknown key, an ambiguous name, or an `estimated` answer all fall back to `convert` and to
  today's visible shortfall. The new behaviour is strictly opt-in per product.
- **+** An entire class of round-trip error is gone from consumption planning, including for
  products with no food identity at all.
- **−** Two more columns, and a decision users must make per product before they see any
  benefit. Deliberate: the alternative is making it for them, invisibly.
- **−** No UI yet. The identity is exposed on `product_out` and through three endpoints that
  report `none`/`current`/`stale`/`unknown` and allow clearing; building the picker is its own
  task. Per ADR-0005's precedent, a half-surfaced identity is worse than none — whatever UI
  lands must show what the identity *is* and offer the clear.
