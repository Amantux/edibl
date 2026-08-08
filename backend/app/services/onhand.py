"""Unit-aware on-hand aggregation — the one place that answers 'how much of X'.

Summing every matching lot's raw quantity is wrong the moment units differ:
2 kg + 500 g is not 2.5, and 3 cans + 500 g is not 503. This groups lots so that
ONLY truly additive ones combine — count/mass/volume convert within their
dimension; package/serving/qualitative-level and unregistered units sum only
unit-for-unit. Shared by the /have endpoint and the assistant so both answer the
same way.
"""
from __future__ import annotations

from decimal import Decimal

from .quantity import (COUNT, MASS, UNIT_DIMENSIONS, VOLUME, convert,
                       dimension_of)

_NUMERIC = ("exact", "estimated", "approximate")


def _factor(unit: str) -> Decimal:
    return UNIT_DIMENSIONS.get((unit or "count").lower(), (COUNT, Decimal(1)))[1]


def _sum_key(unit: str) -> str:
    """Lots combine only within this key. Freely-convertible dimensions share a
    key (any g/kg lot joins the mass bucket); everything else keys on its exact
    unit so two package or custom units never fabricate a joint total."""
    d = dimension_of((unit or "count").lower())
    return d if d in (COUNT, MASS, VOLUME) else f"unit:{(unit or 'count').lower()}"


def aggregate_on_hand(lots) -> dict:
    """Return ``{"byUnit": {unit: qty}, "onHand": <dominant>, "unit": <its unit>}``.

    ``byUnit`` is the honest breakdown; ``onHand``/``unit`` are the single
    headline number (the bucket with the most lots, deterministic tiebreak) for
    callers that want one figure. Presence/unknown lots carry no number and
    never contribute — a caller decides "we have it" from the lot count."""
    groups: dict[str, list] = {}
    for s in lots:
        if (getattr(s, "quantity_kind", "exact") or "exact") not in _NUMERIC:
            continue
        u = s.unit or "count"
        groups.setdefault(_sum_key(u), []).append((s.quantity or 0, u))

    by_unit: dict[str, float] = {}
    lot_counts: dict[str, int] = {}
    for entries in groups.values():
        units = [u for _, u in entries]
        # Display in the LARGEST unit present (2.5 kg reads better than 2500 g),
        # tiebreak by most-common then alphabetical for determinism.
        display = sorted(set(units),
                         key=lambda u: (-_factor(u), -units.count(u), u))[0]
        total = 0.0
        for qty, u in entries:
            c = convert(qty, u, display)
            total += c if c is not None else float(qty or 0)
        by_unit[display] = round(by_unit.get(display, 0.0) + total, 4)
        lot_counts[display] = lot_counts.get(display, 0) + len(entries)

    if not by_unit:
        return {"byUnit": {}, "onHand": 0.0, "unit": ""}
    unit = sorted(by_unit, key=lambda u: (-lot_counts[u], u))[0]
    return {"byUnit": by_unit, "onHand": by_unit[unit], "unit": unit}
