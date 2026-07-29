"""Barcode enrichment.

When a scanned barcode isn't already a known product, optionally look it up in
the public Open Food Facts database so a scan can pre-fill name / brand / category
for a new item. Best-effort and off by default (network) — enable with
EDIBL_BARCODE_LOOKUP=1. Failures degrade to ``None`` (manual entry still works).

Future computer-vision / on-device recognition can plug in behind the same
``lookup_*`` contract: return ``{name, brand, category}`` or ``None``.
"""
import logging
import math
import re

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


# Our normalized nutrition key -> Open Food Facts nutriment base key.
_NUTR_KEYS = {
    "kcal": "energy-kcal", "protein": "proteins", "carbs": "carbohydrates",
    "sugar": "sugars", "fat": "fat", "satFat": "saturated-fat",
    "fiber": "fiber", "sodium": "sodium",
}


def _num(x):
    """A non-negative finite float, or None (missing/garbled/NaN/inf)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    # Reject NaN, +/-inf and negatives. OFF data is community-entered and json.loads
    # parses a literal `Infinity` token to inf; letting it through would put inf in
    # Product.nutrition, and jsonify would then emit invalid JSON (`Infinity`) that
    # breaks the whole /stock/insights + any stock-list response client-side.
    return v if (math.isfinite(v) and v >= 0) else None


def _nutrition_from_off(p):
    """Extract a compact, normalized nutrition dict from an OFF product, or None.
    Shape: {basis, source, per100?, perServing?, servingSize?, servingQuantity?} where
    per100/perServing carry kcal + macros. Never invents values; sodium is derived from
    salt (salt/2.5) only when a direct sodium figure is absent."""
    nutr = p.get("nutriments")
    if not isinstance(nutr, dict):
        return None

    def block(suffix):
        out = {}
        for our, off in _NUTR_KEYS.items():
            v = _num(nutr.get(f"{off}{suffix}"))
            if v is not None:
                out[our] = round(v, 2)
        if "sodium" not in out:  # derive from salt only as a fallback
            salt = _num(nutr.get(f"salt{suffix}"))
            if salt is not None:
                out["sodium"] = round(salt / 2.5, 4)
        return out

    per100, per_serving = block("_100g"), block("_serving")
    if not per100 and not per_serving:
        return None
    serving_size = (p.get("serving_size") or "").strip()
    data_per = str(p.get("nutrition_data_per") or "").lower()
    basis = "100ml" if ("ml" in serving_size.lower() or "ml" in data_per) else "100g"
    out = {"basis": basis, "source": "openfoodfacts"}
    if per100:
        out["per100"] = per100
    if per_serving:
        out["perServing"] = per_serving
    if serving_size:
        out["servingSize"] = serving_size[:32]
    sq = _num(p.get("serving_quantity"))
    if sq is not None:
        out["servingQuantity"] = sq
    return out


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
            "barcode": code, "source": "openfoodfacts",
            "nutrition": _nutrition_from_off(p)}


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


# Words that mark a title as a barcode-aggregator/listing page, not a product name.
# Kept deliberately narrow: retailer/brand words (walmart, target, …) are NOT here,
# since they appear in legitimate product titles ("Great Value … | Walmart").
_TITLE_JUNK = re.compile(
    r"\b(upc|ean|gtin|barcode|bar code|lookup|database|scanner)\b", re.I)


def _clean_title(title, code):
    """Turn a search-result page title into a plausible product name: drop the
    barcode digits, split on separators, pick the first product-looking segment
    (not an aggregator page). None if no such segment exists."""
    t = (title or "").replace(code, " ")
    for part in re.split(r"\s*[|–—:·]\s*|\s+-\s+", t):
        part = part.strip()
        if len(part) >= 3 and not part.isdigit() and not _TITLE_JUNK.search(part):
            return part[:80]
    return None


def _from_web_search(code):
    """Last resort: web-search the barcode number for a product name."""
    from . import enrich

    if not enrich.enabled():
        return None
    results = enrich.web_search(f"{code} UPC barcode product", key=enrich._search_key()) or []
    if not results:
        return None
    # Best: let the model name the product from ranked results + snippets.
    hit = enrich.extract_product(results)
    if hit and hit.get("name"):
        return {"name": hit["name"], "brand": hit.get("brand", ""), "category": "other",
                "barcode": code, "source": "websearch"}
    # Fallback (no model / model failed): a clean product segment from a ranked title.
    ranked = enrich.rank_results(results)
    name = next((c for c in (_clean_title(r.get("title"), code) for r in ranked[:4]) if c),
                None)
    if not name:
        name = (ranked[0].get("title") or "").strip()[:80]
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
