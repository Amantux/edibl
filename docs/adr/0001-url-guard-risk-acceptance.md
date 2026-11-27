# ADR-0001: Risk acceptance for the outbound user-influenced URL guard

**Status:** accepted · **Date:** 2026-11-27
**Residual acceptor (repository owner):** Amantux (`github.com/Amantux/edibl`) — the
founding-household owner. **Owner sign-off: ACCEPTED** — explicit risk acceptance for the
residual gap recorded in §Accepted residuals and out of scope, granted 2026-11-27 as part of
the post-fix follow-up (threat-model ADR + risk-acceptance gate).

## Context

Edibl holds household inventory data and makes outbound calls to two kinds of target:

1. **The LLM endpoint** — chat / completion / chat-stream / tool-calling loops / vision /
   disambiguation / enrichment / receipt extraction / Home Assistant relay. The base URL is
   operator-configurable (`EDIBL_LLM_BASE_URL`, add-on options, env) and owner- or
   household-settable (per-group UI overrides via `PUT /assistant/settings`), and can also
   arrive through async-job options that bypass save-time checks.
2. **A sibling app** — myMeal (recipes / meal plans / suggestions) and HomeHoard (cross-query).
   The sibling URL is owner-set via the UI (`PUT /integrations/mymeal`) or add-on options/env,
   and is carried with a bearer token.

Because those values can arrive from the Settings API, the environment, add-on options, or
per-run async-job options — all of which can bypass a save-time check — the guard has to live in
one module and be re-checked at the point of use.

Before the shipped fix, the LLM base URL reached `httpx` unchecked on the chat/completion path
(an env/add-on-options link-local value bypassed every other guard), and the sibling URL reached
the bearer-token-bearing request unchecked. Both are closed by the guard described below.

## Decision

### Threat model — in-scope threat classes

- **Scheme/host validation:** `backend/app/services/url_guard.py::llm_url_ok` (definition at
  `backend/app/services/url_guard.py:20`) accepts only `http` / `https` and requires a host; a
  blank URL is allowed (falls back to the configured default).
- **Non-public address blocking:** after resolving the host (`socket.getaddrinfo`), any resolved
  address that is `is_private`, `is_loopback`, `is_link_local`, `is_reserved` or `is_multicast` is
  refused, with the IPv4-mapped IPv6 address unwrapped before the test
  (`backend/app/services/url_guard.py:20-56`). The fix widened the guard from link-local-only to
  private / loopback / reserved / multicast blocking, so `10/8`, `172.16/12`, `192.168/16`,
  `169.254/16`, `127/8`, `0/8` and the IPv6 equivalents are refused, alongside the cloud metadata
  endpoint (`169.254.169.254`, `fe80::`).
- **Fail closed:** an unresolvable or malformed host is refused rather than allowed through.
- **Fail closed on redirects / rebound hosts:** `guarded_client` builds the client with
  `follow_redirects=False`; `guarded_redirect_target` re-validates the intended follow-up through
  `llm_url_ok`; `guarded_send` sends one hop and loops with `MAX_REDIRECTS = 10`, aborting when a
  redirect target is unresolvable or rebound to a blocked host.

### Shipped fix — the diff this ADR governs (and its review)

Shipped as one fix chain on branch `forge/edibl/t_14508f8e`, final commit
**`ba6ab239d4db7a108242bc27134d9da7e7c97ec2`**:

- `d7b9760` fix(security): block private/loopback/reserved LLM URLs and fail closed on unresolvable hosts.
- `f7923e2` refactor(security): consolidate duplicated guard logic onto one guarded path.
- `6807bb0` fix(security): fail closed on redirects and rebound hosts.
- `ba6ab23` test(security): add adversarial SSRF coverage for schemes, IP literals, fail-closed paths.

Diff review (the follow-up that consumes this ADR):

- **Single guarded egress chokepoint.** The decision lives once in
  `backend/app/services/url_guard.py::llm_url_ok`; `backend/app/services/assistant.py::_assert_base_url_ok`
  is the point-of-use helper and `backend/app/services/integrations.py::_url_blocked` delegates to
  it. No divergent guard copies remain.
- **All egress through the guarded path.** LLM: `_fetch_models`, `_complete`, `_complete_vision`,
  `run_chat`, `run_chat_stream`, `_stream_turn`, `_stream_openai_style`, `_loop_openai_style`,
  `_loop_anthropic`, `_relay_homeassistant` all issue through `guarded_client` / `guarded_send`,
  with the stream turn following redirects hop by hop via `guarded_redirect_target`. Siblings:
  `backend/app/services/integrations.py::_get` / `_write` route through the guarded send.
