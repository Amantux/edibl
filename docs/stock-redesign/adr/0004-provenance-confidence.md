# ADR-0004: Provenance + confidence on every event; low-confidence cannot silently overwrite

**Status:** proposed · **Date:** 2026-07-21 · **Implemented:** slice (fields) + Phase 6 (queue)

## Context
Stock originates from manual entry, barcode, receipt text/image, delivery, HA, MCP, chat,
computer vision, scales, and migration. Today the only provenance is a free-text `source`
string and `expiry_estimated` bool; agent writes can overwrite user-confirmed values with no
confidence signal, and a retried agent call duplicates data (no idempotency).

## Decision
- Every `InventoryEvent` (and agent-sourced field, where practical) carries `provenance` and
  `confidence ∈ [0,1]`. **The slice adds these fields** to the ledger and positions.
- Mutations take an `idempotency_key` (unique per group) — retries return the same event.
- **Phase 6:** low-confidence agent/vision facts enter a **review queue / visible mark** and
  **cannot silently overwrite** a higher-confidence user-confirmed value; CV that re-detects a
  barcode-scanned item goes to dedup review, not a second add.

## Consequences
- **+** Truthful, auditable origins; safe retries; confirmed data protected.
- **−** A review surface must exist before auto-ingest is trusted (Phase 6) — until then,
  low-confidence input is marked, not applied blind.
