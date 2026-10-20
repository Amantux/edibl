# ADR-0005: foodcore is the shared food-reference source; its verdicts stay beside Edibl's lexicon, not inside it

**Status:** accepted · **Date:** 2026-10-18 · **Updated:** 2026-10-19 (allergen
persistence — see that section; the UI prohibition in *Reachability* is lifted).
**Implemented:** the allergen half end-to-end (stored on `Product`, served on
`product_out`, rendered on the product sheet). `convert_for_food` is still the
seam only and has no production consumer.

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
- ~~**`inventory/selection.py` keeps using `convert`.**~~ **Superseded — see ADR-0006.**
  The identity prerequisite named here was built (`Product.food_key` +
  `food_key_dataset_version`), and selection now converts food-aware for products that carry
  one. **One claim in the original text was wrong and is corrected there:** "foodcore's two
  directions do invert exactly (both come off the same published portion)" is true of honey —
  the only food the round-trip test covered — and false in general. USDA publishes
  per-food portions that contradict each other (alfalfa sprouts: 1 cup = 33 g *and*
  1 tbsp = 3 g, where 16 tbsp/cup implies 48 g), and 4.7% of mass↔volume round-trips over the
  shipped dataset do not invert, the worst by 50%. The round-trip was removed rather than
  bounded.
- **Nutrition stays as it is.** `_nutrition_from_off` and `foodcore.nutrition` overlap, and
  reconciling them means ranking an ODbL-licensed declared label value against a CC0-licensed
  measured one. That is a licence and precedence judgement, not an integration; its own task.

## Reachability — what this does NOT yet do
State this plainly, because the code reads as a shipped feature and is not one:
- ~~**`convert_for_food` has no caller.**~~ **Superseded by ADR-0006**: `convert_for_food_key`
  — the same seam keyed on a persisted identity rather than free text — is called by
  `inventory/selection.py`. `convert_for_food` (the free-text entry point) still has no
  production caller, and deliberately so: resolving a name inside a consumption path is the
  thing ADR-0003 forbids.
- ~~**The allergen block reaches the barcode *suggestion* only.**~~ **RESOLVED** — see
  *Allergen persistence* below. The block reached `GET /products/barcode/<code>` only for a
  code you did **not** already own, because the `found: true` branch serialises a `Product`
  and `Product` had no allergen column. The prohibition this bullet carried ("no UI until the
  `found: true` branch carries it") is **lifted**: the branch now carries it.- **Only `measured`/`declared`/`calculated` conversions are returned.** foodcore labels every
  per-item count weight (a clove, a slice) `estimated`, and a bare float cannot say so, so
  those refuse. Lifting that means returning the `Outcome`, together with the unit allow-list.

## Allergen persistence (added 2026-10-19, migration `0013_product_allergens`)
The asymmetry above is fixed; this section records the decisions that fixing it forced.

- **Stored on `Product`, in three columns.** `label_allergens` (JSON) holds the verdicts,
  `allergen_ruleset_version` and `allergen_source` record what produced them and where they
  came from. The version is denormalised out of the blob so a ruleset bump can *find* the
  rows it stranded without parsing every product's JSON.
- **`NULL` means never screened, and that is not "screened, nothing found".** A screened
  product stores an explicit verdict for every ruleset key, so a label declaring nothing is
  twelve `unknown` verdicts — never an empty list. Both states stay expressible in storage
  *and* render differently: never-screened gets a notice and no verdict rows, a label that
  declared nothing gets a caveat paragraph, the rows, and the date it was read.
- **A verdict with no ruleset version is refused on write and read as never-screened.** It
  cannot be re-interpreted against the rules that produced it, which makes it worse than no
  verdict: it looks like data and cannot be checked.
- **Named `label_allergens`, not `allergens`.** `FoodConcept.allergens` is Edibl's material
  lexicon and the two must not be merged (the coconut case above). The column name is the
  cheapest place to keep that boundary visible.
- **Served inline on `product_out`, NOT from a dedicated endpoint.** myMeal splits recipe
  safety out for four reasons; three are consequences of myMeal screening ON READ while Edibl
  captures AT SCAN, so they do not transfer — Edibl has no Product versioning to poison, no
  per-render CPU cost (the verdicts are stored), and no live engine that can be "unavailable"
  at read time (a `NULL` column says "never screened" as a value, not an error). The fourth,
  a stale verdict travelling in an export, is answered by the block carrying its own
  `rulesetVersion` and `screenedAt`. Inline is also the more consistent choice *within* Edibl:
  `nutrition` is already an OFF-derived, stored, provenance-carrying blob served this way.
- **The backfill's "already attempted" marker lives on `allergen_source`, not in the verdict
  column.** Nutrition marks a fruitless lookup with a `{}` sentinel; allergens cannot, because
  a record that exists and says nothing renders as a screened product with nothing found.
- **Still not reconciled with nutrition.** The licence-and-precedence judgement below is
  untouched by this change.

## Consequences
- **+** "A cup of honey" is 339 g instead of a refusal — and "rice" is still a refusal,
  because raw and cooked differ by 17% per cup.
- **+** A scan now reads declared allergens *and* the precautionary traces line, keyed through
  foodcore's phrase rules so OFF's whole taxonomy lands (`en:almonds` → tree nuts,
  `en:sulphur-dioxide-and-sulphites` → sulfites), with the gaps stated rather than implied.
- **−** A ~2.5 MB asset expands to ~10 MB in a private 0700 temp directory on first use.
- **−** The commit pin is enforceable only on Edibl's side while foodcore's source lives in
  myMeal's tree (see `backend/requirements.txt`). Extracting foodcore to its own repository is
  what would make the lockstep a mechanism instead of a convention.
