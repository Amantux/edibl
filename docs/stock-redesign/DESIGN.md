# Edibl Kitchen Stock Model — Redesign

> Status: **proposed** (awaiting sign-off on the model + first vertical slice).
> Governs the evolution of Edibl's stock domain from a single mutable `StockLot`
> into a coherent, event-logged kitchen model. Companion ADRs live in `adr/`.

The guiding constraint from the brief: **the smallest coherent model that handles
the scenario matrix** — not a warehouse platform, not a nullable-column patch. When
forced to choose, **truthful uncertainty over false precision.**

---

## 1. Current-state audit (evidence)

All references are `backend/app/…`. Verified by reading the code, not memory.

### 1.1 The domain today
`Product` (the "what") → `StockLot` (a scalar quantity in one `Location`), plus
`ConsumptionEvent` (consume-only), `ShelfLifeProfile` (expiry seed), `ShoppingItem`,
`PlannedItem` (myMeal demand), nested `Location`, `Group` (tenant). One module:
`models/__init__.py`.

### 1.2 The eight baked-in assumptions (each is a correctness hazard)

| # | Assumption | Where | Failure it causes |
|---|---|---|---|
| a | One scalar quantity + one unit per lot | `models/__init__.py:171-172` | Can't hold "2 cartons = 1.5 L"; **`quantity` defaults to `1`** so *unknown* silently becomes 1 |
| b | Units summed without conversion | `api/stock.py:139,143`; `services/planning.py:41-45`; `services/assistant.py:89-91` | Mixed-unit groups show a **mathematically invalid total**; group `unit` is just the first lot's |
| c | Product *name* identifies stock | `api/stock.py:60`; `services/assistant.py:74` | No canonical identity; `family` is first-writer-wins free text (`models:134`) |
| d | Substring matching is safe | `services/planning.py:42` (bidirectional `in`); `services/assistant.py:67,228,298`; `edibl_mcp.py:165` | "milk" consumes almond milk; scallions ≠ green onion; no allergen guard |
| e | Earliest-expiring lot is the intended lot | `services/assistant.py:68,250`; `api/stock.py:111-112` | Only `lots[0]` is ever touched; a consume larger than lot[0] is **clamped, not spilled** |
| f | Expiry is one definitive date | `models/__init__.py:179-181` | printed / use-by / best-by / estimated all overwrite one column; only a bool of provenance |
| g | Storage+packaging+prep+freshness = two strings | `models/__init__.py:173,176` (`"opened"` is in **both** `STORAGE_METHODS` and `FRESHNESS_LEVELS`) | Can't say "frozen **and** vacuum-sealed", "opened **and** chopped" |
| h | Delete/edit-in-place is the history | `api/stock.py:191-192,313`; `services/assistant.py:218` | Hard deletes, no ledger; `ConsumptionEvent` keys on `product_id` not the lot (`models:254`) and is the *only* event, written by consume alone |

### 1.3 The structural finding (highest leverage)
There is **no shared stock service.** Three implementations of add/consume/delete:
- **REST** `api/stock.py` — canonical, richest.
- **MCP** `edibl_mcp.py` — thin HTTP client over REST (good; inherits REST logic).
- **Assistant** `services/assistant.py` — **re-implements against the ORM**, and diverges:
  consume-with-no-qty empties the lot in REST (`stock.py:229`) but removes **1** in the
  assistant (`assistant.py:243`); assistant auto-finishes on qty≤0, REST `PUT` does not;
  assistant add omits `created_by`/`cost`/`lot_code`/location validation.

`_resolve_product` exists **three times** (`stock.py:48`, `assistant.py:72`, `data.py:97`).

### 1.4 Other integrity / UX failure modes
- **No idempotency anywhere** — a retried agent POST creates duplicate lots/events.
- Undo exists only as reverse-mutation (`unconsume` `stock.py:261`; assistant `apply_undo`
  `assistant.py:645`); the assistant path is **not idempotent** (re-applying re-adds qty).
