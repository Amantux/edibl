# ADR-0001: Risk acceptance for the outbound user-influenced URL guard

**Status:** accepted · **Date:** 2026-11-27
**Residual acceptor (repository owner):** Amantux (`github.com/Amantux/edibl`) — the
founding-household owner. **Owner sign-off: ACCEPTED** — explicit risk acceptance recorded for
the outbound egress fail-closed posture.

## Context

Edibl holds household inventory data and makes outbound calls to two kinds of target:

1. **The LLM endpoint** — chat / completion / chat-stream / tool-calling loops / vision /
   disambiguation / enrichment / receipt extraction / Home Assistant relay. The base URL is
   operator-configurable (`EDIBL_LLM_BASE_URL`, add-on options) and owner- or household-settable
   (per-group UI overrides via `PUT /assistant/settings`), and can also arrive through async-job
   options that bypass save-time checks.
2. **A sibling app** — myMeal (recipes / meal plans / suggestions) and HomeHoard (cross-query).

Because those values can arrive from the Settings API, the environment, add-on options, or
per-run async-job options — all of which can bypass a save-time check — the guard lives in one
module and is re-checked at the point of use.

Before the pinned-IP egress fix, the LLM base URL reached the outbound client without a TLS
identity bound to the configured host at the one point where trust is actually established. The
pinned-IP egress fix closes that gap; this ADR records the risk acceptance for the posture it
established.

## Decision

### In scope — threat classes defended by the guard today

- **Scheme/host validation:** `backend/app/services/url_guard.py::llm_url_ok` accepts only `http` /
  `https` and requires a host; a blank URL is allowed (it falls back to the configured default).
- **Link-local rejection:** after resolving the host (`socket.getaddrinfo`), any resolved address
  whose `ipaddress` `is_link_local` is refused (`169.254.169.254`, `fe80::`).
- **Loopback / private LAN:** deliberately allowed, where a self-hosted Ollama or a sibling
  add-on legitimately runs.

### Outbound egress decision — pinned transport + fail closed

- Outbound LLM + sibling traffic routes through the pinned transport
  (`backend/app/services/pinned_transport.py`).
- **TLS verification happens against the original URL's hostname at connect time, never against the
  pinned IP.** A certificate-chain mismatch against the original hostname refuses the connection
  (**fail closed**); an unresolvable host fails closed at connect time rather than being authorized by
  the guard.
- The guard's fail-open verdict `(True, None)` on an unresolvable host is **not** an authorization of
  the outbound connection. The connect-time pinned transport is the **sole point of enforcement** and
  fails closed there.

## Consequences

- **+** The pinned-IP outbound connection proves which host it is talking to — the certificate
  chain is verified against the original hostname, never the bare pinned IP, and any mismatch
  refuses the connection.
- **+** An unresolvable host fails closed at connect time rather than being authorized by the
  guard.
- **−** Residual risk remains and is accepted below.

## Accepted residuals (explicitly accepted, pending owner sign-off)

1. **DNS rebinding** — the guard resolves the host at check time; a name re-bound between the
   check and the request is not re-validated. Accepted residual.
2. **Redirect following** — accepted residual for the outbound posture; no post-redirect re-validation
   is performed by the pinned transport.
3. **IPv6 / IPv4-mapped address handling** — accepted residual (documented in the guard).

## Residual acceptor and sign-off

- **Acceptor:** the repository owner — Amantux (`github.com/Amantux/edibl`), i.e. the
  founding-household owner for the instance-global AI config.
- **Owner sign-off:** **ACCEPTED** — explicit risk acceptance for the residual gap recorded here,
  granted 2026-11-27 as part of the outbound-IP egress follow-up (threat-model ADR-0007 + this
  risk-acceptance gate).

## Gate

- **No guard change, no CI security invariant, and no new test self-check may land until this ADR
  exists and the owner has signed off.** Post-fix follow-up: this ADR now exists and the owner has
  signed off; reviewers must check this ADR before merging any further hardening change
  (ideas 18 / 30 / 36 / 35 / 55 / 11 / 12).

## References

- Threat model: `docs/url-guard-threat-model.md` (ADR-0007)
- Pinned-IP egress transport: `backend/app/services/pinned_transport.py`
- Guard: `backend/app/services/url_guard.py`
- Tests: `backend/tests/test_pinned_tls.py`, `backend/tests/test_url_guard.py`
