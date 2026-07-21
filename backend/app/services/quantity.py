"""Dimension-aware, uncertainty-aware quantity value object.

The authoritative representation for "how much" of something. A bare float can't
tell *unknown* from *zero* from *one*, can't stop you summing grams and litres, and
rounds money-like decimals badly. `Quantity` fixes all three:

- `kind` distinguishes exact / estimated / approximate / presence / unknown.
  **Unknown is never 0 and never 1.**
- `dimension` (derived from `unit`) blocks cross-dimension arithmetic; conversion is
  deterministic *within* a dimension only. No volume↔mass without a product density.
- `value` is a `Decimal`, so exact amounts stay exact.

See docs/stock-redesign/adr/0002-quantity-value-object.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

# Quantity kinds. Ordered loosely most→least certain; `unknown` and `presence`
# carry NO numeric value (value is None) and must never be coerced to a number.
EXACT = "exact"
ESTIMATED = "estimated"
APPROXIMATE = "approximate"
PRESENCE = "presence"      # "we have it", amount unmeasured
UNKNOWN = "unknown"        # we don't even know if we have it
QUANTITY_KINDS = (EXACT, ESTIMATED, APPROXIMATE, PRESENCE, UNKNOWN)
_NUMERIC_KINDS = frozenset({EXACT, ESTIMATED, APPROXIMATE})

# Dimensions. count/mass/volume convert freely within themselves; package/serving
# convert only unit-for-unit (a "pack" has no standard size — it's product-specific,
# so a pack and a bottle never sum); level/presence are not additive at all.
COUNT, MASS, VOLUME, PACKAGE, SERVING, LEVEL, PRESENCE_DIM = (
    "count", "mass", "volume", "package", "serving", "level", "presence")

# unit -> (dimension, factor-to-base). Base units: count=1 item, mass=grams,
# volume=millilitres. Package/serving units carry factor 1 and only sum unit-for-unit.
_BASE = {COUNT: "count", MASS: "g", VOLUME: "ml"}
UNIT_DIMENSIONS: dict[str, tuple[str, Decimal]] = {}


def _reg(dim: str, factor, *units):
    for u in units:
        UNIT_DIMENSIONS[u] = (dim, Decimal(str(factor)))


_reg(COUNT, 1, "count", "ct", "each", "ea", "pcs", "piece", "pieces", "unit", "egg", "eggs")
_reg(COUNT, 12, "dozen", "doz")
_reg(MASS, 1, "g", "gram", "grams")
_reg(MASS, 1000, "kg", "kilo", "kilos")
_reg(MASS, 0.001, "mg")
_reg(MASS, 28.349523125, "oz", "ounce", "ounces")
_reg(MASS, 453.59237, "lb", "lbs", "pound", "pounds")
_reg(VOLUME, 1, "ml", "milliliter", "millilitre")
_reg(VOLUME, 1000, "l", "liter", "litre", "liters", "litres")
_reg(VOLUME, 10, "cl")
_reg(VOLUME, 29.5735295625, "floz", "fl_oz", "fluid_ounce")
_reg(VOLUME, 236.5882365, "cup", "cups")
_reg(VOLUME, 14.78676478125, "tbsp", "tablespoon")
_reg(VOLUME, 4.92892159375, "tsp", "teaspoon")
_reg(VOLUME, 3785.411784, "gal", "gallon")
_reg(VOLUME, 946.352946, "qt", "quart")
_reg(VOLUME, 473.176473, "pt", "pint")
# Package-ish units: each is its own thing; factor 1, sums only unit-for-unit.
_reg(PACKAGE, 1, "pack", "package", "bottle", "carton", "can", "jar", "bag",
     "box", "case", "tin", "tub", "packet", "sachet", "roll")
_reg(SERVING, 1, "serving", "servings", "portion", "portions", "meal", "meals")

# Qualitative levels, ordered. Not numeric, not additive; the max wins on rollup.
LEVELS = ("empty", "low", "quarter", "half", "three_quarter", "full")
for _lv in LEVELS:
    UNIT_DIMENSIONS[_lv] = (LEVEL, Decimal(1))
UNIT_DIMENSIONS["presence"] = (PRESENCE_DIM, Decimal(1))


def dimension_of(unit: str) -> str:
    """Dimension for a unit string. An UNREGISTERED unit gets its OWN dimension
    (`other:<unit>`) rather than defaulting to `count` — otherwise two unrelated
    custom units ("gizmo", "widget") would silently sum into one fabricated total.
    Unknown units therefore only add to an identical unit, never across."""
    u = (unit or "").strip().lower()
    known = UNIT_DIMENSIONS.get(u)
    return known[0] if known else f"other:{u}"


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Quantity:
    value: Optional[Decimal]
    unit: str = "count"
    kind: str = EXACT
    lower: Optional[Decimal] = None
    upper: Optional[Decimal] = None
    confidence: Optional[float] = None
    provenance: str = "manual"
    dimension: str = field(default="", compare=False)

    def __post_init__(self):
        object.__setattr__(self, "unit", (self.unit or "count").strip().lower())
        object.__setattr__(self, "value", _to_decimal(self.value))
        object.__setattr__(self, "lower", _to_decimal(self.lower))
        object.__setattr__(self, "upper", _to_decimal(self.upper))
        object.__setattr__(self, "dimension", dimension_of(self.unit))
        if self.kind not in QUANTITY_KINDS:
            object.__setattr__(self, "kind", EXACT)
        # Non-numeric kinds must not carry a false number.
        if self.kind in (UNKNOWN, PRESENCE):
            object.__setattr__(self, "value", None)

    # -- construction helpers -------------------------------------------------
    @classmethod
    def unknown(cls, unit="count") -> "Quantity":
        return cls(value=None, unit=unit, kind=UNKNOWN)

    @classmethod
    def present(cls, unit="count") -> "Quantity":
        return cls(value=None, unit=unit, kind=PRESENCE)

    @property
    def is_numeric(self) -> bool:
        return self.kind in _NUMERIC_KINDS and self.value is not None

    # -- arithmetic (dimension-safe) -----------------------------------------
    def _factor(self) -> Decimal:
        return UNIT_DIMENSIONS.get(self.unit, (COUNT, Decimal(1)))[1]

    def to_base(self) -> Optional[Decimal]:
        """Value in the dimension's base unit, or None if non-numeric."""
        if not self.is_numeric:
            return None
        return self.value * self._factor()

    def can_add(self, other: "Quantity") -> bool:
        if self.dimension != other.dimension:
            return False
        # count/mass/volume convert; package/serving only sum unit-for-unit.
        if self.dimension in (PACKAGE, SERVING):
            return self.unit == other.unit
        if self.dimension in (LEVEL, PRESENCE_DIM):
            return False  # qualitative levels don't sum
        return True

    def add(self, other: "Quantity") -> "Quantity":
        """Sum two quantities. Raises ValueError across incompatible dimensions.
        If EITHER side is non-numeric (unknown/presence/level), the result is
        unknown in this unit — we refuse to invent a total."""
        if not self.can_add(other):
            raise ValueError(
                f"cannot add {self.unit} ({self.dimension}) and "
                f"{other.unit} ({other.dimension})")
        if not (self.is_numeric and other.is_numeric):
            return Quantity.unknown(self.unit)
        base = self.to_base() + other.to_base()
        # express the sum back in self's unit
        total = base / self._factor()
        kind = EXACT if self.kind == other.kind == EXACT else ESTIMATED
        return Quantity(value=total, unit=self.unit, kind=kind)

    # -- presentation ---------------------------------------------------------
    def _n(self, d: Decimal) -> str:
        d = d.normalize()
        s = format(d, "f")
        return s

    def describe(self) -> str:
        """Natural-language amount: 'about half', '6 count', 'low', 'some',
        'unknown amount'. Never a bare invented number for a non-numeric kind."""
        if self.kind == UNKNOWN:
            return "unknown amount"
        if self.kind == PRESENCE:
            return "some"
        if self.dimension == LEVEL:
            return self.unit.replace("_", " ")
        if self.value is None:
            return "unknown amount"
        n = self._n(self.value)
        unit = "" if self.unit == "count" else f" {self.unit}"
        if self.kind == ESTIMATED:
            return f"about {n}{unit}"
        if self.kind == APPROXIMATE and (self.lower is not None or self.upper is not None):
            lo = self._n(self.lower) if self.lower is not None else n
            hi = self._n(self.upper) if self.upper is not None else n
            return f"{lo}–{hi}{unit}"
        return f"{n}{unit}"

    def as_dict(self) -> dict:
        return {
            "value": (float(self.value) if self.value is not None else None),
            "unit": self.unit,
            "kind": self.kind,
            "dimension": self.dimension,
            "lower": (float(self.lower) if self.lower is not None else None),
            "upper": (float(self.upper) if self.upper is not None else None),
            "confidence": self.confidence,
            "text": self.describe(),
        }


def aggregate(quantities: list["Quantity"]) -> list["Quantity"]:
    """Dimension-safe rollup: sum only within compatible dimension+unit buckets,
    so a group of mixed units yields one total PER bucket — never a single invalid
    number. A bucket containing any non-numeric quantity collapses to unknown."""
    buckets: dict[tuple, Quantity] = {}
    for q in quantities:
        key = (q.dimension, q.unit if q.dimension in (PACKAGE, SERVING, LEVEL) else "_base")
        if key in buckets:
            try:
                buckets[key] = buckets[key].add(q)
            except ValueError:
                buckets[(q.dimension, q.unit, id(q))] = q  # shouldn't happen: same key ⇒ addable
        else:
            buckets[key] = q
    return list(buckets.values())
