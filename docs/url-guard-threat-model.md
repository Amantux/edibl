# ADR-0007: Threat model — the URL guard (SSRF) for user-influenced outbound URLs

**Status:** accepted · **Date:** 2026-11-27 · **Type:** threat-model ADR (documentation-only — no guard code changed, no hardening added, idea 18 not implemented)

## Context

Edibl's server holds bearer credentials for an LLM provider and for companion apps (myMeal,
HomeHoard) and makes outbound HTTP calls to the endpoint each credential belongs to. Those
endpoints are *operator- or household-configured*, and the configuration reaches the code
through more than one surface that do not all pass through the same validation:

- Env / Home Assistant add-on options — `EDIBL_LLM_PROVIDER`, `EDIBL_LLM_BASE_URL`,
  `EDIBL_LLM_API_KEY`, `EDIBL_LLM_MODEL`, `EDIBL_LLM_AGENT_ID`, `EDIBL_MYMEAL_URL`,
  `EDIBL_MYMEAL_TOKEN`, `EDIBL_HOMEHOARD_URL`, `EDIBL_HOMEHOARD_TOKEN`, `EDIBL_BARCODE_DB_URL`,
  `EDIBL_BARCODE_DB_KEY`, … (read in `backend/app/config.py`;
  add-on options mirrored in `addon/config.yaml` as `llm_*` / `barcode_*`).
- Per-household UI overrides — `llm_*` / `mymeal_*` keys in the settings store
  (`backend/app/services/settings.py`: `LLM_KEYS`, `MYMEAL_KEYS`).

The save-time guards (`PUT /api/v1/assistant/settings`, `PUT /api/v1/assistant/job-settings`,
`POST /api/v1/assistant/models`, `PUT /api/v1/integrations/mymeal`) all call
`url_guard.llm_url_ok`, but the env/add-on-options layer never passes through those endpoints,
so a value set via env or add-on options reaches the outbound client unchecked unless the guard
also runs **at the point of use**.

The threat modelled here is **server-side request forgery (SSRF)**: the server must not be able
to be pointed at an internal / unauthorised target — most importantly the cloud metadata endpoint
`169.254.169.254` / `fe80::` — while still being able to reach endpoints the owner legitimately
configured (a self-hosted Ollama on loopback/LAN, a companion add-on on the private LAN).

## Decision

This ADR records the threat model for **every outbound callsite that takes a user-influenced
URL**, plus the accepted residuals and the accepted env/options bypass. It is deliberately
documentation-only: no guard code changed, no hardening added, idea 18 not implemented.

### C1 — LLM base URL (assistant): chat / stream / tool-loop / completion / vision / model probe

| Field | Value |
|---|---|
| Callsite | `backend/app/services/assistant.py` — `_cfg()` resolves the effective base URL (per-group UI override `llm_base_url` > app config `LLM_BASE_URL` > provider default `_DEFAULTS`); the outbound calls are `_loop_openai_style`, `_stream_openai_style`, `_loop_anthropic`, `_relay_homeassistant` → `_complete`, `_complete_vision`, and `_fetch_models`. |
| Guard | `backend/app/services/url_guard.llm_url_ok` via `services/assistant._assert_base_url_ok(cfg)` (raises `ValueError("refusing to reach that base URL: …")`), invoked from `run_chat`, `run_chat_stream`, `_complete`, `_complete_vision`; `job_cfg(...)` validates a per-run `baseUrl`; `_fetch_models` validates before constructing the httpx client. |
| Controller (owner via env / options / settings) | Provider/base URL via env (`EDIBL_LLM_*`) / add-on options (`llm_*`) / per-household UI overrides (`llm_*`) > env > built-in defaults (`ollama → http://localhost:11434`, `openai → https://api.openai.com/v1`, `anthropic → https://api.anthropic.com`, `homeassistant → http://supervisor/core`). |
| Real attacker | The owner / operator through the env/add-on-options path (which bypasses save-time checks); **not** an untrusted third party. |
| Residual risks (accepted) | (1) only link-local is blocked — loopback + private LAN are allowed, deliberately; (2) no DNS-rebinding hardening → **accepted residual** (documented in `llm_url_ok`); (3) blank base URL → allowed, falls back to the configured default; (4) unresolvable host → allowed, the real request fails later. |
| Accepted env/options bypass | The guard runs at the **point of use**, not only at save time; the save-time layers live in `backend/app/api/assistant.py` (`PUT /assistant/settings`, `PUT /assistant/job-settings`, `POST /assistant/models`). |

### C2 — Sibling myMeal URL (`integrations._url_blocked`)

| Field | Value |
|---|---|
| Callsite | `backend/app/services/integrations.py` — `_url_blocked(base_url)` delegates to `llm_url_ok(base_url or "")`; `_get` / `_write` refuse before building a token-bearing httpx client. |
| Controller (owner via env / options / settings) | UI override `mymeal_url` (per household) > env `EDIBL_MYMEAL_URL`; token via `EDIBL_MYMEAL_TOKEN`. |
| Real attacker | The owner through the env/add-on-options path that bypasses the save-time check (`PUT /api/v1/integrations/mymeal` does validate at save, but env/add-on options never pass through it). |
| Residual risks (accepted) | (1) private LAN is allowed **deliberately** — a sibling add-on legitimately runs on the private LAN; (2) link-local is blocked. |
| Accepted env/options bypass | The guard runs at the **point of use** (before the bearer token is attached). |