- **Adversarial coverage.** `backend/tests/test_url_guard_adversarial.py` covers scheme escapes
  (`file://`, `ftp://`, empty scheme), blank URL, blocked IP literals, fail-closed unresolvable
  host, and fail-closed redirect targets; the full backend suite is green at the shipped commit.

### Callsite inventory — outbound user-influenced URL paths

- **Guard core:** `backend/app/services/url_guard.py::llm_url_ok` (definition at
  `backend/app/services/url_guard.py:20`), plus `guarded_client` (`:63`), `guarded_redirect_target`
  (`:78`), `guarded_send` (`:100`), `MAX_REDIRECTS = 10` (`:60`).
- **Save-time validation surfaces:** `backend/app/api/assistant.py::put_settings` (`:70`),
  `put_job_settings_endpoint` (`:98`), `list_models` (`:130`); `backend/app/api/integrations.py::set_mymeal_settings` (`:236`).
- **Point-of-use / outbound LLM:** `backend/app/services/assistant.py::_assert_base_url_ok`
  (definition at `:1147`, invoked before any client is built), `_fetch_models` (`:1325`, guarded
  calls at `:1332-1348`), `_complete` (`:1565`, assert at `:1569`), `_complete_vision` (`:1692`, assert
  at `:1696`), `run_chat` (`:1382`, assert at `:1393`), `run_chat_stream` (`:1411`, assert at
  `:1427`), `_stream_turn` (`:1452`), `_stream_openai_style` (`:1527`), `_loop_openai_style`
  (`:1918`), `_loop_anthropic` (`:1963`), `_relay_homeassistant` (`:1608`).
- **Siblings:** `backend/app/services/integrations.py::_client` (`:20`), `_url_blocked` (`:27`),
  `_get` (`:41`), `_write` (`:58`); consumers `mymeal_get` / `mymeal_post` / `mymeal_put` /
  `mymeal_delete`, `homehoard_get`.
- **Other outbound calls inventoried for completeness:** `backend/app/services/barcode.py::_from_off`
  (hosted Open Food Facts) and `_from_product_db` (`BARCODE_DB_URL`); `backend/app/services/ha.py`
  notification push; `backend/edibl_mcp.py` MCP → Edibl REST.

## Accepted residuals and out of scope

1. **DNS rebinding — explicitly OUT OF SCOPE.** The guard resolves the host at check time and is
   not hardened against a name re-bound between the check and the request. This residual gap is
   explicitly out of scope for this ADR and for the shipped fix.
2. **Redirect following** — closed in scope by the fail-closed guarded send shipped at
   `6807bb0` (client `follow_redirects=False`, per-hop re-validation, `MAX_REDIRECTS = 10).
3. **IPv6 / IPv4-mapped address handling** — closed in scope by the `getaddrinfo` resolution and
   IPv4-mapped unwrap shipped at `d7b9760`.

## Residual acceptor and sign-off

- **Acceptor:** the repository owner — Amantux (`github.com/Amantux/edibl`), i.e. the
  founding-household owner for the instance-global AI config.
- **Owner sign-off:** **ACCEPTED** — the owner explicitly accepted the residual risk for the
  DNS-rebinding gap recorded here; acceptance recorded 2026-11-27 as part of this follow-up
  (threat-model ADR + risk-acceptance gate).

## Gate

- **No guard change, no CI security invariant, and no new test self-check may land until this ADR
  exists and the owner has signed off.** Post-fix follow-up: this ADR now exists and the owner has
  signed off; reviewers must check this ADR before merging any further hardening change
  (ideas 18 / 30 / 36 / 35 / 55 / 11 / 12).

## References

- Shipped fix commit: **`ba6ab239d4db7a108242bc27134d9da7e7c97ec2`** — `test(security): add
  adversarial SSRF coverage for schemes, IP literals, fail-closed paths`
  (`backend/tests/test_url_guard_adversarial.py`, +211 lines).
- Fix chain: `d7b9760` fix(security): block private/loopback/reserved LLM URLs and fail closed on
  unresolvable hosts; `f7923e2` refactor(security): consolidate duplicated guard logic onto one
  guarded path; `6807bb0` fix(security): fail closed on redirects and rebound hosts.
- Guard: `backend/app/services/url_guard.py` · LLM egress: `backend/app/services/assistant.py` ·
  siblings: `backend/app/services/integrations.py` · save path: `backend/app/api/assistant.py` ·
  tests: `backend/tests/test_chat_ssrf.py`, `backend/tests/test_mymeal_ssrf.py`,
  `backend/tests/test_security.py`, `backend/tests/test_security_hardening.py`.
