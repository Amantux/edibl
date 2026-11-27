# ADR-0001: Risk acceptance for the outbound user-influenced URL guard

**Status:** proposed · **Date:** 2026-11-27
**Residual acceptor (repository owner):** Amantux (`github.com/Amantux/edibl`) — the
founding-household owner. **Owner sign-off: PENDING — UNFILLED.** The sign-off line in
§5 (Residual acceptor and sign-off) stays empty until the owner approves this acceptance;
nothing below may be read as an accepted residual before that approval is recorded here.

## Context

Edibl holds household inventory data and makes outbound calls to two kinds of target:

1. **The LLM endpoint** — chat / tool loop / streaming / single-shot completion /
   vision / disambiguation / enrichment / receipt extraction / Home Assistant relay. The
   base URL is operator-configurable (`EDIBL_LLM_BASE_URL`, add-on options) and owner- or
   household-settable (per-group UI overrides via `PUT /assistant/settings`).
2. **A sibling app** — myMeal (recipes / meal plans / suggestions) and HomeHoard
   (cross-query). The sibling URL is owner-set via the UI (`PUT /integrations/mymeal`) or
   the add-on options / env, and is carried with a bearer token.

Because those values can arrive from the Settings API, the environment, add-on options, or
per-run async-job options — all of which can bypass a save-time check — the guard had to live
in one module and be re-checked at the point of use.

Before the current state, the LLM base URL reached `httpx` unchecked on the chat/completion
path (an env/add-on-options link-local value bypassed every other guard), and the sibling URL
reached the bearer-token-bearing request unchecked. Both are fixed by the guard described in
the Decision sections above, with regression tests asserting on the *guard* (e.g. that no HTTP
client is even built for a blocked URL) rather than on swallowed error text — the pattern later
formalised here as the "no vacuous self-check" gate described in the Gate section.

## Decision

### In scope — threat classes defended by the guard today

- **Scheme/host validation:** `backend/app/services/url_guard.py::llm_url_ok` (definition at
  `backend/app/services/url_guard.py:20`) accepts only `http` / `https` and requires a host;
  a blank URL is allowed (it falls back to the configured default).
- **Link-local rejection:** after resolving the host (`socket.getaddrinfo`), any address whose
  `ipaddress` `is_link_local` is refused (`backend/app/services/url_guard.py:36-47`) — this is
  the cloud metadata endpoint (`169.254.169.254`) and the IPv6 link-local range (`fe80::`).
- **Loopback / private LAN:** deliberately allowed, where a self-hosted Ollama or a sibling
  add-on legitimately runs.

### Callsite inventory — every outbound user-influenced URL path

**Guard core**

- `backend/app/services/url_guard.py::llm_url_ok` — single implementation; called by the save
  path, the model-list path and the point-of-use paths (see below).

**Save-time validation surfaces (user-influenced URL accepted → refused before persist/use)**

- `backend/app/api/assistant.py::put_settings` (`PUT /assistant/settings`, definition at
  `backend/app/api/assistant.py:88`) → `llm_url_ok(str(data["baseUrl"] or ""))` at
  `backend/app/api/assistant.py:100`; refused with `422`.
- `backend/app/api/assistant.py::put_job_settings_endpoint` (`PUT /assistant/job-settings`,
  definition at `backend/app/api/assistant.py:55`) → `llm_url_ok` on the `enrich` / `organize`
  per-run `baseUrl` at `backend/app/api/assistant.py:72`; refused with `422`.
- `backend/app/api/assistant.py::list_models` (`POST /assistant/models`, definition at
  `backend/app/api/assistant.py:125`) → `llm_url_ok(data.get("baseUrl") or "")` at
  `backend/app/api/assistant.py:132`; delegated to `backend/app/services/assistant.py::list_models`.
- `backend/app/api/integrations.py::set_mymeal_settings` (`PUT /integrations/mymeal`, definition at
  `backend/app/api/integrations.py:223`) → `llm_url_ok(url)` at `backend/app/api/integrations.py:234`;
  refused with `422`.