### C3 — Sibling HomeHoard URL

| Field | Value |
|---|---|
| Callsite | `backend/app/services/integrations.py` — `homehoard_get` → `_get` → `_url_blocked` (same policy). |
| Controller (owner via env / options / settings) | Env / add-on options only: `EDIBL_HOMEHOARD_URL`, `EDIBL_HOMEHOARD_TOKEN`. |
| Real attacker | The owner via the env/add-on-options path. |
| Residual risks (accepted) | Same as C2 (private LAN allowed deliberately; link-local blocked). |
| Accepted env/options bypass | Guard at the point of use via `_get` / `_write`. |

### C4 — Barcode product-database URL (no guard yet)

| Field | Value |
|---|---|
| Callsite | `backend/app/services/barcode.py` — `_from_product_db` → `httpx.get(BARCODE_DB_URL, params={"upc": code})` with `BARCODE_DB_URL` from config (`EDIBL_BARCODE_DB_URL`, default `https://api.upcitemdb.com/prod/trial/lookup`) and `BARCODE_DB_KEY`. |
| Controller (owner via env / options / settings) | Operator via env / add-on options (`EDIBL_BARCODE_DB_URL`, `EDIBL_BARCODE_DB_KEY`). |
| Real attacker | The owner via the env/add-on-options path. |
| Residual risks (accepted) | No SSRF guard exists on this callsite today; the default endpoint is public and the lookup is best-effort and off by default. |
| Accepted env/options bypass | n/a today — no guard to bypass; tracked by this threat model. |

### C5–C8 — Remaining outbound callsites (not user-influenced-URL, or internal)

| Callsite | URL source | Notes |
|---|---|---|
| Web search (`services/enrich.py`) | Fixed `https://ollama.com/api/web_search` + `EDIBL_OLLAMA_SEARCH_KEY | No user-influenced URL; out of the guard's threat model. |
| HA notification push (`services/ha.py`) | Fixed internal `http://supervisor` | Internal hop; not user-influenced. |
| MCP server → own REST API (`backend/edibl_mcp.py`) | `EDIBL_MCP_API` (internal) | Internal service-to-service hop; not an external SSRF target. |
| HA custom integration → Edibl (`custom_components/edibl`) | User-configured host (`CONF_HOST`, default `http://homeassistant.local:7746`) via aiohttp | Outbound from Home Assistant; from Edibl's threat-model perspective it is inbound. Adjacent only. |
| e2e / add-on Supervisor registration | Fixed / internal (`http://supervisor`) | Test/ops tooling; not user-influenced. |

### Accepted summary per callsite

- **LLM base URL (C1):** controller = owner (env / options / UI) · real attacker = owner via env/options bypass of save-time checks · accepted residual risks = link-local-only block, DNS-rebinding accepted residual, blank-URL fallback, unresolvable-host pass-through · accepted bypass = guard at point of use.
- **Sibling myMeal URL (C2):** controller = owner (UI override / env) · real attacker = owner via env/options bypass · accepted residual risks = private-LAN deliberate allowance, link-local blocked · accepted bypass = guard at point of use (before bearer attach).
- **Sibling HomeHoard URL (C3):** same shape as C2 through `_get` / `_write`.
- **Barcode DB URL (C4):** controller = operator (env / options) · real attacker = owner via env · accepted residual = no guard yet; accepted bypass = n/a (no guard).

## Idea 18 reconciliation — private/loopback blocking

**Question:** is idea 18 (blocking private/loopback ranges in addition to link-local) in scope
for the LLM path?

**Answer recorded here:** the current accepted policy of `llm_url_ok` is to block **link-local
only** and to allow **loopback + private LAN**, and that policy is mirrored by the sibling path
(`integrations._url_blocked`) because a sibling add-on legitimately runs on the private LAN.

- For the LLM path: `llm_url_ok` only blocks link-local; private/loopback is allowed (and
  deliberately so — a self-hosted Ollama is the common, legitimate case).
- Idea 18 would make the LLM path **stricter than the sibling path it mirrors**. The recorded
  decision is therefore: **not a uniform strengthening** — the two paths keep their mirrored
  policy unless a per-callsite decision says otherwise. If idea 18 is ever acted on, it is scoped
  per-callsite rather than applied uniformly across the LLM and sibling callsites.

## Kill criterion

If the threat model is confirmed as **“owner configures their own endpoint”**, further hardening
(redirect handling, DNS-rebinding resistance, pinned-address connections) is **gated on evidence
of a real attack** and is **not shipped by default**. The accepted residuals above stand until
that evidence exists.

## Consequences

- Accepted residuals: DNS rebinding for `llm_url_ok` (accepted residual), blank-URL fallback to
  the configured default, unresolvable-host pass-through, deliberate private-LAN allowance, and
  the unguarded barcode-DB callsite (C4) remain as recorded.
- Accepted env/options bypass: the guard runs at the point of use, not only at save time.
- No guard code changed, no hardening added, idea 18 not implemented — as required by this ADR.
