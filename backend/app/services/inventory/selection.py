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
            # ONE factor, used for BOTH directions. This is the whole safety
            # argument of this branch, so it is worth being explicit about:
            #
            # The obvious shape asks the converter twice — `avail` into the
            # demand unit to decide what the lot covers, and `remaining` into
            # the lot unit to decide the take. Those are opposite directions,
            # and a food-aware conversion does NOT invert: USDA publishes
            # per-food portions that contradict each other, so alfalfa sprouts
            # carry "1 cup = 33 g" AND "1 tbsp = 3 g" though 16 tbsp/cup implies
            # 48 g. foodcore answers tbsp->g from the tbsp row and g->tbsp from
            # the cup-derived density, so the two directions disagree by up to
            # 50% (4.7% of mass<->volume round-trips over the shipped dataset
            # fail to invert, 0.5% by more than 5%).
            #
            # Mixing them is not merely imprecise, it silently LOSES stock: a
            # 6 tbsp demand against a 10 g lot credited itself 4.85 tbsp (the
            # reverse scale) for a lot actually worth 3.33 tbsp, so it reported
            # shortfall=0, drew 13.4 g of the 18 g owed, and never touched the
            # next lot. Deriving availability from the SAME factor that computes
            # the take puts both on one scale, makes the planner's arithmetic
            # self-consistent, and makes over-drawing structurally impossible:
            # `remaining < avail / factor` implies `remaining * factor < avail`,
            # so the take can never exceed the lot and needs no clamp.
            #
            # The factor is `convert_(1, ...)` because the conversion is linear
            # (grams = amount x a per-food factor) — verified over the whole
            # dataset, zero non-linear conversions — so one probe characterises
            # it exactly. Asking in the demand->lot direction is also the one
            # that matters: if the demand cannot be expressed in the lot's unit
            # there is no take to compute, and skipping costs a visible
            # shortfall rather than a guess.
            convert_ = _converter_for(s)
            per_demand_unit = convert_(1, du, lu)   # lot-units per 1 demand-unit
            if per_demand_unit is None or per_demand_unit <= 0:
                continue  # incompatible dimension — don't guess
            avail_in_demand = avail / per_demand_unit
            if remaining >= avail_in_demand:
                # The whole lot is drawn, so the take is its own quantity — a
                # fact, not a conversion — and it is credited with what that
                # quantity is actually worth on the same scale.
                take_lot, take_demand = avail, avail_in_demand
            else:
                take_lot, take_demand = remaining * per_demand_unit, remaining
            picks.append(Pick(lot=s, take=round(take_lot, 4)))
            remaining = round(remaining - take_demand, 4)
        else:
            take = min(remaining, avail)
            if take > 0:
                picks.append(Pick(lot=s, take=round(take, 4)))
                remaining = round(remaining - take, 4)
    return picks, max(remaining, 0)
