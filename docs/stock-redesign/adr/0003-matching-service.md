# ADR-0003: One matching service; mutations never guess across material boundaries

**Status:** proposed · **Date:** 2026-07-21 · **Implemented:** Phase 5 (designed now)

## Context
Name resolution is scattered and unsafe: bidirectional substring (`services/planning.py:42`),
`ilike`/`in` in the assistant (`services/assistant.py:67,228,298`) and MCP (`edibl_mcp.py:165`),
three copies of `_resolve_product`. "milk" can match almond milk; "scallions" won't match
"green onion"; nothing guards allergen/dietary/item_type boundaries.

## Decision
A single `services/matching.py` returning **ranked candidates with confidence + reasons**,
keyed on normalized text, aliases, barcode, `FoodConcept`, brand/variant, package size,
location, and unit-compatibility, learning from user corrections.
- **Reads** may aggregate plausible matches and *explain* the ambiguity.
- **Mutations** must **not guess** when candidates differ materially (allergen, dietary,
  `item_type`, concept): return structured candidates for confirmation, or apply an explicit,
  **visible and reversible** household policy. Substring never crosses an allergen/item_type edge.

## Consequences
- **+** "Use the milk" can safely pick the earliest-expiring *open dairy* milk under a visible
  policy, but never silently consume almond milk.
- **+** Kills three divergent resolvers.
- **−** Some agent flows gain a confirmation step — intended (agent safety > convenience).
