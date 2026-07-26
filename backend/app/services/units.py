"""Canonical unit normalization + dimensional awareness (aligned with myMeal).

Two jobs:
  * `canonical_unit(raw)` collapses the many spellings for a unit ("Units",
    "pieces", "grams", "Ounce", "cups") to one canonical token, so the database
    stores exactly one spelling. Unknown units pass through lowercased — never
    guessed into the wrong unit.
  * `dimension(unit)` classifies a unit as count / weight / volume, so Edibl and a
    companion myMeal agree on measures when they share shopping lists and plans.

The measure vocabulary + aliases mirror myMeal's services/units.py (volume base =
ml, weight = g); Edibl additionally carries count-based inventory units
(count / pack / bottle / can / jar / box / bag) that myMeal's recipe units omit.
"""

# Canonical measure units grouped by dimension.
_VOLUME = ("ml", "l", "tsp", "tbsp", "fl oz", "cup", "pint", "quart", "gallon")
_WEIGHT = ("g", "kg", "oz", "lb")
_COUNT = ("count", "pack", "bottle", "can", "jar", "box", "bag")

# synonyms / plurals / abbreviations -> canonical (matched case-insensitively).
_ALIASES = {
    # count / containers
    "counts": "count", "unit": "count", "units": "count", "piece": "count",
    "pieces": "count", "pcs": "count", "pc": "count", "ct": "count", "each": "count",
    "ea": "count", "item": "count", "items": "count", "qty": "count", "x": "count",
    "packs": "pack", "packet": "pack", "packets": "pack", "pkg": "pack",
    "pkgs": "pack", "package": "pack", "packages": "pack",
    "bottles": "bottle", "btl": "bottle",
    "cans": "can", "jars": "jar", "boxes": "box", "bags": "bag",
    # weight (myMeal-aligned)
    "gram": "g", "grams": "g", "gr": "g", "gm": "g", "gramme": "g", "grammes": "g",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg", "kgs": "kg",
    "ounce": "oz", "ounces": "oz", "ozs": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "#": "lb",
    # volume (myMeal-aligned)
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "millilitres": "ml",
    "cc": "ml", "liter": "l", "liters": "l", "litre": "l", "litres": "l", "ltr": "l",
    "lt": "l", "teaspoon": "tsp", "teaspoons": "tsp", "ts": "tsp",
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp", "tbl": "tbsp",
    "fluid ounce": "fl oz", "fluid ounces": "fl oz", "floz": "fl oz",
    "fl. oz.": "fl oz", "fl. oz": "fl oz",
    "cups": "cup", "c": "cup", "pints": "pint", "pt": "pint",
    "quarts": "quart", "qt": "quart", "gallons": "gallon", "gal": "gallon",
}

_DIMENSION = {**{u: "volume" for u in _VOLUME},
              **{u: "weight" for u in _WEIGHT},
              **{u: "count" for u in _COUNT}}

# Canonical measure units surfaced as suggestions (see models.UNITS).
CANONICAL_UNITS = _COUNT + _WEIGHT + _VOLUME


def canonical_unit(raw) -> str:
    """Map a free-form unit to its canonical spelling. Blank → 'count' (the app
    default); an unrecognized unit is returned lowercased+trimmed, not remapped."""
    u = (raw or "").strip().lower().rstrip(".")
    if not u:
        return "count"
    return _ALIASES.get(u, u)


def dimension(unit) -> str | None:
    """'count' | 'weight' | 'volume' for a known unit, else None (e.g. a novel unit
    like 'sprig'). Lets the app tell apart measurable (weight/volume) from countable
    stock and agree on dimensions with myMeal."""
    return _DIMENSION.get(canonical_unit(unit))
