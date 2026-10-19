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


def _converter_for(lot):
    """The conversion this lot is entitled to: food-aware, or plain arithmetic.

    ADR-0005 left `selection` on `convert` because the only food name available
    was the product's free text, which resolving HERE would make a guess at the
    moment of a deduction — the guess ADR-0003 reserves for the matching
    service. What unblocks it is `Product.food_key`: an identity chosen once,
    deliberately, and looked up by exact key, so this path performs no matching
    at all.

    Everything that is not a live, current identity falls back to `convert` and
    therefore to today's behaviour — no product, no key, or a key chosen against
    a different dataset snapshot than the one loaded now. That fallback is the
    whole safety argument: the new behaviour is strictly opt-in per product, and
    its absence costs a visible shortfall rather than a wrong deduction.

    Resolved per LOT rather than once per call, because a lot knows its own
    product; the callers all pass one product's lots, but nothing in this
    module's contract says they must.
    """
    from ..quantity import convert, convert_for_food_key
    product = getattr(lot, "product", None)
    key = (getattr(product, "food_key", "") or "").strip()
    if not key:
        return convert
    from ..food_reference import key_is_current
    if not key_is_current(key, getattr(product, "food_key_dataset_version", "") or ""):
        return convert
    return lambda value, from_unit, to_unit: convert_for_food_key(
        value, from_unit, to_unit, key)


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
    arithmetic is used unchanged.

    A lot whose product carries a PERSISTED food identity (`Product.food_key`)
    converts with `convert_for_food_key`, which can cross mass<->volume because
    the food — and so the density — is known. Everything else keeps using
    `convert` and its refusal. See `_converter_for`."""
    remaining = round(float(amount), 4)  # in demand_unit
    du = (demand_unit or "").strip().lower() or None
    picks: list[Pick] = []
    for s in rank_lots(lots, policy):
        if remaining <= 0:
            break
        lu = (s.unit or "").strip().lower() or None
        avail = s.quantity or 0
        if du and lu and du != lu:
            convert_ = _converter_for(s)
            avail_in_demand = convert_(avail, lu, du)
            if avail_in_demand is None or avail_in_demand <= 0:
                continue  # incompatible dimension — don't guess
            if remaining >= avail_in_demand:
                # The whole lot is drawn, so its take is its own quantity — a
                # FACT, not a conversion. Converting back here used to be a
                # round-trip, and a round-trip is only safe when the two
                # directions invert. For plain `convert` they do (exact Decimal
                # ratios), but for a food-aware conversion they demonstrably do
                # NOT: USDA publishes per-food portions that disagree with each
                # other, so alfalfa sprouts are "1 cup = 33 g" AND "1 tbsp =
                # 3 g" (16 tbsp/cup would make that 48 g). foodcore answers
                # tbsp->g from the tbsp portion and g->tbsp from the cup-derived
                # density, and round-tripping the two compounds the discrepancy
                # — measured across the whole shipped dataset, 4.7% of
                # mass<->volume round-trips do not invert, 0.5% are off by more
                # than 5% and the worst (agave syrup, tsp) by 50%. Taking
                # `avail` directly removes that error class from the deduction
                # entirely instead of bounding it, and is exactly equivalent for
                # `convert`, whose round-trip was already exact.
                take_lot, take_demand = avail, avail_in_demand
            else:
                # A PARTIAL draw is a single forward conversion, not a
                # round-trip: "I need 2 tbsp" -> grams uses the tbsp portion
                # directly, which is the measured direction.
                take_lot = convert_(remaining, du, lu)
                if take_lot is None or take_lot <= 0:
                    continue
                take_demand = remaining
                if take_lot > avail:
                    # The two directions disagree: the availability check said
                    # this lot covers `remaining`, but converting the demand
                    # forward asks for more than the lot holds. Clamp the take —
                    # over-drawing a lot is the one failure this module must
                    # never produce — and CREDIT ONLY WHAT `avail` ACTUALLY
                    # BUYS. Crediting the full `remaining` here (the obvious
                    # shape) drove `remaining` to 0, so the planner reported
                    # shortfall=0 and stopped spilling: a 4 tbsp demand took
                    # 10 g of a lot that owed 12 g and left a 1 kg lot
                    # untouched, while telling the user nothing was short. A
                    # silent under-draw is as wrong as an over-draw.
                    #
                    # The forward conversion is linear (grams = amount x a
                    # per-food factor), so inverting it for `avail` is exact and
                    # — unlike `avail_in_demand`, which came from the direction
                    # we just decided not to trust — keeps the take and its
                    # credit on ONE consistent scale.
                    take_demand = remaining * (avail / take_lot)
                    take_lot = avail
            picks.append(Pick(lot=s, take=round(take_lot, 4)))
            remaining = round(remaining - take_demand, 4)
        else:
            take = min(remaining, avail)
            if take > 0:
                picks.append(Pick(lot=s, take=round(take, 4)))
                remaining = round(remaining - take, 4)
    return picks, max(remaining, 0)
