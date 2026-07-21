# Architecture

Single-container app (mirrors HomeHoard's shape). No external datastores/brokers.

## Data model
```
Product (the "what": name, category, barcode, unit, shelf-life override)
   └──▶ StockLot (a real quantity in a Location, with storage method + expiry)
              attrs JSON: wine {vintage,varietal,region,abv} · meat {cut,animal,weightG,freezeDate}
Location (nested, multi-site: site→room→fridge/freezer/pantry/wine_cellar)
ShelfLifeProfile (category × storage_method → typical_days; seeded defaults)
ShoppingItem · ConsumptionEvent (runout) · PlannedItem (demand from myMeal)
InventoryEvent (append-only ledger: add/open/consume/reverse + migration import)
Group/User/ApiToken (household tenancy + machine-client auth)
```

> **Stock redesign (in progress).** The kitchen stock model is evolving toward an
> event-logged design — dimension-safe quantities, orthogonal package/preservation
> state, and a shared command layer so REST/MCP/assistant can't diverge. Phase 1
> (foundation + the "Milk" vertical slice) has shipped: `services/quantity.py`
> (the `Quantity` value object), `services/inventory/` (the command layer:
> add/open/consume/reverse), and the `InventoryEvent` ledger. Full design, scored
> model choice, scenario matrix, phased plan, and ADRs live in
> [`stock-redesign/`](stock-redesign/DESIGN.md).

## Services
- `estimation.py` — expiry from (category, storage); runout from consumption rate.
- `shopping.py` — paste-for-delivery formatting.
- `planning.py` — demand ↔ inventory reconciliation (have/need/shortfall).
- `integrations.py` — bounded, graceful outbound clients for myMeal/HomeHoard.

## Processes
- gunicorn (2 workers, non-root) — REST API + Vue SPA.
- Edibl MCP server (uvicorn, SSE :7767) — AI tooling.
- entrypoint one-shot — schema init before workers (no create_all race).

## Failure domains
- SQLite file (single SPOF) — WAL + busy_timeout + FK; back up + restore-verify
  as in HomeHoard (`scripts/` to be ported).
- `/api/v1/ready` reports DB + storage; Docker HEALTHCHECK gates on it.
