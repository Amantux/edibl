"""Stock selection policies — which lot(s) to draw from, made explicit and shared.

Replaces the scattered "take lots[0]" logic. The REST API, the assistant, and MCP
all select through here, so automatic picks are consistent, visible, and testable.
The default policy is FEFO with a bias toward already-opened packages, and it
**spills across lots** (a request larger than one lot draws from the next) — the old
code silently clamped to the first lot.

Safety (ADR-0003): selection never crosses a dietary/allergen/item-type or unit
boundary. This module only ranks *already dimension-compatible* lots of one product;
resolving a name to the right product is the matching service's job (Phase 5).
"""
from __future__ import annotations

from dataclasses import dataclass

# Named, household-overridable policies. FEFO = first-expiring, first-out.
FEFO = "fefo"
PREFER_OPEN_FEFO = "prefer_open_fefo"   # default: use opened packages first, then FEFO
POLICIES = (FEFO, PREFER_OPEN_FEFO)


@dataclass
class Pick:
    lot: object
    take: float


def rank_lots(lots, policy=PREFER_OPEN_FEFO):
    """Order candidate lots by the policy (does not consume)."""
    usable = [s for s in lots if not s.finished and (s.quantity or 0) > 0]
    if policy == PREFER_OPEN_FEFO:
        return sorted(usable, key=lambda s: (
            0 if getattr(s, "package_state", "") == "opened" else 1,
            s.expiry_date is None, s.expiry_date or s.created_at))
    return sorted(usable, key=lambda s: (s.expiry_date is None, s.expiry_date or s.created_at))


def plan_consumption(lots, amount, policy=PREFER_OPEN_FEFO):
    """Plan how to draw `amount` across ranked lots, spilling to the next lot when
    one runs out. Returns (picks, shortfall) — shortfall > 0 means not enough stock.
    Does NOT mutate; the caller runs consume_lot per pick so each is a ledger event."""
    remaining = round(float(amount), 4)
    picks: list[Pick] = []
    for s in rank_lots(lots, policy):
        if remaining <= 0:
            break
        take = min(remaining, s.quantity or 0)
        if take > 0:
            picks.append(Pick(lot=s, take=round(take, 4)))
            remaining = round(remaining - take, 4)
    return picks, max(remaining, 0)