**Point-of-use validation and outbound LLM calls**

- `backend/app/services/assistant.py::_assert_base_url_ok` (definition at
  `backend/app/services/assistant.py:1145`) — raises `ValueError` when the base URL is refused;
  invoked before any HTTP client is built, at:
  - `backend/app/services/assistant.py::run_chat` (`:1382`, assert at `:1393`);
  - `backend/app/services/assistant.py::run_chat_stream` (`:1411`, assert at `:1427`);
  - `backend/app/services/assistant.py::_complete` (definition at `:1553`, assert at `:1557`);
  - `backend/app/services/assistant.py::_complete_vision` (definition at `:1681`, assert at
    `:1686`).
- `backend/app/services/assistant.py::_fetch_models` (definition at `:1320`) — re-validates via
  `llm_url_ok` (`:1332`) before `httpx.Client` use (`:1336`) against
  `<base_url>/api/tags | /models | /v1/models`.
- Outbound completion paths that inherit the point-of-use guard from their caller:
  - `backend/app/services/assistant.py::_complete` → `httpx.Client` POST to
    `<base_url>/api/chat | /chat/completions | /v1/messages | /api/conversation/process`
    (`:1553`, client `:1559`).
  - `backend/app/services/assistant.py::_complete_vision` → same guard, image-capable vendor
    request shapes (`:1681`, client `:1689`).
  - `backend/app/services/assistant.py::_loop_openai_style` (definition at `:1908`, `httpx` at
    `:1909`/`:1920`) and `backend/app/services/assistant.py::_loop_anthropic` (definition at
    `:1955`, `httpx` at `:1956`/`:1965`) — the tool-calling loops used by `run_chat` /
    `run_chat_stream`; their URL comes from the already-asserted `cfg["base_url"]`.
  - `backend/app/services/assistant.py::_stream_openai_style` (definition at `:1513`) and
    `_stream_turn` (definition at `:1452`) — streaming twin; URL built in `_stream_openai_style`
    from the asserted config, consumed by `_stream_turn`.
  - `backend/app/services/assistant.py::_relay_homeassistant` (definition at `:1597`) — relays to
    the Home Assistant conversation agent via `_complete`.

**Outbound consumers of the guarded helpers (user-influenced URL flows into real calls)**

- `backend/app/services/disambiguation.py::explain_candidates` (definition at `:162`) →
  `assistant._complete` (call at `:198`). Callers: the outbound LLM explain branches
  `backend/app/api/stock.py::resolve_stock` (`?explain=1`, around `:437-439`) and
  `backend/app/api/stock.py::consume_by_product` (`"explain": true`, around `:762`).
- `backend/app/services/enrich.py` — `web_search` (fixed hosted Ollama search URL) plus
  `_complete_json` → `assistant._complete` (`:50`, `:58`); synthesis / extraction flows.
- `backend/app/services/placement.py` — `_llm_pick` (`:94`) and `describe_area` (`:116`) →
  `assistant._complete` (`:107`, `:143`).
- `backend/app/services/tooling.py` — `_job_cfg_or_raise` (`:38`) / `_complete_obj` (`:48`) →
  `assistant.job_cfg` / `assistant._complete` (`:42`, `:51`); backs the async
  `categorize` / `cluster` / `enrich` jobs.
- `backend/app/services/jobs.py::_enrich_job` (`:198`) → `assistant.job_cfg` (`:219`) → tooling.
- `backend/app/services/assistant.py` myMeal bridge tools (`h_mymeal_list_recipes` `:755`,
  `h_mymeal_get_recipe` `:766`, …) → `backend/app/services/integrations.py::mymeal_get/_write`
  (guarded by `_url_blocked`).

**Sibling-app outbound calls**

