# ADR-0002: Quantity is a dimension-aware value object over decimals

**Status:** proposed · **Date:** 2026-07-21

## Context
Today `quantity` is a `Float` defaulting to `1` (`models/__init__.py:171`), with a free-text
`unit`. This conflates *unknown*, *present*, *approximate*, and *exact*; sums quantities
across incompatible units (`api/stock.py:143`; `services/planning.py:41-45`); and uses binary
float for amounts (and money — `cost: Float`). The brief forbids all three.

## Decision
Introduce a `Quantity` value object (`services/quantity.py`), never a bare float:
- `kind ∈ {exact, estimated, approximate, presence, unknown}`; **unknown ≠ 0 and ≠ 1**.
- `dimension ∈ {count, mass, volume, package, serving, level, presence}`, derived from `unit`
  via a `UNIT_DIMENSIONS` registry; qualitative `level ∈ {full, half, low, empty}`.
- `value: Decimal | None`, optional `lower`/`upper`, `confidence ∈ [0,1]`, `provenance`.
- Deterministic within-dimension conversion (g↔kg, ml↔l). **No volume↔mass** without a
  product-specific density. `add()` **raises** on incompatible dimensions; adding to
  `unknown` returns `unknown`.
- Authoritative storage is **decimal**: ledger deltas as TEXT-encoded `Decimal`; `Numeric`
  columns as position columns are added. The legacy `StockLot.quantity` Float is mirrored
  during transition and migrated to `Numeric` in Phase 1-tail (not the slice).

## Consequences
- **+** Truthful uncertainty; no invalid aggregates; recipe layer can say "unknown in grams".
- **−** Every aggregation path must go through the VO — enforced by the shared command/serializer layer.
- **−** SQLite has no native Decimal; TEXT-decimal in the ledger avoids float round-trip.
