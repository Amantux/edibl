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


def _from_off(code):
    """Open Food Facts — food-focused, no key. {name,brand,category,...} or None."""
    import httpx

    url = f"https://world.openfoodfacts.org/api/v2/product/{code}.json"
    try:
        r = httpx.get(url, timeout=6, headers={"User-Agent": "Edibl/1.0"})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 — best-effort
        _LOGGER.info("OFF lookup failed for %s: %s", code, exc)
        return None
    if data.get("status") != 1:
        return None
    p = data.get("product") or {}
    name = (p.get("product_name") or p.get("generic_name") or "").strip()
    if not name:
        return None
    return {"name": name, "brand": (p.get("brands") or "").split(",")[0].strip(),
            "category": _guess_category(p.get("categories_tags")),
            "barcode": code, "source": "openfoodfacts"}


def _from_product_db(code):
    """General product barcode DB (UPCitemdb-style). {name,brand,...} or None."""
    import httpx

    base = current_app.config.get("BARCODE_DB_URL")
    if not base:
        return None
    key = current_app.config.get("BARCODE_DB_KEY")
    headers = {"user_key": key, "key_type": "3scale"} if key else {}
    try:
        r = httpx.get(base, params={"upc": code}, headers=headers, timeout=6)
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
    except Exception as exc:  # noqa: BLE001 — best-effort
        _LOGGER.info("product-DB lookup failed for %s: %s", code, exc)
        return None
    if not items:
        return None
    it = items[0]
    name = (it.get("title") or "").strip()
    if not name:
        return None
    return {"name": name, "brand": (it.get("brand") or "").strip(),
            "category": _guess_category([it.get("category", "")]),
            "barcode": code, "source": "productdb"}


def _from_web_search(code):
    """Last resort: web-search the barcode number for a product name."""
    from . import enrich

    if not enrich.enabled():
        return None
    results = enrich.web_search(f"{code} UPC barcode product", key=enrich._search_key())
    if not results:
        return None
    name = (results[0].get("title") or "").strip()[:80]
    if not name:
        return None
    return {"name": name, "brand": "", "category": "other",
            "barcode": code, "source": "websearch"}


def lookup_barcode(code: str):
    """Return ``{name, brand, category, barcode, source}`` for a barcode, or
    ``None``. Chains Open Food Facts (food) → a general product DB → an Ollama
    web-search fallback. Guarded by config so offline installs never touch the
    network; each step is best-effort and degrades to the next / to None.
    """
    if not code or not current_app.config.get("BARCODE_LOOKUP"):
        return None
    return _from_off(code) or _from_product_db(code) or _from_web_search(code)