- `backend/app/services/integrations.py::_url_blocked` (definition at `:25`) → `_get` (`:40`) /
  `_write` (`:57`) / `_client` (`:19`); consumed by `mymeal_get/post/put/delete`,
  `homehoard_get`, and the assistant myMeal bridge tools. Loopback/private LAN allowed via the
  shared `llm_url_ok` policy; link-local refused before the bearer-token-bearing client is built.

**Other outbound calls (operator-configured targets, inventoried for completeness)**

- `backend/app/services/barcode.py::_from_off` (hosted Open Food Facts) and `_from_product_db`
  (`BARCODE_DB_URL`).
- `backend/app/services/ha.py::push_notification` (Home Assistant Supervisor proxy).
- `backend/edibl_mcp.py` MCP server → Edibl REST (`_HTTP`, base from env).

**Frontend entry points that feed the guarded backend surfaces**

- `frontend/src/views/Data.vue` — AI provider form (`baseUrl`, `:500`) →
  `PUT /assistant/settings`, `POST /assistant/models`, `PUT /assistant/job-settings`;
  myMeal URL (`:556`) → `PUT /integrations/mymeal`, `POST /integrations/mymeal/test|pull|discover`.
- `frontend/src/views/HomeAssistant.vue` — connect-token / connect-link minting (`connectUrl`).
- `frontend/src/components/ChatAssistant.vue` — reads `GET /assistant/config` (no URL entry).
- `frontend/src/utils/bugReport.js` — client-side GitHub issue deep-link, all values
  percent-encoded; no server-bound outbound call.
- `frontend/src/api.js` — fetch wrapper to the app's own backend (not a user-influenced outbound
  URL).

## Consequences

- **+** One guard implementation, enforced at save-time *and* at the point of use, so
  env/add-on-options values cannot reach `httpx` unchecked.
- **−** Residual risk remains (see Accepted residuals) — accepted pending owner sign-off.
- **−** httpx redirect-following is left on by default; no post-redirect re-validation.

## Accepted residuals (explicitly accepted, pending owner sign-off)

1. **DNS rebinding** — the guard resolves the host at check time; a name re-bound between the
   check and the request is not re-validated. Documented in `backend/app/services/url_guard.py`
   as an acceptable residual for a semi-trusted owner configuring their own endpoint.
2. **Redirect following** — `httpx` follows redirects by default; the guard does not re-check
   redirect targets.
3. **IPv6 / IPv4-mapped address handling** — the guard resolves via `getaddrinfo` and unwraps
   IPv4-mapped IPv6 before applying the link-local test; handling of resolution timing and
   address-set breadth is the residual under acceptance.

## Residual acceptor and sign-off

- **Acceptor:** the repository owner — Amantux (`github.com/Amantux/edibl`), i.e. the
  founding-household owner for the instance-global AI config.
- **Sign-off mechanism:** the owner records approval in this ADR (status → `accepted`, and the
  sign-off line below filled in).
- **Sign-off line: PENDING / UNFILLED.** No owner approval has been recorded here; do not
  treat the residuals above as formally accepted until it is.

## Gate

- **No guard change, no CI security invariant, and no new test self-check may land until this
  ADR exists and the owner has signed off.**
- Reviewers **must check this ADR** before merging any change from the follow-up hardening set:
  ideas **18 / 30 / 36 / 35 / 55 / 11 / 12**.

## References

- `backend/app/services/url_guard.py` · `backend/app/services/assistant.py` ·
  `backend/app/services/integrations.py` · `backend/app/api/assistant.py` ·
  `backend/app/api/integrations.py` · `backend/app/api/stock.py` ·
  `backend/app/services/disambiguation.py` · `backend/app/services/enrich.py` ·
  `backend/app/services/placement.py` · `backend/app/services/tooling.py` ·
  `backend/app/services/jobs.py` · `backend/app/services/barcode.py` ·
  `backend/app/services/ha.py` · `backend/edibl_mcp.py` ·
  `frontend/src/views/Data.vue` · `frontend/src/views/HomeAssistant.vue` ·
  `frontend/src/components/ChatAssistant.vue` · `frontend/src/utils/bugReport.js`.
