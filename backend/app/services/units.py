"""Canonical unit normalization.

Collapse the many spellings users, receipts, and LLMs produce for the same unit
("Units", "pieces", "Piece", "pcs", "grams", "Ounce", …) down to Edibl's canonical
set (models.UNITS) so the database stores exactly one token per unit. Unknown units
pass through lowercased+trimmed — never guessed into the wrong unit.
"""

# canonical -> its synonyms (all matched case-insensitively).
_GROUPS = {
    "count": ("counts", "unit", "units", "piece", "pieces", "pcs", "pc", "ct",
              "each", "ea", "item", "items", "qty", "x"),
    "g": ("gram", "grams", "gm", "gr", "grm"),
    "kg": ("kgs", "kilogram", "kilograms", "kilo", "kilos"),
    "oz": ("ounce", "ounces"),
    "lb": ("lbs", "pound", "pounds"),
    "ml": ("milliliter", "milliliters", "millilitre", "millilitres", "cc"),
    "l": ("liter", "liters", "litre", "litres", "ltr", "lt"),
    "pack": ("packs", "packet", "packets", "pkg", "pkgs", "package", "packages"),
    "bottle": ("bottles", "btl"),
}

_SYNONYMS = {canon: canon for canon in _GROUPS}
for _canon, _syns in _GROUPS.items():
    for _s in _syns:
        _SYNONYMS[_s] = _canon


def canonical_unit(raw) -> str:
    """Map a free-form unit to Edibl's canonical spelling. Blank → 'count' (the app
    default); an unrecognized unit is returned lowercased+trimmed, not remapped."""
    u = (raw or "").strip().lower()
    if not u:
        return "count"
    return _SYNONYMS.get(u, u)
