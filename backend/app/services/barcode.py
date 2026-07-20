"""Barcode enrichment.

When a scanned barcode isn't already a known product, optionally look it up in
the public Open Food Facts database so a scan can pre-fill name / brand / category
for a new item. Best-effort and off by default (network) — enable with
EDIBL_BARCODE_LOOKUP=1. Failures degrade to ``None`` (manual entry still works).

Future computer-vision / on-device recognition can plug in behind the same
``lookup_*`` contract: return ``{name, brand, category}`` or ``None``.
"""
import logging

from flask import current_app

_LOGGER = logging.getLogger("edibl.barcode")

# Map Open Food Facts category tags → Edibl categories (best-effort, coarse).
_OFF_CATEGORY_HINTS = {
    "dairy": "dairy", "milk": "dairy", "cheese": "dairy", "yogurt": "dairy",
    "meat": "meat", "poultry": "meat", "beef": "meat", "pork": "meat",
    "fish": "seafood", "seafood": "seafood",
    "fruit": "produce", "vegetable": "produce", "produce": "produce",
    "bread": "bakery", "bakery": "bakery",
    "frozen": "frozen", "beverage": "beverage", "drink": "beverage",
    "wine": "wine", "beer": "beer", "spirit": "spirits",
    "snack": "snack", "condiment": "condiment", "sauce": "condiment",
}


def _guess_category(tags):
    text = " ".join(tags or []).lower()
    for needle, cat in _OFF_CATEGORY_HINTS.items():
        if needle in text:
            return cat
    return "other"


def lookup_barcode(code: str):
    """Return ``{name, brand, category, barcode}`` for a barcode, or ``None``.

    Guarded by config so tests and offline installs never touch the network.
    """
    if not code or not current_app.config.get("BARCODE_LOOKUP"):
        return None
    import httpx  # local import: only needed when lookup is enabled

    url = f"https://world.openfoodfacts.org/api/v2/product/{code}.json"
    try:
        r = httpx.get(url, timeout=6, headers={"User-Agent": "Edibl/1.0"})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        _LOGGER.info("barcode lookup failed for %s: %s", code, exc)
        return None
    if data.get("status") != 1:
        return None
    p = data.get("product") or {}
    name = (p.get("product_name") or p.get("generic_name") or "").strip()
    if not name:
        return None
    return {
        "name": name,
        "brand": (p.get("brands") or "").split(",")[0].strip(),
        "category": _guess_category(p.get("categories_tags")),
        "barcode": code,
    }
