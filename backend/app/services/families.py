"""Derive a generic product group ("family") from a specific product name, so
branded variants collapse together — e.g. "Wegmans Teriyaki Marinade" groups under
"Teriyaki Marinade" while the product name stays "Wegmans Teriyaki Marinade".

The good path is the LLM (the extract/classify prompts are told to emit a
brand-stripped group). This heuristic is the offline fallback: strip a leading
known store / house brand; if none matches, return "" and the caller leaves the
family unset (stock grouping then falls back to the product name).
"""

# Store / house brands worth stripping off the front of a name. Longest first so
# "kirkland signature" wins over "kirkland".
_STORE_BRANDS = sorted((
    "trader joe's", "trader joes", "whole foods", "great value", "good & gather",
    "good and gather", "kirkland signature", "kirkland", "market pantry",
    "simple truth", "signature select", "member's mark", "members mark",
    "sam's choice", "sams choice", "amazon fresh", "wegmans", "costco", "walmart",
    "target", "aldi", "kroger", "safeway", "publix", "365",
), key=len, reverse=True)


def generic_family(name) -> str:
    """A brand-stripped generic group for `name`, or "" if no known brand prefix.
    Only strips a leading store/house brand — never guesses on an unbranded name."""
    n = (name or "").strip()
    low = n.lower()
    for brand in _STORE_BRANDS:
        if low.startswith(brand + " "):
            stripped = n[len(brand):].strip(" -–—:")
            return stripped or ""  # don't return the brand alone
    return ""
