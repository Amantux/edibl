# Integration: myMeal ↔ Edibl ↔ HomeHoard

The design principle: **each app owns what it's authoritative about, and they
query each other.** myMeal knows recipes & meal plans; Edibl knows the real food
inventory; HomeHoard knows the rest of the house. You (via an LLM/chat) talk to
all three.

```
   myMeal (recipes, meal plans)                 HomeHoard (home inventory)
        │  pushes planned ingredients                 ▲ cross-query non-food
        ▼                                             │
   ┌─────────────────────────── Edibl ───────────────┴──────────┐
   │  REST API (/api/v1)   +   MCP server (:7767/sse, 14 tools)  │
   │  what's on hand · expiring · shortfall · order · consume     │
   └─────────────────────────────────────────────────────────────┘
                         ▲ you talk to it (LLM / chat / HA Assist)
```

## 1. myMeal → Edibl (propagate planned ingredients)

myMeal pushes the ingredients its planned recipes need; Edibl reconciles them
against real stock.

```bash
# Push a recipe/plan's ingredients (idempotent by sourceRef):
curl -X POST http://edibl:7746/api/v1/integrations/mymeal/plan \
  -H "Authorization: Bearer <edibl-api-token>" -H "Content-Type: application/json" \
  -d '{"meal":"Pancakes","sourceRef":"recipe-42","items":[
        {"name":"Eggs","quantity":6},
        {"name":"Whole milk","quantity":1,"unit":"l"},
        {"name":"Flour","quantity":200,"unit":"g"}]}'

# See availability + shortfall:
curl http://edibl:7746/api/v1/plan            # {planned, items[], shortfall[], canMakeAll}
# Turn the shortfall into a shopping list:
curl -X POST http://edibl:7746/api/v1/plan/order
```

**Stateless recipe check** (no persistence) — "can I make this right now?":

```bash
curl -X POST http://edibl:7746/api/v1/plan/check \
  -d '{"ingredients":[{"name":"Rice","quantity":200,"unit":"g"},{"name":"Beans"}]}'
```

## 2. Edibl → myMeal / HomeHoard (outbound queries)

Configure the sibling base URLs + tokens; calls are bounded and degrade
gracefully (a missing/unreachable sibling returns `{configured/reachable:false}`
rather than erroring):

```
EDIBL_MYMEAL_URL, EDIBL_MYMEAL_TOKEN
EDIBL_HOMEHOARD_URL, EDIBL_HOMEHOARD_TOKEN
```

`GET /api/v1/integrations/status` shows what's wired. `POST
/api/v1/integrations/mymeal/pull` pulls the upcoming plan **from** myMeal (expects
myMeal to expose `GET /api/v1/plan/ingredients` → `{items:[{name,quantity,unit}]}`
— adjust the path in `services/integrations.py` to match myMeal's real API).

## 3. The Edibl MCP server (AI tooling)

A separate SSE server (`:7767/sse`, `EDIBL_MCP_SERVER_TOKEN` to require auth) with
14 tools an LLM uses to query & act. Point Home Assistant's **MCP Client**, or
myMeal's agent, at it.

**Query:** `do_i_have`, `whats_in_stock`, `expiring_soon`, `runout_forecast`,
`freezer_inventory`, `wine_cellar`.
**Bridge to recipes:** `check_recipe`, `plan_status`, `ingest_meal_plan`,
`order_shortfall`.
**Act:** `add_stock`, `record_consumption`, `add_to_shopping_list`, `shopping_list`.

Example conversation the tools enable:

> *"I'm making pancakes tonight."* → `check_recipe(...)` → *"You have milk but
> you're short 6 eggs and 200 g flour — want me to add them to the list?"* →
> `add_to_shopping_list(...)`. Later: *"I used a dozen eggs"* →
> `record_consumption("eggs", 12)` (feeds runout forecasting).

## Auth for machine clients

Generate a long-lived Edibl API token (`POST /api/v1/tokens`, shown once,
`edbl_…`) and give it to myMeal / the MCP server (`EDIBL_MCP_API_TOKEN`). Tokens
are SHA-256 hashed at rest and revocable.
