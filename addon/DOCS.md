# Edibl — Home Assistant add-on

Your kitchen's real food inventory, inside Home Assistant: what you have, where
it is, how fresh it is, what you're about to waste, and a chat assistant for your
pantry. Opens in the sidebar via **Ingress** (no separate login).

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add:
   `https://github.com/Amantux/edibl`
2. Install **Edibl**, then **Start**. Open it from the sidebar.

**Built for ARM.** The web UI is pre-built and shipped with the add-on, so there
is **no npm build on your device** — install only fetches the small Python
backend and installs wheels (no compiling). Works on **amd64** and **aarch64**.
On a Raspberry Pi, use the **64-bit** Home Assistant OS (aarch64); 32-bit (armv7)
isn't supported because its crypto libraries have no prebuilt wheels.

Data is stored in the add-on's persistent `/data` (survives updates).

## Configuration

| Option | Meaning |
|---|---|
| `disable_auth` | Keep **on** behind Ingress (HA authenticates you). Turn **off** for *hardened mode* — see [Access & keys](#access--keys-hardened-mode). |
| `llm_provider` | `ollama` / `openai` / `anthropic` / `homeassistant` — **required** for the chat assistant. |
| `llm_base_url` | e.g. `http://homeassistant.local:11434` for the HA **Ollama** add-on. |
| `llm_api_key` | Only for `openai` / `anthropic`. |
| `llm_model` | `llama3.1`, `gpt-4o-mini`, `claude-opus-4-8`, … |
| `llm_agent_id` | Only for `homeassistant`: which HA conversation agent to use (e.g. `conversation.ollama`). Blank = HA's default. |
| `barcode_lookup` | Identify an unknown scanned product barcode (1D UPC/EAN) online — Open Food Facts → product DB → Ollama web-search — to prefill a new item. 2D QR codes aren't treated as product barcodes. Blank/off = no online lookup. |
| `barcode_db_key` | **Optional.** API key for the product-barcode DB. The default lookup endpoint is keyless — set this only if you've pointed the lookup at a keyed provider. |
| `mcp_enabled` | Run the MCP tool server (for HA's MCP Client / other agents). |
| `mcp_server_token` | Optional legacy static bearer for the MCP endpoint. You can instead mint a scoped **MCP** key in the UI (Settings → Access & keys) — see below. |
| `database_url` | **Optional.** External Postgres instead of the built-in SQLite — see [External database](#external-database-postgres). Blank = SQLite in `/data` (recommended). |
| `ollama_search_key` | **Optional.** An [Ollama](https://ollama.com) API key for **AI product descriptions**: look products up online for a short searchable description (Settings → *AI product descriptions*). Blank = off. |

## Access & keys (hardened mode)

The Edibl companion **integration** authenticates automatically: the add-on mints
a stable API key at startup and advertises it over Supervisor discovery, so the
one-click setup just works — no token to copy, and it survives restarts.

You control all other machine access from the UI at **Settings → Access & keys**
(owner only). Each key has a **scope**:

- **Full access** — REST API + MCP server.
- **REST API only** — the HTTP API (sensors, integrations, exports).
- **MCP only** — the MCP tool server (`/sse`) and nothing else.

…and an **access** class, independent of scope:

- **Read & write** (default) — full use of whatever the scope allows.
- **Read-only** — can only *read*: mutating REST calls (POST/PUT/PATCH/DELETE) get
  `403`, and MCP write-tools (add/update/delete/move/consume/…) are refused; query
  tools still work. Ideal for an external MCP client that should look but not touch.

Revoking a key cuts that access immediately.

**Hardened mode** (`disable_auth: false`): unauthenticated internal callers get
`401`. Ingress (the sidebar) and the auto-paired integration keep working; any
other client must present a key. The bundled MCP server is protected too — in
hardened mode it **requires** a key (it authenticates its own REST calls with the
minted integration key automatically).

**MCP access:** point an MCP client (e.g. HA's **Model Context Protocol** Client)
at `http://<addon-host>:7767/sse` and use a **Full** or **MCP** key as its bearer
token. The MCP endpoint requires a key when **any** of these is true: Edibl runs
in hardened mode (`disable_auth: false`), you set `mcp_server_token`, or you mint
an **MCP**-scoped key. Otherwise (open mode, no server token, no MCP key) it stays
open on the internal network — so if you map port `7767` for LAN access, turn on
one of those first. Minting a *Full* key in open mode does **not** by itself lock
the MCP port.

**Reaching MCP from outside Home Assistant.** To use the MCP server from another
machine (a desktop agent, a remote client), do **both**:

1. Mint an **MCP** (or Full) key in *Settings → Access & keys* and keep it safe.
2. Turn on **`mcp_expose_external`** and map port **`7767`** in the add-on's
   *Network* tab.

With `mcp_expose_external` on, every request **must** carry that key as a Bearer
token, and the server **refuses to start** until at least one MCP or Full key
exists — it will never come up open on a mapped port. Revoke the key to cut a
client off. TLS is your reverse proxy's job if you expose this to the internet;
prefer a VPN/tunnel over a raw WAN port.

## External database (Postgres)

Edibl stores everything in an embedded **SQLite** database in `/data` by default —
zero-config, backed up with the add-on, and the right choice for almost everyone.

If you'd rather use an external **Postgres** (e.g. a shared HA Postgres add-on or a
managed instance), set `database_url` to a psycopg URL:

```
database_url: postgresql+psycopg://edibl:secret@core-postgres:5432/edibl
```

- The schema is created and migrated automatically on start (via Alembic) — no
  manual setup beyond an empty database and a user that can create tables.
- `postgres://` and `postgresql://` URLs are accepted and normalized to the
  bundled **psycopg 3** driver.
- SQLite remains fully supported and is the default.
- Run **one** Edibl instance against a given database. Schema init is serialized
  within a container, but multiple hosts sharing one Postgres aren't coordinated
  (the add-on/compose model is single-instance anyway).

### Moving your existing data to Postgres

Two ways to copy your current SQLite data into a **new, empty** Postgres (your
SQLite data is never modified):

- **From the UI:** open Edibl → **Settings → Migrate to PostgreSQL**, paste the
  target Postgres URL, click **Migrate data**. When it reports the rows copied,
  set `database_url` to that URL and restart.
- **On the add-on config:** set `database_url` to the empty Postgres, turn on
  `migrate_from_sqlite`, and **restart** once. Edibl copies the SQLite data into
  Postgres on boot, then runs on it. Leave the option on — it's a no-op once the
  target already has data. If the copy fails, the add-on **stops with an error in
  the log** (your SQLite data is untouched) rather than starting on an empty
  database — fix the target and restart, or revert `database_url` to recover.

Migrate into an **empty** database (Edibl refuses a non-empty target so it can't
clobber data), and ideally when you're not actively editing, since changes made
during the copy aren't captured.

## Using Ollama (recommended) or OpenAI

The chat box (bottom-right on every screen) **requires an LLM provider** — set
one below and it can query *and* act (add / update / remove stock, edit the
shopping list). Without a provider the chat shows setup instructions.

**Ollama, entirely local** — install the community **Ollama** add-on (or point at
any Ollama host), pull a model (`ollama run llama3.1`), then set:

```
llm_provider: ollama
llm_base_url: http://<ollama-host>:11434   # e.g. http://homeassistant.local:11434
llm_model:    llama3.1
```

**OpenAI-compatible** — `llm_provider: openai`, set `llm_api_key`, `llm_model`
(`gpt-4o-mini`), and `llm_base_url` if you use a compatible gateway.

**Anthropic** — `llm_provider: anthropic`, set `llm_api_key`, `llm_model`
(`claude-opus-4-8`).

The assistant uses the same inventory tools as the MCP server, so it can look
things up **and** act (add / update / remove stock, record what you ate/tossed,
edit the shopping list). An unreachable provider shows an error rather than
crashing the chat. For a **local model server** (LM Studio, vLLM, llama.cpp),
use `llm_provider: openai` and set `llm_base_url` to its OpenAI-compatible endpoint.

Once a provider is set it also powers the background AI tooling (Settings page):

- **AI product descriptions** — look products up online and store a short searchable
  description. Runs in the background with progress (needs an ollama.com search key).
- **AI organize** —
  - **Auto-categorize** proposes a grocery category per product; a confident, known
    category is applied automatically, the rest wait for review.
  - **Propose families** suggests display groupings ("Milk" for whole + skimmed).
  - Review pending suggestions on the **Review** page (accept/reject); your choices
    train later runs. Add a *note* or override the *model* per run if you like.

## Talking to your pantry through HA Assist (voice)

Edibl's MCP server exposes the same tools to Home Assistant's own assistant:

1. Set `mcp_enabled: true` and a `mcp_server_token`, and map port `7767`.
2. Add HA's **Model Context Protocol** (MCP Client) integration pointing at
   `http://<addon-host>:7767/sse` with the token.
3. Use **Ollama** or **OpenAI Conversation** as your Assist agent — now "what's
   expiring?" / "add milk to the shopping list" work by voice.

See the repo's `docs/home-assistant.md` for the full wiring, plus the optional
**HACS integration** that surfaces Edibl as sensors (items in stock, expiring,
expired) and an `edibl.add_to_shopping_list` service.
