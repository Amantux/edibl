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
- **Auto-estimate expiry** from the food's category × storage method — leave the
  date blank and Edibl fills it in. Vacuum-sealed + frozen meat lasts *years*;
  fresh dairy days.
- **"Use it or lose it" dashboard** — what's expiring soon, everywhere.
- **Butchering workflow** — one animal → many **vacuum-sealed frozen cuts** in one
  action, each with a long estimated shelf life and a shared session tag.
- **Wine & alcohol** specialty view (vintage / varietal / region).
- **Shopping list → one-click "Copy for delivery"** — a paste-ready list for Uber
  Eats / Instacart, plus auto-suggestions for what you've run out of.
- **Meal-plan reconciliation** — ingest planned recipes from myMeal, see exactly
  what you have vs. need, and **order just the shortfall**.
- **Runout prediction** — learns your consumption rate and forecasts when you'll
  run out.
- **MCP AI tooling** — 14 tools so you can *talk* to your pantry: "do I have
  butter? / what's expiring? / can I make this recipe? / order what I'm short on."

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

## Docs

- [`docs/architecture.md`](docs/architecture.md) — data model, services, failure domains.
- [`docs/integration.md`](docs/integration.md) — **myMeal ↔ Edibl ↔ HomeHoard** wiring + the MCP tools.
- [`docs/roadmap.md`](docs/roadmap.md) — computer vision & weight estimation (planned) and more.

## Stack & posture

Flask API (gunicorn, non-root, fail-closed on weak secrets, WAL SQLite,
readiness vs liveness, security headers, rate limiting) · Vue 3 SPA · a separate
MCP (SSE) server · Docker + CI (lint / tests / readiness-gated smoke). Same
production-operability patterns proven on HomeHoard.
