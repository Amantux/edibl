# ADR-0001: Event-logged (CQRS-lite), not event-sourced

**Status:** proposed · **Date:** 2026-07-21

## Context
The redesign requires every meaningful stock change to be an immutable `InventoryEvent`,
"the current state must be explainable from events", and reversal to create a new event.
Two ways to relate state to events: (A) rebuild current state from the event log on read
(event-sourcing); (B) keep the current-state projection authoritative and mutable, writing
an append-only ledger alongside it in the same transaction (event-logged / CQRS-lite).

## Decision
**B.** `StockPosition` (evolved `StockLot`) stays the authoritative mutable current state.
Every command writes the state change **and** appends an `InventoryEvent` atomically. The
ledger is the audit trail, the reversal source, and the explanation; it is not replayed on
every read. A reconcile/verify job can prove `projection == fold(events)`.

## Consequences
- **+** SQLite-friendly (no per-read replay/joins); simpler queries; small migration.
- **+** Satisfies "explainable from events" and "reversal = new event" without event-sourcing cost.
- **+** Additive, incremental: the ledger is added beside the existing tables.
- **−** Projection and ledger can drift on a bug → mitigated by a reconciliation test/job and
  by routing *all* mutations through the shared command layer (no direct ORM writes).
- **−** Not a full audit of read models / time-travel — acceptable for a household app.

## Rejected
Model A (full event-sourcing): ~1 pt more integrity for large SQLite-suitability, migration,
and maintainability costs — the "enterprise warehouse" over-build the brief warns against.
