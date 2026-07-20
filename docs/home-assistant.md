# Edibl in Home Assistant

Edibl plugs into Home Assistant three ways, which you can mix:

1. **Add-on** — run Edibl itself inside HA, in the sidebar via Ingress.
2. **HACS integration** — surface Edibl as sensors + an `add_to_shopping_list`
   service, for dashboards and automations.
3. **Assistant** — talk to your pantry with **Ollama** or **OpenAI**, either
   through Edibl's own chat box or through HA Assist (voice) via MCP.

```
        ┌────────────────── Home Assistant ──────────────────┐
        │  Sidebar (Ingress) ──► Edibl add-on (the app)       │
        │  HACS integration  ──► sensors + service ──► REST   │
        │  Assist (Ollama/OpenAI) ──► MCP Client ──► Edibl MCP │
        └─────────────────────────────────────────────────────┘
```

## 1. Add-on (run Edibl in HA)

**Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add
`https://github.com/Amantux/edibl`, then install **Edibl** and start it. It
appears in the sidebar. Data persists in the add-on's `/data`. Full option
reference: [`addon/DOCS.md`](../addon/DOCS.md).

## 2. HACS integration (sensors + service)

The integration reads Edibl's REST API and exposes:

- Sensors: **Items in stock**, **Expiring soon** (with an `items` attribute),
  **Expired**, **Products**, **Locations**, **Wasted products (learned)** (with a
  `suggestions` attribute).
- Service: **`edibl.add_to_shopping_list`** (`name`, `quantity`, `unit`).

**Install:** HACS → ⋮ → **Custom repositories** → add
`https://github.com/Amantux/edibl` as an **Integration** → install **Edibl** →
restart HA → **Settings → Devices & Services → Add Integration → Edibl**.

**Connect:** enter the Edibl URL. If you run the add-on, map its port `7746` and
use `http://homeassistant.local:7746`. The API token is optional in single-tenant
mode; if Edibl requires auth, create a long-lived token in Edibl
(`POST /api/v1/tokens`, `edbl_…`) and paste it.

Example automation — nudge when things are going off:

```yaml
automation:
  - alias: Edibl expiring nudge
    trigger:
      - trigger: numeric_state
        entity_id: sensor.edibl_expiring_soon
        above: 0
    action:
      - action: notify.mobile_app
        data:
          message: >-
            {{ state_attr('sensor.edibl_expiring_soon','items')
               | map(attribute='name') | join(', ') }} expiring soon.
```

## 3. Assistant: Ollama or OpenAI

### A. Edibl's own chat box (simplest)

The chat widget (bottom-right in Edibl) uses a provider-neutral backend. In the
add-on options (or env vars if standalone):

```
# Local Ollama (install the HA "Ollama" add-on, pull a model):
llm_provider: ollama
llm_base_url: http://homeassistant.local:11434
llm_model:    llama3.1

# OpenAI (or any OpenAI-compatible endpoint):
llm_provider: openai
llm_api_key:  sk-...
llm_model:    gpt-4o-mini
```

It uses the same inventory tools as the MCP server, so it can look things up and
act (add stock, record eaten/tossed, edit the shopping list). No provider set →
a built-in rules assistant still handles the common questions.

### B. HA Assist / voice (through MCP)

Let Home Assistant's own assistant reach Edibl's tools:

1. In the add-on: `mcp_enabled: true`, set a `mcp_server_token`, and map port
   `7767`.
2. Add HA's **Model Context Protocol** integration (MCP Client) pointing at
   `http://<addon-host>:7767/sse` with that token.
3. Set your Assist pipeline's conversation agent to **Ollama** or **OpenAI
   Conversation**. Now "what's expiring?" and "add milk to the shopping list"
   work by voice.

Edibl is the source of truth for what's actually on hand; myMeal owns recipes.
Point the same MCP server at myMeal's agent too — see
[`integration.md`](integration.md).
