# ADR-0007: Threat model — the URL guard (SSRF) for user-influenced outbound URLs

**Status:** accepted · **Date:** 2026-11-27 · **Type:** threat-model ADR (documentation-only
companion to the pinned-IP egress fix — no guard code changed by this ADR)

## Context

Edibl's server holds bearer credentials for an LLM provider and for companion apps (myMeal,
HomeHoard) and makes outbound HTTP calls to the endpoint each credential belongs to. Those
endpoints are *operator- or household-configured*, and the configuration reaches the code
through more than one surface that do not all pass through the same validation:

- Env / Home Assistant add-on options — `EDIBL_LLM_PROVIDER`, `EDIBL_LLM_BASE_URL`, `EDIBL_LLM_API_KEY`,
  `EDIBL_LLM_MODEL`, `EDIBL_LLM_AGENT_ID`, `EDIBL_MYMEAL_URL`, `EDIBL_MYMEAL_TOKEN`,
  `EDIBL_HOMEHOARD_URL`, `EDIBL_HOMEHOARD_TOKEN`, `EDIBL_BARCODE_DB_URL`, `EDIBL_BARCODE_DB_KEY`, …
- Per-household UI overrides — `llm_*` / `mymeal_*` keys in the settings store
  (`backend/app/services/settings.py`).

The save-time guards (`PUT /api/v1/assistant/settings`, `PUT /api/v1/assistant/job-settings`,
`POST /api/v1/assistant/models`, `PUT /api/v1/integrations/mymeal`) all call
`url_guard.llm_url_ok`, but the env/add-on-options layer never passes through those endpoints, so
a value set via env or add-on options reaches the outbound client unchecked unless the guard also
runs at the point of use.

The threat modelled here is **server-side request forgery (SSRF)**: the server must not be able to
be pointed at an internal / unauthorised target — most importantly the cloud metadata endpoint
`169.254.169.254` / `fe80::` — while still being able to reach endpoints the owner legitimately
configured (a self-hosted Ollama on loopback/LAN, a companion add-on on the private LAN).

The same boundary is where the **pinned-IP egress** design decision is recorded: the outbound
client resolves the configured host and connects to the resolved address, so the TCP leg is
pinned, and the TLS leg must still prove which host it is talking to.

## Decision

This ADR records the threat model for **every outbound callsite that takes a user-influenced
URL**, plus the accepted residuals and the accepted env/options bypass.

### D1 — Outbound egress: pinned address, TLS verified against the original hostname

The outbound client for a validated base URL is the pinned transport
(`backend/app/services/pinned_transport.py`): `pin_base_url` resolves the configured host and
returns `(pinned_url, original_host)` — the scheme + resolved address + port, and the configured
hostname — and the pinned transport's TLS layer uses that original host as the TLS servername / SNI
and as the expected hostname for the certificate-chain check.

**TLS verification happens against the original URL's hostname at connect time, never against the
pinned IP.** The pinned IP is the address that is dialed; the original URL hostname is the identity
that is verified. The requirement is **explicit, not implied**, and it holds for every outbound call
routed through the pinned transport:

- a certificate-chain mismatch against the original hostname **refuses the connection**
  (**fail closed**);
- an unresolvable / malformed host raises at connect time (**fail closed**) rather than silently
  permitting the connection.

### D2 — The guard's verdict is not an authorization

`backend/app/services/url_guard.py::llm_url_ok` keeps a fail-open posture on an unresolvable host:
it returns `(True, None)` so the real request can surface a clean error rather than a 500. That is a
guard-time decision and is **not** an authorization of the outbound connection. The connect-time
pinned transport is the **sole point of enforcement** and fails closed there, so a guard-time
`(True, None)` on an unresolvable host never authorizes a pinned connection.

## Consequences

- **+** A pinned-IP connection still proves which host it is talking to: the certificate chain is
  checked against the original hostname, never the bare pinned IP, and a mismatch refuses the
  connection.
- **+** An unresolvable host fails closed at connect time rather than being authorized by the
  guard.
- **−** The pinned transport resolves the host itself at connect time; the guard's resolution is
  not shared with the dial path.

## Accepted residuals

1. **DNS rebinding** — the guard resolves the host at check time; a name re-bound between the check
   and the request is not re-validated. Accepted residual.
2. **Redirect following** — accepted residual for the outbound posture; no post-redirect re-validation
   is performed by the pinned transport.
3. **IPv6 / IPv4-mapped address handling** — accepted residual (documented in the guard).

## Kill criterion

If the threat model is confirmed as "owner configures their own endpoint", further hardening
(redirect handling, DNS-rebinding resistance, pinned-address connections) is gated on evidence of
a real attack and is not shipped by default.

## References

- `backend/app/services/pinned_transport.py`
- `backend/app/services/url_guard.py`
- `backend/app/services/assistant.py`
- `backend/app/services/integrations.py`
- `backend/tests/test_pinned_tls.py`
- `backend/tests/test_url_guard.py`
- `docs/adr/0001-url-guard-risk-acceptance.md`
