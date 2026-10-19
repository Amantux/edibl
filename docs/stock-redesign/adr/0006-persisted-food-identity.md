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

**So the planner stopped using two directions at all.** It converted a lot into the demand
unit to decide what the lot covered, and converted the demand into lot units to decide the
take. Those are opposite directions, and mixing them does not merely lose precision — **it
silently loses stock**: a 6 tbsp demand against a 10 g alfalfa lot credited itself 4.85 tbsp
(the reverse scale) for a lot actually worth 3.33 tbsp, so it reported `shortfall = 0`, drew
13.4 g of the 18 g owed, and never touched the next lot.

`plan_consumption` now probes the conversion **once per lot** —
`per_demand_unit = convert_(1, demand_unit, lot_unit)` — and derives *both* availability
(`avail / factor`) and take (`remaining × factor`) from that single factor. The conversion is
linear (`grams = amount × a per-food factor`), verified dataset-wide with zero non-linear
conversions, so one probe characterises it exactly.

Three things fall out, which is why this is the right shape rather than another correction
term:
- **Over-drawing becomes structurally impossible**, not clamped: `remaining < avail / factor`
  implies `remaining × factor < avail`. Both the clamp and its credit correction are deleted.
- **A full draw takes the lot's own quantity** — a fact, not a conversion — credited with
  what that quantity is worth *on the same scale*.
- **The two branches can no longer disagree.** They did: one 10 g alfalfa lot reported a
  0.6667 tbsp shortfall at a 4 tbsp demand (right) and 1.1515 at 6 tbsp (should be 2.6667).

**A silent under-draw is as wrong as an over-draw**, and it is precisely the failure mode
that "skip the lot → visible shortfall" was supposed to rule out. Both halves of this were
caught in adversarial review, the second after a first fix addressed only the partial branch
while leaving the identical mistake in the full-draw branch one line away. The tests now
parametrise *both* demands over *both* branches, because a single case is what let the
inconsistency hide.

Concretely: a 10 g lot of alfalfa against a large tbsp demand used to compute a take of
**14.5 g from a 10 g lot** — a 45% over-draw, caught only by `consume_lot`'s own clamp. It is
now 10 g.

**Migration parent.** `0014` names `0012_api_token_access` while
`0013_product_allergens` is in flight on a sibling branch, and is re-pointed to `0013` at
merge. A revision naming a parent that does not exist yet is not a cosmetic problem: Alembic
raises resolving the graph and `app/__init__.py` upgrades in-process at startup, so the
container never serves a request. A branch must be bootable on its own.

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
