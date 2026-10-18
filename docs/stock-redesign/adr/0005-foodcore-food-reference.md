# ADR-0005: foodcore is the shared food-reference source; its verdicts stay beside Edibl's lexicon, not inside it

**Status:** accepted · **Date:** 2026-10-18 · **Implemented:** conversion + barcode allergens

## Context
Edibl could not answer two food questions. `quantity.convert` refuses mass↔volume because it
has no food and therefore no density (ADR-0002, correctly). And a barcode scan read
`product_name`, `brands`, `categories_tags` and `nutriments` from Open Food Facts while
throwing away both allergen fields. myMeal had already solved both against a USDA-backed
dataset where every answer carries provenance and an unjustifiable answer comes back refused;
that logic now lives in `foodcore`, a dependency-free library both apps install.

## Decision
- **One seam.** `services/food_reference.py` is the only module that imports foodcore, uses
  only its documented public API, and degrades every failure (no library, no dataset asset,
  unreadable temp dir, bad data) to exactly the behaviour that preceded it.
- **Pinned to a commit.** foodcore has two consumers and SOP §8 requires a shared-code change
  to land in both together; a commit pin makes the port a diff someone has to write.
- **`convert` is unchanged.** `convert_for_food` is a separate entry point for the sites that
  name a specific food; within a dimension it is `convert` verbatim. Package, serving and
  qualitative-level units never reach the reference data.
- **foodcore's allergen verdicts are stored and read SEPARATELY from `FoodConcept.allergens`.**
  They answer different questions: `FoodConcept.allergens` is Edibl's material boundary, used
  to stop matching crossing it (ADR-0003), while foodcore's ruleset is regulatory labelling.
  Coconut is botanically a drupe and carries no allergen in a material lexicon; FDA labelling —
  and so every package label and foodcore — counts it as a tree nut. Merging the two produces
  one list that is wrong for one of its consumers.
- **Four states, no boolean.** A declaration proves presence and says nothing about absence, so
  an allergen nothing was declared about is `unknown`, emitted explicitly. No `safe` flag:
  whether a state is acceptable depends on a policy only the caller holds.

## Not decided here (deliberately)
- **`inventory/selection.py` keeps using `convert`.** A lot is a specific product, so a
  food-aware conversion is arguably right there, and foodcore's two directions do invert
  exactly (both come off the same published portion), so the round-trip is not the obstacle.
  The obstacle is identity: the only food name available is the product's free text, resolved
  against USDA *at the moment of a deduction* — the guess ADR-0003 reserves for the matching
  service. The prerequisite is a food identity chosen once and persisted on `Product`.
- **Nutrition stays as it is.** `_nutrition_from_off` and `foodcore.nutrition` overlap, and
  reconciling them means ranking an ODbL-licensed declared label value against a CC0-licensed
  measured one. That is a licence and precedence judgement, not an integration; its own task.

## Consequences
- **+** "A cup of honey" is 339 g, with provenance, instead of a refusal — and "rice" is still
  a refusal, because raw and cooked differ by 17% per cup.
- **+** A scan now carries declared allergens *and* the precautionary traces line, with the
  gaps visible rather than implied.
- **−** Nothing persists the allergen block yet; it reaches the scan suggestion only. Storing
  it is a schema change and gets its own review.
- **−** A ~2.5 MB asset expands to ~10 MB in the container's temp dir on first use.
