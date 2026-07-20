<p align="center">
  <img src="docs/logo.png" alt="Edibl" width="360">
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FAmantux%2Fedibl">
    <img src="https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg" alt="Add the Edibl add-on repository to your Home Assistant.">
  </a>
</p>

# Edibl 🥑

**Edibl is your kitchen's real inventory** — what food you actually have, where it
is, how fresh it is, and what you need to buy. It's a sibling to
[HomeHoard](https://github.com/Amantux/homehoard) (home inventory) and a partner
to **myMeal** (recipes & meal planning):

> **myMeal owns the recipes. Edibl owns the lay of the land. You talk to both to
> plan your food.**

Built on the same hardened stack as HomeHoard: **Flask + SQLite + Vue 3 + Docker**,
with a dedicated **MCP server** so an LLM (in myMeal, Home Assistant, or a chat
client) can query and act on your inventory.

## What it does

- **Track real stock** across multiple sites (home, the lake house), nested
  locations (fridge / freezer / pantry / **wine cellar**), with quantities,
  storage method, cost, and freshness.
- **Grouped, but tracked separately** — organic milk for drinking and filtered
  milk for ice cream are distinct products (own shelf-lives, own buy-dates) that
  roll up under one **Group** ("Milk"); every lot keeps its own expiry.
- **Everything's user-driven** — categories, units, freshness, and groups are
  free-form with autocomplete from what you already use; nothing is a fixed list.
- **Full agent CRUD** — the chat assistant and MCP tools can add, look up,
  **update, and remove** stock, not just query it.
- **Auto-estimate expiry** from the food's category × storage method — leave the
  date blank and Edibl fills it in. Vacuum-sealed + frozen meat lasts *years*;
  fresh dairy days.
- **Learns your food's real shelf life** — mark how things left the kitchen
  (eaten / spoiled / expired / tossed) and their ripeness (unripe/ripe/overripe);
  Edibl personalizes future expiry estimates from *your* losses and suggests what
  to buy less of ("your bananas usually last ~5 days"). Waste feed on the dashboard.
- **"Use it or lose it" dashboard** — what's expiring soon, everywhere.
- **Chat assistant on every screen** — ask "what's expiring?", "do I have eggs?",
  "what am I wasting?", or just tell it what you bought/ate. Provider-neutral and
  built for Home Assistant: point it at a local **Ollama**, any **OpenAI-compatible**
  endpoint, or **Anthropic**. It can look things up *and* act — add, update, and
  remove stock and shopping-list items by chat. (Requires a configured provider.)
- **Flexible bulk add** — log a whole grocery haul, a farm box, or a butchered
  animal in one action, with shared defaults + per-row overrides. **Paste a
  receipt or order** and an LLM extracts the items for you to review and add.
- **Barcode intake** — scan (native browser `BarcodeDetector`) or type a code;
  known products auto-fill, unknown ones can enrich from Open Food Facts.
- **Wine & alcohol** specialty view (vintage / varietal / region).
- **Shopping list → one-click "Copy for delivery"** — a paste-ready list for Uber
  Eats / Instacart, plus auto-suggestions for what you've run out of.
- **Meal-plan reconciliation** — ingest planned recipes from myMeal, see exactly
  what you have vs. need, and **order just the shortfall**.
- **Runout prediction** — learns your consumption rate and forecasts when you'll
  run out.
- **MCP AI tooling** — tools so you can *talk* to your pantry: "do I have
  butter? / what's expiring? / can I make this recipe? / order what I'm short on."
  Point Home Assistant's MCP Client (or myMeal's agent) at it.

## Assistant setup (required for chat)

The chat assistant needs an LLM provider — set one and it can query **and** act
(add / update / remove stock, edit the shopping list). Without a provider the
chat shows setup guidance instead. Pick one:

```bash
# Local Ollama (recommended for Home Assistant / privacy):
EDIBL_LLM_PROVIDER=ollama  EDIBL_LLM_BASE_URL=http://localhost:11434  EDIBL_LLM_MODEL=llama3.1
# Any OpenAI-compatible endpoint:
EDIBL_LLM_PROVIDER=openai  EDIBL_LLM_API_KEY=sk-...  EDIBL_LLM_MODEL=gpt-4o-mini
# Anthropic:
EDIBL_LLM_PROVIDER=anthropic  EDIBL_LLM_API_KEY=sk-ant-...  EDIBL_LLM_MODEL=claude-opus-4-8
# As a Home Assistant add-on, reuse HA's own conversation agent (completion-only):
EDIBL_LLM_PROVIDER=homeassistant
```

`ollama` / `openai` / `anthropic` support full chat CRUD (the same tools as the
MCP server: add / update / remove stock, edit the shopping list) and receipt
extraction. `homeassistant` reuses HA's configured chat agent for extraction and
simple Q&A (completion-only). Optional barcode enrichment: `EDIBL_BARCODE_LOOKUP=1`.

## Quick start

```bash
# Dev (auth off, in-memory-safe):
cd backend && EDIBL_DISABLE_AUTH=1 EDIBL_DATA_DIR=./data python run.py   # :7746
cd frontend && npm install && npm run dev                                # :5180

# Production (Docker):
export EDIBL_SECRET_KEY="$(openssl rand -base64 48)"
docker compose up -d --build
# then open http://localhost:7746 and wait for readiness:
until curl -fsS http://localhost:7746/api/v1/ready; do sleep 2; done
```

## Home Assistant

[![Add the Edibl add-on repository to your Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FAmantux%2Fedibl)

Click the button above to add the repository, then install **Edibl** — it pulls a
**prebuilt image** (no build on your device; aarch64 / amd64) and runs in the
sidebar via Ingress.

Edibl also ships a **HACS integration**: freshness/expiry **sensors**, an
`add_to_shopping_list` service, and your shopping list as a native **To-do List**.
Wire the chat assistant to a local **Ollama** / **OpenAI** endpoint (or reuse HA's
own conversation agent), or talk to your pantry by voice via HA Assist + MCP.
Full guide: [`docs/home-assistant.md`](docs/home-assistant.md).

## Docs

- [`docs/home-assistant.md`](docs/home-assistant.md) — add-on, HACS integration, Ollama/OpenAI, Assist/voice.
- [`docs/architecture.md`](docs/architecture.md) — data model, services, failure domains.
- [`docs/integration.md`](docs/integration.md) — **myMeal ↔ Edibl ↔ HomeHoard** wiring + the MCP tools.
- [`docs/roadmap.md`](docs/roadmap.md) — computer vision & weight estimation (planned) and more.

## Stack & posture

Flask API (gunicorn, non-root, fail-closed on weak secrets, WAL SQLite,
readiness vs liveness, security headers, rate limiting) · Vue 3 SPA · a separate
MCP (SSE) server · Docker + CI (lint / tests / readiness-gated smoke). Same
production-operability patterns proven on HomeHoard.
