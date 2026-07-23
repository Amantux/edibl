# Changelog

All notable changes to the Edibl add-on. The patch version is **auto-bumped by
CI** on every release push, so Home Assistant always sees an update.

## 1.5.26

- **The condition scale now speaks the item's language.** Adding produce shows a
  ripeness scale (Unripe → Ripe → Overripe), bakery shows staleness (Fresh-baked →
  Stale), meat/seafood their own wording, and everything else the general
  Fresh → Going-off scale — chosen automatically from the item's category.

## 1.5.25

- **Adding stock is smarter and smoother.** As you type an item's name, Edibl now
  classifies it — auto-filling the category, unit, storage, group, and food type —
  so you rarely need "More options". It uses your configured AI model when there is
  one, and a built-in classifier otherwise (works with no setup).
- **Add from anywhere, instantly.** The Dashboard's "Add stock" button now opens the
  add form immediately instead of waiting for the page to load.
- **Reliability fix:** a fast first page-load (which now fetches several things at
  once) could hit a race that returned a server error on brand-new/standalone
  installs — fixed so the shared local user is created safely under concurrency.

## 1.5.24

- Maintenance release — republishes the add-on so Home Assistant picks up the
  latest build. (If HA still doesn't show an update, reload the add-on store:
  Settings → Add-ons → ⋮ → Check for updates / Reload, then hard-refresh.)

## 1.5.22

A visual polish pass — verified on phone, tablet, and desktop.

- **No more sideways scroll or clipped content.** Page headers now wrap their
  actions below the title instead of pushing the page wide, buttons never break
  across two lines, and wide tables scroll neatly inside their card.
- **Clearer "on hand".** The stock list now reads naturally — "2 cartons · 1 open",
  a plain "1" for simple items, and "some" for things you only track as present
  (no more confusing "0 count"). Expiry tags stay on one line.
- **Truer "running low".** The predicted-runout list no longer shows items whose
  amount is unknown as "runs out in ~0 days".
- Small fixes: "1 item" vs "N items", tag chips no longer wrap mid-word.

## 1.5.21

A frontend consistency + accessibility pass across the whole app.

- **Consistent, accessible feedback.** One global notification system replaces the
  old per-page toasts and pop-up alerts; messages are announced to screen readers,
  errors stay until dismissed, and Undo lives right in the toast.
- **Every dialog is keyboard-friendly.** Modals now trap focus, close on **Esc**,
  and return focus where you were — across add, bulk, use, reconcile, item actions,
  and location screens.
- **Nothing fails silently.** Pages that couldn't load used to show nothing; now
  they surface a clear message (with retry) and every action reports success or a
  specific, readable error.
- **Snappier loads.** The Dashboard and Stock pages fetch their data in parallel
  instead of one request after another.
- **Polish.** A visible keyboard focus ring everywhere, a "skip to content" link,
  a proper sign-in form, and a fixed Locations empty state.

## 1.5.20

- **Starts ready to use.** Every household now begins with a **Kitchen** containing a
  **Fridge** and **Freezer**, so you can add stock right away (existing installs get
  them too, once).
- **Condition on the way in.** The add form has a simple **1–5 condition scale**
  (Fresh · Good · Okay · Use soon · Going off) so you can note freshness as you stock.
- **Smarter add.** Item names now **autocomplete from your kitchen** as you type
  (server-side), and when you add something you already have, Edibl **defaults its
  location to wherever most of it already lives**.
- **Chat closes on mobile.** Added a proper ✕ on the chat panel — the bottom sheet no
  longer traps you.

## 1.5.19

- **A dashboard that's a launchpad, not just a readout.** The landing page now opens
  with the same tap-through cards as the Stock page — **Use it or lose it**, **Open
  packages**, **Restock**, **Reconcile a place** — each jumping straight to the right
  view, plus a one-line "here's what needs attention" summary in the header.
- **Restock now** on the dashboard lists what's below your reorder levels (reserved
  stock accounted for) with one-tap **Add to list**.
- At-a-glance tiles (items, locations) are now clickable, expiring items link through,
  and the whole page shares the look and feel of the rest of the app.

## 1.5.18

- **"Running low" in one tap.** Any stock item's ⋯ menu now has **Running low — add
  to list**, which drops it onto the grocery list linked to the product and tagged
  **low**. The shopping list shows that tag (and the note), so a quick delivery order
  can prioritise what you're actually short on.

## 1.5.17

A redesigned stock experience, an easier myMeal connection, and a nicer chat.

- **Stock, reimagined around answers.** The stock page now opens with the things
  you actually want to know — **Use it or lose it**, **Open packages**, **Running
  low**, and **Reconcile a place** — as tap-through cards, plus a single search box
  that also adds items or asks the assistant. The full grouped list is still there,
  one tap away.