- **`cost: Float`** — money as binary float (`models:182`), no currency.
- `expiry_date` heavily sorted/filtered but **not indexed**.
- Export (`api/data.py`) omits `ConsumptionEvent` + `PlannedItem`; re-import **duplicates
  lots** (additive, name-keyed, `test_flexible.py:106`).
- Frontend never exposes full lot editing — only freshness (`Stock.vue:225`).

### 1.5 What is genuinely good (preserve)
Product/StockLot separation; nested first-class `Location`; strict `group_id` tenancy on
every table; name-first progressively-disclosed intake (`Stock.vue:318-372`, "▸ More
options"); "Add & another" haul mode; bulk + receipt/photo extract → review → commit;
versioned export tag `"edibl":"1"`; MCP-over-REST convergence; per-product shelf-life
learning from `ConsumptionEvent` outcomes.

---

## 2. Gap table (ranked by correctness risk × user impact)

| Rank | Gap | Risk | Slice? |
|---|---|---|---|
| 1 | No event ledger; mutate-in-place + hard delete → no explainability/undo | **Correctness** | ✅ |
| 2 | Assistant re-implements stock logic → REST/agent disagree | **Correctness** | ✅ (for slice ops) |
| 3 | `quantity` default 1 conflates unknown/present/exact | **Truthfulness** | ✅ |
| 4 | Cross-dimension sums produce invalid totals | **Correctness** | ✅ |
| 5 | No idempotency → duplicate agent writes | **Correctness** | ✅ |
| 6 | Packaging/preservation/prep/quality collapsed | Fidelity | Partial (package_state only) |
| 7 | Substring matching crosses allergen/type boundaries | **Agent safety** | Phase 5 (documented) |
| 8 | Single expiry date, facts overwritten | Truthfulness | Phase 5 (schema seam in slice) |
| 9 | No canonical FoodConcept identity | Recipe compat | Phase 1-tail/5 |
| 10 | No acquisition/position split (portions/freezer) | Fidelity | Phase 4 |
| 11 | Money as float | Data integrity | Phase 1-tail |
| 12 | No reorder/reservation/replenishment semantics | Planning | Phase 5 |
| 13 | Low-confidence agent/vision can overwrite confirmed data | Agent safety | Phase 6 |

"Slice?" = touched by the first vertical slice (§8). The rest are designed here and
sequenced in §7, implemented only after the slice is correct.

---

## 3. Glossary (internal model ↔ user-facing language)

| Internal | User sees | Meaning |
|---|---|---|
| `FoodConcept` | "milk", "chicken breast" | canonical ingredient identity; recipe & substitution anchor |
| `Product` | "Fairlife whole milk 52 oz" | purchasable/repeatable variant; barcode, pack def, brand |
| `AcquisitionLot` | "bought Tuesday", "Sunday's roast" | a batch acquired/produced together (date, source, cost, original qty) |
| `StockPosition` | "the open carton in the fridge" | a current physical amount of a lot, in a place + condition |
| `Quantity` (value object) | "about half", "6", "low", "some" | value?/unit/dimension/precision/exact-or-estimated/bounds/confidence |
| `InventoryEvent` | "opened 4 days ago", "moved to garage" | one immutable recorded change (the ledger) |
| package/preservation/prep/quality state | "open", "frozen", "chopped", "overripe" | four **orthogonal** facets, not one string |
| selection policy | (invisible) "use the open one first" | deterministic, visible rule for auto-picking stock |

**In `StockLot` today, a "lot" already means a position.** The redesign renames the
concept and splits acquisition facts out of it (§4), keeping `StockLot` as a compat facade.

---

## 4. Two candidate models (scored) — Architecture Decision

Both satisfy the scenario matrix. They differ in *how* current state relates to the ledger.

### Model A — Full event-sourced 4-layer
`FoodConcept + Product + AcquisitionLot + StockPosition + InventoryEvent`, with the
**current state as a pure projection rebuilt from events** (event-sourcing proper).
`Quantity` embedded on positions/events; four state-facet tables; `ShelfLifeFact` rows;
match + policy services. `StockLot` → a compatibility view.

### Model B — Positions + append-only ledger, projection kept in sync (event-*logged*, CQRS-lite)
Evolve `StockLot` → **`StockPosition`** (add orthogonal state facets, Quantity fields,
provenance/confidence; **kept as the authoritative mutable current state**). Add a thin
**`AcquisitionLot`** (1..n positions) for batch facts. Add an **append-only
`InventoryEvent`** ledger *alongside* the projection — every command writes both, in one
transaction. Add a **lightweight `FoodConcept`** canonical layer (`Product.concept_id`,
nullable, derived from `family`). `Quantity` is a Python value object over a small set of
columns + a units/dimension registry. One shared **command layer** (`services/inventory/`)
that REST + MCP + assistant all call; plus **matching** and **selection-policy** services.
`StockLot` remains as a re-export/serializer facade.

The essential difference: **A rebuilds state from events on read; B keeps the projection
authoritative and reconcilable, with the ledger as the audit + reversal + explanation
source.** "Explainable from events" and "reversal creates a new event" are satisfied by
both; only A pays event-sourcing's read/rebuild cost.

### Scoring (1–10, higher = better)

| Dimension | A | B |
|---|---|---|
| User intuitiveness (UI hides internals either way) | 8 | 9 |
| Kitchen-domain completeness | 10 | 9 |
| Data integrity | 10 | 9 |
| Recipe compatibility | 9 | 9 |
| Agent safety | 9 | 9 |
| Offline / SQLite suitability | 5 | 9 |
| Migration ease | 3 | 7 |
| Implementation simplicity | 3 | 7 |
| Future sensor / CV support | 10 | 8 |
| Long-term maintainability (household app) | 5 | 8 |
| **Total** | **72** | **84** |

### Decision: **Model B.**
Rationale: for a single-household SQLite/Flask app, true event-sourcing (A) buys ~1 point
of integrity at the cost of SQLite suitability, migration risk, and maintainability — the
exact "enterprise warehouse" over-build the brief warns against. B is the *smallest
coherent* model: it delivers the ledger, dimension-safe quantities, orthogonal state,
reversal, shared services, and canonical identity, while every step is an **additive
expand+backfill** migration that keeps `StockLot` reads working. Recorded as
[ADR-0001](adr/0001-event-logged-not-event-sourced.md).

### Recommended ER (Model B, target)

```
Group 1─┬─* Location (nested: parent_id)
        ├─* FoodConcept 1─* Product 1─* AcquisitionLot 1─* StockPosition *─1 Location
        │                                     ▲                    │
        │                                     └── InventoryEvent ──┘  (source/dest position)
        ├─* InventoryEvent   (append-only ledger; reversal_of self-FK; idempotency_key uq)
        ├─* ShoppingItem     ├─* PlannedItem     └─* Setting
FoodConcept: canonical_name, aliases[], classification, item_type(food|beverage|consumable),
             substitution_group, default_tracking_mode, allergens[]
Product:     + concept_id(fk,null), package_def(json: unit,size,inner_count), net_contents
StockPosition (was StockLot): + acquisition_lot_id(fk,null), package_state, preservation_state,
             prep_state, quality_state, quantity_kind, q_value(Numeric/text-decimal),
             q_lower, q_upper, confidence, provenance, archived_at
AcquisitionLot: product_id, acquired_at, source, receipt_ref, original_qty, cost(Numeric),
             currency, lot_code, provenance, created_by
InventoryEvent: type, at, actor_user_id, source_app, idempotency_key(uq per group), reason,
             src_position_id, dst_position_id, delta_value(text-decimal), delta_unit,
             state_changes(json), confidence, provenance, reversal_of(self-fk), summary
```

State-transition (position lifecycle, slice-relevant subset):
```
[created:sealed] --open--> [opened] --consume*--> [opened, q↓] --consume→0--> [depleted]
      │                                                              │
      └-------------------- reverse(event) restores prior (value, state) exactly
[any] --archive--> [archived]   (archived ≠ deleted; depleted ≠ deleted)
```

---

## 5. Cross-cutting specifications

### 5.1 Quantity & conversion ([ADR-0002](adr/0002-quantity-value-object.md))
A `Quantity` value object (`services/quantity.py`), never a bare float:
- `kind ∈ {exact, estimated, approximate, presence, unknown}` — **unknown is not zero and
  not 1**; presence = "have it, amount unmeasured".
- `dimension ∈ {count, mass, volume, package, serving, level, presence}` derived from `unit`
  via a `UNIT_DIMENSIONS` registry; qualitative `level ∈ {full, half, low, empty}`.
- `value: Decimal | None`, optional `lower/upper` bounds, `confidence ∈ [0,1]`, `provenance`.
- Deterministic within-dimension conversion (g↔kg, ml↔l, …). **No volume↔mass without a
  product-specific density.** `add()` raises on incompatible dimensions; adding anything to
  `unknown` yields `unknown` (never silently drops it). Authoritative storage is
  **decimal** (SQLite: TEXT-encoded `Decimal` in the ledger; `Numeric` columns as columns
  are added), not binary float.

### 5.2 Matching & ambiguity ([ADR-0003](adr/0003-matching-service.md))
One `services/matching.py` replacing scattered `ilike`/substring. Inputs: normalized text,
aliases, barcode, concept, brand/variant, package size, location, unit-compat. Returns
**ranked candidates with confidence + reasons**. Reads may aggregate plausible matches and
*explain* ambiguity; **mutations never guess** when materially-different candidates exist
(different allergen/dietary/item_type/concept) — they return structured candidates for
confirmation, or apply a **visible, reversible household policy** (e.g. "use the
earliest-expiring *open dairy* milk"). Substring never crosses an allergen/item_type edge.

### 5.3 Expiry & uncertainty
Preserve raw date *facts* (printed best-by / use-by / sell-by / packed-on / user-entered /
freeze / thaw / cooked) rather than overwriting one column; compute an **effective "use
soon" forecast** = likely date **or range** + confidence. Open/freeze/thaw/cook/observed-
quality recompute the forecast while keeping prior facts. Explain in plain language
("Likely good 3–5 days after opening"; "Low confidence — purchase date unknown"). *Slice
lays the seam (events carry the state change + provenance); the multi-fact expiry table is
Phase 5.*

### 5.4 Provenance & confidence
Every event and agent-sourced field carries `provenance` + `confidence`. Low-confidence
agent/vision input enters a **review queue / visible mark** and **cannot silently overwrite
confirmed values** ([ADR-0004](adr/0004-provenance-confidence.md), Phase 6).

### 5.5 Inventory-event command matrix (shared services)
`services/inventory/commands.py` — REST, MCP, assistant, HA all call these; each is
**transactional, idempotency-keyed, returns `{event_id, summary, undo}`**, emits an
immutable `InventoryEvent`, and refuses to guess on ambiguity:

`add · import · reconcile · adjust · consume · waste · expire · donate · return · open ·
reseal · move · split · merge · freeze · thaw · prepare · transform · decant · archive ·
reverse`. Reversal = a new compensating event referencing `reversal_of`, never a rewrite.
**Slice implements: `add, open, consume, reverse` (+ `import` for the migration).**

---

## 6. Scenario matrix → expected behavior (all 30; slice-covered marked ✅)

| # | Scenario | Commands / events → user-visible result | Key invariant |
|---|---|---|---|
| 1 ✅ | 2 milk cartons, different days | 2 `add`→2 positions, 1 lot each | qty conserved |
| 2 ✅ | Open one | `open` → position.package_state=opened; "1 open, 1 sealed" | facet orthogonal |
| 3 ✅ | Use half the open one | `consume(qty)` on the open position | never negative |
| 4 ✅ | Cartons in one view, ml in a recipe | count dimension; recipe asks volume → **no silent convert**, report "tracked by carton" | no cross-dim sum |
| 5 | Dozen + 4 loose eggs | package(12)+count(4); summary "16 total" | dimension-safe rollup |
| 6 | 24-can case, 9 left | package w/ inner_count; consume decrements inner | conserve |
| 7 | Half jar, no weight | `level=half`, kind=estimated | estimated≠exact |
| 8 | Spice "low" | `level=low`, tracking=level | unknown/level distinct |
| 9 | Cilantro, unknown amount | kind=presence | unknown≠0, ≠1 |
| 10 | 5 lb chicken → freezer portions | `split`+`freeze` → N portion positions | split conserves |
| 11 | One portion thawed, moved | `thaw`+`move` | move conserves, cross-household blocked |
| 12 | Cook → 4 meal-prep servings | `transform` (consume src, produce dst, lineage) | yield uncertainty recorded |
| 13 | 2 eaten, 1 spoiled | `consume`+`waste` | never negative |
| 14 | Homemade stock → 3 jars | produce lot, `split` ×3 | conserve |
| 15 | Leftover soup, no ingredients | `add` leftover (lineage optional) | lineage not mandatory |
| 16 | Flour from 2 buys in one bin | `merge` (2 lots → 1 position, lineage kept) | merge conserves |
| 17 | Correct est 2 kg → measured 1.6 kg | `reconcile`/`adjust`, kind exact | exact supersedes est, event logged |
| 18 | Wine, different vintages | one concept, per-position vintage attr | not collapsed |
| 19 | Similar names, different allergens | distinct products/concepts | no auto-merge across allergen |
| 20 | Scallions = green onion | alias/substitution match | no substring cross-food |
| 21 | Receipt: confident product, weak qty | product high-conf, qty kind=estimated low-conf | mixed confidence per field |
| 22 | CV detects a barcode-scanned item | matching → dedup review, not double-add | no silent double |
| 23 ✅ | Same agent mutation retried (idem key) | second call → same event, no double | idempotent |
| 24 ✅ | Reverse a consumption | `reverse(event)` → new compensating event, restore exact | immutable history |
| 25 | Concurrent multi-user edits | per-position row guard / event serialize | projection = events |
| 26 | Recipe needs g, stock by count, no conversion | report "unknown in g" | no false certainty |
| 27 | Non-food consumable in stock | item_type=consumable → excluded from recipe/expiry | type boundary |
| 28 | Expired but still present | expired flag ≠ absent; stays in stock | expired≠gone |
| 29 | Reserve ingredients for a meal | reservation holds qty | reserved not double-allocated |
| 30 | Reconcile a location, items missing | `reconcile` one reversible op | preview + reversible |

---

## 7. Phased implementation plan (each phase ships deployable & compatible)

1. **Domain foundation** — `Quantity` VO + unit registry; `InventoryEvent` ledger; shared
   `services/inventory/` command layer; provenance/confidence + orthogonal `package_state`
   columns; migration scaffolding + opening-balance events. **← the vertical slice lives here.**
2. **Core daily ops** — add/consume/waste/adjust/move/open/split/merge + reverse, on the
   command layer; safe selection (FEFO + prefer-open) via `services/selection.py`.
3. **Human inventory** — grouped dimension-safe summaries; tracking modes; progressive
   disclosure; quick actions; location reconciliation; HA flows.
4. **Kitchen transformations** — AcquisitionLot/position split; freeze/thaw/prepare/
   transform/decant; portions; lineage.
5. **Planning & intelligence** — FoodConcept canonicalization; conversion/substitution;
   reservation/allocation; multi-fact expiry; reorder policies; uncertainty-aware suggestions.
6. **External intelligence** — receipt/vision confidence; scales; dedup/merge review queue;
   agent preview/confirm policies.

---

## 8. First vertical slice — "Milk" (the only thing built in PR-1)

Proves the spine: **ledger + shared command layer + dimension-safe Quantity + orthogonal
package_state + reversal + provenance + migration-with-event + REST/assistant convergence.**
Story: *add two cartons of milk, open one, consume part, safely aggregate the remainder,
expose via REST + assistant, reverse the consumption, preserve full event history.*

**Backend**
- `InventoryEvent` model (append-only; `delta_value` TEXT-decimal; `idempotency_key` unique
  per group; `reversal_of` self-FK; `state_changes` JSON; `summary`, `provenance`, `confidence`).
- `StockLot` **additive** columns: `package_state` (sealed/opened/…), `quantity_kind`
  (exact/estimated/presence/unknown), `confidence`, `provenance`. No type change, no drops.
- `services/quantity.py` — `Quantity` VO + `UNIT_DIMENSIONS`; dimension-safe `add`; unknown-safe.
- `services/inventory/commands.py` — `add_inventory / open_inventory / consume_inventory /
  reverse_inventory_event` (+ `_emit_event`, idempotency, transactional, returns summary+id).
- REST: route add/consume through the command layer; new `POST /stock/<id>/open` and
  `POST /inventory/events/<id>/reverse`; **keep** `consume`/`unconsume` as back-compat
  adapters (unconsume → reverse). Serializer gains `packageState`, `quantityKind`; grouped
  summary becomes package-aware and dimension-safe ("2 cartons: 1 open, 1 sealed").
- **Assistant**: `h_add_stock`/`h_record_consumption`/open call the **same command layer**
  (removes that duplication; REST/MCP/assistant now agree for these ops).
- **Migration** (`_ensure_columns` additive cols + `create_all` new table): backfill each
  active `StockLot` → derive `package_state` from `storage_method`/`opened_date`, set
  `quantity_kind='exact'`, and **emit one `import` opening-balance `InventoryEvent`**
  (idempotent — guarded by "no import event for this position"). DB backed up first.

**Frontend** (`Stock.vue`): "Open" quick action; show open/sealed + dimension-safe rollup;
"Undo" on the last consume (→ reverse event). Four states preserved.

**Tests** (behavior, not status): milk end-to-end (2 sealed → open 1 → consume part →
aggregate "1 open partial + 1 sealed", dimension-safe → reverse restores exact → history
intact); Quantity VO (cross-dimension refused, unknown stays unknown, decimal exact);
idempotent retry no-op; event household-isolation; migration on a legacy fixture emits
exactly one opening event per lot (idempotent on re-run). Plus MCP/assistant↔REST parity
for consume.

**Docs**: this file + ADRs + `architecture.md` update. **Back-compat**: every existing
serializer key, input alias (`family|group`, `freshness|state`, `productName|name`,
legacy `reason`), and the `unconsume`/`consumptionId` contract keep working.

**Explicitly deferred to Phases 2–6** (designed above, not built in PR-1): FoodConcept
table, AcquisitionLot split, preservation/prep/quality facets, matching-service overhaul,
selection policies, replenishment/reservation, receipt/vision confidence queue, full
Decimal migration of `StockLot.quantity`, multi-fact expiry table.

---

## 9. Definition of done (slice)
Ordinary add/open/consume has ≤ today's friction; unknown/estimated/presence are
representable and never coerced to 0/1; no invalid cross-dimension totals; every slice
mutation is one explainable, reversible event; REST + MCP + assistant agree for add/
consume/open; legacy data migrates with an opening event and zero loss; `make check` green.
