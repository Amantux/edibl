# Changelog

All notable changes to the Edibl add-on. The patch version is **auto-bumped by
CI** on every release push, so Home Assistant always sees an update.

## 1.5.8

- **Smoother add-stock.** The Add form now leads with just the essentials — name,
  quantity, unit, location — with everything else tucked under **More options**.
  Typing a name you've used before **auto-fills its category, group, and unit**,
  and a new **Add & another** button keeps the form open (holding your location /
  storage / category) so a grocery haul goes in fast. Name field is focused and
  Enter submits.

## 1.5.7

- **Troubleshoot discovery** — the Settings → myMeal card has a **🔧 Diagnose**
  button (and a read-only `GET /integrations/mymeal/discover/debug` endpoint) that
  shows exactly which hosts "Find myMeal" tried and what each returned. No secrets.

## 1.5.6

- **Fix "Find myMeal".** Discovery relied solely on the Supervisor's per-add-on
  info API, which the default add-on role can't read for a *sibling* add-on — so
  it found nothing. It now also probes the internal add-on hostnames
  (`mymeal` / `local-mymeal:7850`) and returns only instances that actually
  answer, so it works without extra permissions or port mapping.

## 1.5.5

- **Reverse a consumption over the API** — new `POST /stock/<lot>/unconsume`, and
  the consume response now returns `consumptionId`/`consumedAmount`. This lets a
  connected app (e.g. myMeal's chat) offer one-tap **undo** of recording that
  food was eaten/spoiled, restoring the quantity and removing the event.

## 1.5.4

- **One assistant for the whole kitchen.** When a myMeal instance is connected,
  Edibl's chat can now also **manage myMeal** — look up recipes, see and add to
  the meal plan, create recipes, and add to myMeal's shopping list (mutations are
  undoable). It's fully optional and auto-detected: with no myMeal connected these
  tools don't appear and standalone Edibl is unchanged. Paired with myMeal's chat
  gaining Edibl pantry tools, either app's chat manages both — and registering
  both MCP servers (plus HomeHoard) with Home Assistant's MCP Client lets one HA
  Assist pipeline drive everything by chat or voice. See
  [docs/home-assistant.md](../docs/home-assistant.md).

## 1.5.3

- **Zero-URL integration setup, matched to HomeHoard** — the add-on now registers
  its auto-discovery whenever it runs under the Supervisor (previously only in
  auth-disabled mode), falls back to the container hostname if the Supervisor
  doesn't report one, and the config flow **probes the add-on before offering the
  discovered card** so it never creates a dead entry. Add the "Edibl" card and
  you're done — no URL, no token, no port mapping.

## 1.5.2

- **Connect to a myMeal add-on in one click** — the Settings → myMeal card can
  now **find myMeal running as a Home Assistant add-on** (via the Supervisor) and
  fill in its internal address, so Edibl reaches it on the add-on network without
  you knowing the hostname or mapping a port.

## 1.5.1

- **Auto-discovery** — when the add-on runs it registers with the Supervisor, so
  the Edibl integration is offered automatically in Home Assistant (no URL/token,
  no port mapping — HA reaches the add-on internally). Config flow gains a hassio
  discovery step.
- **Connect to myMeal from the UI** — the Settings page has a myMeal card (URL +
  token, Test connection, Pull plan now), persisted per-household and overriding
  the env default.

## 1.5.0

Milestone release rolling up everything since 1.0.0: product grouping, user-driven
categories/units/freshness, full agent CRUD with surfaced + undoable chat actions,
receipt/order extraction (text **and** photo), a Home Assistant To-do shopping
list, notification blueprint, data export/import, mobile-friendly UI with an
in-app Settings page, and configurable providers (incl. Ollama key + model
polling, and targeting your HA conversation agent).

## 1.4.5

- **Better Ollama support** — send Ollama's API key (for secured / cloud Ollama)
  on every call; the Settings page can **list and pick models** polled straight
  from your Ollama server (and from OpenAI / Anthropic).
- **Use your Home Assistant conversation agent** — the `homeassistant` provider
  now takes an optional agent id (e.g. `conversation.ollama`) so it targets your
  Ollama conversation agent instead of the default.

## 1.4.4

- **Configure the chat provider from the UI** — the Settings page now has an
  editable provider form (Ollama / OpenAI / Anthropic / Home Assistant, base URL,
  model, API key). It's persisted per-household and **overrides** the add-on
  default, so you can set it up in Home Assistant *or* in Edibl and it's
  remembered either way. The API key is stored locally and never shown back.

## 1.4.3

- **Mobile-friendly** — the sidebar now collapses into a top-bar menu on phones
  (previously navigation was hidden on small screens), wide tables scroll, and
  modals fit. A new **Settings** page shows the assistant status and the
  export/import tools.
- **Home Assistant messaging** — an importable **notification blueprint** sends
  expiry alerts through any HA notify service (companion app, Telegram, …).

## 1.4.2

- **Surfaced, undoable chat actions** — the chat now shows each tool call it made
  (added / updated / removed stock, shopping edits, consumption) as a distinct
  row, and mutating ones have an **Undo** button that reverses just that action.
- CI now auto-bumps the add-on version on release pushes.

## 1.4.1

- Add-on **icon** and **logo**, an "Add to my Home Assistant" repository button,
  a web favicon, and this changelog.

## 1.4.0

- **Shopping list as a Home Assistant To-do List** entity (HACS integration) —
  add/complete/delete, two-way synced.
- **Receipt photo → items**: snap a photo of a receipt in Bulk add and a
  vision-capable model (gpt-4o / Claude / `llava`) extracts the items.
- **Data export/import** — full JSON snapshot + stock CSV, additive import (a new
  **Data** page).
- Advisory **security-scan CI** (pip-audit, npm audit, gitleaks, Trivy).

## 1.3.0

- **Paste a receipt / order → auto-extract** items with the LLM, review, then add.
- New **`homeassistant`** LLM provider — reuse Home Assistant's own conversation
  agent (completion-only; powers extraction and a relay chat).

## 1.2.0

- The chat assistant now **requires an LLM provider** (ollama / openai /
  anthropic); the limited built-in fallback was removed. Without a provider the
  chat shows setup guidance.

## 1.1.0

- **User-driven** categories, units, and freshness (free-form + autocomplete).
- **Product grouping** — distinct products (organic vs filtered milk) roll up
  under one group while keeping separate shelf-lives; grouped stock view.
- **Full agent CRUD** on stock via the chat assistant and MCP tools.
- Renamed ripeness → **freshness**; added an internal **source** field.

## 1.0.0

- Initial Home Assistant add-on: Ingress-served Edibl, prebuilt multi-arch images
  (aarch64 / amd64) via GitHub Actions, the MCP tool server, and a
  provider-neutral chat assistant (Ollama / OpenAI / Anthropic).
- Lifecycle learning (personalized shelf life from your losses), flexible bulk
  add, barcode intake, wine/freezer views, and the myMeal reconciliation bridge.