- **Running low, handled.** Set a minimum on an item and it shows up under Running
  low with a suggested amount and a one-tap "Add to list" (it even accounts for what
  you've reserved for planned meals).
- **Reconcile a location** from the app: walk a place, fix the counts, mark what's
  missing, add what you found — committed as one undoable step.
- **Cleaner item actions.** Correct / Split / Move / Freeze / Thaw now live in a
  tidy action sheet instead of pop-up prompts.
- **See inside a place.** Tap any location to see exactly what's in it, add there, or
  reconcile it.
- **Easier myMeal connection.** Picking a discovered add-on, or pasting a connect
  link, now connects **and verifies** in one step — with clearer guidance when
  there's nothing set up yet.
- **A friendlier assistant.** The chat opens instantly, looks nicer, animates
  gently (and respects reduced-motion), and works as a proper bottom sheet on phones.

## 1.5.16

The kitchen-stock redesign, phases 2–5 — everyday actions, transformations, and
smarter planning, all on the one shared, fully-reversible engine.

- **Everyday actions, done right.** Correct an amount to what you actually measured,
  move a lot to another location, split a portion off, or merge two of the same
  thing together — each is a single tap, and each can be undone. "Use the milk" now
  draws from the open carton first, then the soonest-to-expire, and spills across
  cartons instead of stopping at one.
- **Stock takes.** A new reconciliation flow lets you walk a location, correct what's
  there, mark what's missing, and add what you found — committed (and undone) as one
  operation. More honest than expecting everyone to log every teaspoon.
- **Kitchens transform, not just consume.** Freeze and thaw (with the shelf-life
  estimate adjusting automatically), and turn stock into other stock — "made stock
  from the carcass", "cooked 2 lb chicken into 4 servings" — keeping the lineage.
  Items bought together now stay linked as one purchase even when split up.
- **Smarter, safer suggestions.** Edibl now understands that "scallions" and "green
  onion" are the same thing, keeps dairy and almond milk apart, and never offers a
  dishwasher tablet for a recipe. Set a minimum/target per item and it suggests what
  to reorder — counting what you've reserved for planned meals, and honest about
  anything it's unsure of.
- Every one of these works from the app, the chat assistant, and the MCP tools
  identically, and writes to the same audit ledger.

## 1.5.15

- **Open a package without using it up.** Stock now tracks whether a package is
  sealed or opened as its own thing (a carton can be frozen *and* open). Each item
  gets an **Open** button, and groups read naturally — "2 cartons: 1 open, 1 sealed".
- **Honest amounts.** You can log that you *have* something without pretending to
  know how much — cilantro shows "some", a spice shows its level — instead of a
  made-up "1". Unknown never silently becomes a number.
- **Undo that means it.** Recording what you used now offers a one-tap **Undo** that
  reverses that exact action from a full history, rather than guessing a quantity.
- **One brain for changes.** The app, the chat assistant, and the MCP tools now make
  inventory changes through one shared engine, so they always behave the same way.
- Under the hood: every stock change is written to an append-only ledger with who/
  when/why, quantities are unit-aware (no more adding litres to grams), and existing
  data migrates automatically with an opening-balance entry. First step of a larger
  kitchen-stock redesign — see docs/stock-redesign.

## 1.5.14

- **Who added it.** In a multi-user household (behind Home Assistant), each stock
  lot now shows who added it (👤 name). Only shown for real HA users, so a
  single-user or standalone install stays uncluttered.

## 1.5.13

- **Multi-user behind Home Assistant.** Each HA user now gets their own Edibl
  identity (auto-provisioned from the ingress sign-in), all sharing one
  household — so stock, plan, and shopping are shared but identity is per-person.
  The **first** user is the **owner** and can change household config (assistant
  provider, myMeal connection, API keys, data import); everyone else is a
  **member** with full inventory / plan / shopping / chat use. The Settings page
  is owner-only. Trust boundary enforced: the identity headers are honored only
  from the Home Assistant Supervisor proxy — a forged header from a directly
  published port is ignored and falls back to the shared local user. Standalone
  installs are unchanged (your account owns its household).

## 1.5.12

- **Access & keys + connect links.** Settings gains a token manager — generate a
  named API key (shown once, copy, revoke) — plus a one-click **connect link**
  that bundles Edibl's address + the key. Paste a myMeal connect link into the
  myMeal card to fill its URL *and* token in one step. This is for **standalone /
  cross-network** setups; behind Home Assistant Ingress siblings still connect
  with no key at all.

## 1.5.11

- **Config UX.** The Settings → Chat assistant card gains **↩ Reset to add-on
  default** (clears the values set in the app so it falls back to the add-on / env
  config), and a note that changes here are instant while add-on *Configuration*
  changes need an add-on restart. Standalone `docker-compose.yml` now loads your
  `.env` so every setting there reaches the container.

## 1.5.10

- **Config syncs both ways.** The Home Assistant conversation-agent id is now a
  real add-on option (`llm_agent_id`) and env var — previously it could only be
  set in the app's Settings page, so the `homeassistant` provider was
  half-configurable from the add-on. The Settings page now also *shows* an
  add-on/env-set agent id (not just a UI override), so the two surfaces agree.
- **Standalone made explicit.** Added a documented `.env.example` covering every
  `EDIBL_*` setting for standalone Docker, with the auth differences between
  running on your own vs behind Home Assistant Ingress called out.

## 1.5.9

- **"Find myMeal" now actually finds it.** Looking up a *sibling* add-on's real
  internal hostname requires the Supervisor **`manager`** role — the default role
  is denied, so discovery was blind (and myMeal's container isn't named
  `mymeal`/`local-mymeal`, so guessing failed too). The add-on now requests
  `hassio_role: manager`. **Updating will ask you to re-approve the add-on's
  permissions in Home Assistant.** The 🔧 Diagnose output also now reports the
  add-on's own hostname and distinguishes a role denial from a token/network
  problem.

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
