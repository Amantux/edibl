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


def plan_consumption(lots, amount, demand_unit=None, policy=PREFER_OPEN_FEFO):
    """Plan how to draw `amount` (in `demand_unit`) across ranked lots, spilling
    to the next lot when one runs out. Returns (picks, shortfall).

    Unit-aware: a lot's quantity is in the LOT's unit, which may differ from the
    recipe's `demand_unit` (2 kg on hand vs a 500 g need). Each lot is converted
    into the demand unit to decide how much it satisfies; each Pick's `take` is
    in that lot's OWN unit so ``consume_lot`` deducts correctly. `shortfall` is
    in `demand_unit`. A lot whose unit is a different, non-convertible dimension
    (mass vs volume, no density) is SKIPPED — cooking must never guess how much
    of it satisfies the demand. When `demand_unit` is None the old same-unit
    arithmetic is used unchanged."""
    from ..quantity import convert
    remaining = round(float(amount), 4)  # in demand_unit
    du = (demand_unit or "").strip().lower() or None
    picks: list[Pick] = []
    for s in rank_lots(lots, policy):
        if remaining <= 0:
            break
        lu = (s.unit or "").strip().lower() or None
        avail = s.quantity or 0
        if du and lu and du != lu:
            avail_in_demand = convert(avail, lu, du)
            if avail_in_demand is None:
                continue  # incompatible dimension — don't guess
            take_demand = min(remaining, avail_in_demand)
            take_lot = convert(take_demand, du, lu)
            if not take_lot or take_lot <= 0:
                continue
            picks.append(Pick(lot=s, take=round(take_lot, 4)))
            remaining = round(remaining - take_demand, 4)
        else:
            take = min(remaining, avail)
            if take > 0:
                picks.append(Pick(lot=s, take=round(take, 4)))
                remaining = round(remaining - take, 4)
    return picks, max(remaining, 0)
