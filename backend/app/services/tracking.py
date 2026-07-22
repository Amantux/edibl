"""Contextual tracking-mode defaults.

Casual users shouldn't have to choose how precisely to track milk vs. a spice —
Edibl picks a sensible default from the item's category, and the user can change it
per product. A product's explicit `tracking_mode` always wins.
"""

# category -> default tracking mode (services.quantity dimensions in parentheses).
_BY_CATEGORY = {
    "produce": "count",       # or weight/presence — count is the friendly default
    "dairy": "package",       # cartons/tubs, optional volume
    "meat": "measure",        # weight or package
    "seafood": "measure",
    "bakery": "count",
    "frozen": "portions",     # freezer meals / portions
    "beverage": "package",    # cans/bottles
    "wine": "package",        # bottle count (+ bottle attrs)
    "spirits": "package",
    "beer": "package",
    "dry_goods": "level",     # bins/bags — level or measure
    "condiment": "level",     # jars/bottles — how full
    "snack": "count",
    "other": "count",
}

# What each mode implies for the default unit + whether it's numeric.
DEFAULT_UNIT = {
    "presence": "presence", "level": "half", "count": "count",
    "measure": "g", "package": "pack", "portions": "portion",
}


def default_tracking_mode(category: str) -> str:
    return _BY_CATEGORY.get((category or "other").strip().lower(), "count")


def effective_tracking_mode(product) -> str:
    """The product's explicit mode, else the category default."""
    explicit = (getattr(product, "tracking_mode", "") or "").strip().lower()
    return explicit or default_tracking_mode(getattr(product, "category", "other"))
