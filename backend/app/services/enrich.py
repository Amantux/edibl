"""AI-enriched, searchable product descriptions via Ollama's hosted web search.

Look a product up online and synthesize a short factual description + keywords so
search finds it by what it actually is. Bounded, best-effort — never raises to the
caller (returns None when search is off / nothing found). Synthesis reuses the
app's existing Ollama LLM config (services/assistant._cfg); the web-search key is
the hosted Ollama API key (EDIBL_OLLAMA_SEARCH_KEY).
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import httpx
from flask import current_app

_LOGGER = logging.getLogger("edibl.enrich")
_SEARCH_URL = "https://ollama.com/api/web_search"
_TIMEOUT = 20.0
# Barcode-aggregator/listing hosts — real product pages should rank ahead of these.
_AGGREGATOR_HOSTS = ("barcodelookup", "upcitemdb", "barcodespider", "go-upc",
                     "upcdatabase", "ean-search", "buycott", "barcode-list")


def _search_key() -> str:
    return (current_app.config.get("OLLAMA_SEARCH_KEY") or "").strip()


def enabled() -> bool:
    return bool(_search_key())


def web_search(query, *, key, max_results=3):
    try:
        r = httpx.post(_SEARCH_URL, headers={"Authorization": f"Bearer {key}"},
                       json={"query": query, "max_results": max_results}, timeout=_TIMEOUT)
        r.raise_for_status()
        return (r.json() or {}).get("results") or []
    except Exception as exc:  # noqa: BLE001
        _LOGGER.info("web_search failed: %s", exc)
        return []


def _query(fields) -> str:
    parts = [fields.get(k) for k in ("brand", "name", "category")]
    return " ".join(str(p).strip() for p in parts if p).strip()


def _complete_json(system, user, cfg=None) -> dict | None:
    """One completion through a provider → parsed JSON object, or None.
    Provider-agnostic (uses assistant._complete). ``cfg`` lets the enrich job pass
    the async "Enrichment" preference; None → the chat provider."""
    from .assistant import _cfg, _PROVIDERS, _complete
    cfg = cfg or _cfg()
    if cfg.get("provider") not in _PROVIDERS:
        return None
    reply = _complete(cfg, system, user)
    s, e = reply.find("{"), reply.rfind("}")
    if s != -1 and e > s:
        data = json.loads(reply[s:e + 1])
        return data if isinstance(data, dict) else None
    return None


_SYNTH_SYSTEM = "You write concise, factual product descriptions. Respond ONLY with JSON."


def _synthesize(fields, results, cfg=None):
    snippets = "\n\n".join(f"{r.get('title', '')}\n{r.get('content', '')}"[:600]
                           for r in results[:3])
    name = fields.get("name") or "this product"
    try:
        user = (f"From the web results below, write a concise factual description of the "
                f"food/product '{name}' (1-2 sentences) and 6-10 search keywords. Respond "
                f'ONLY as JSON: {{"description":"...","keywords":["..."]}}.\n\n{snippets}')
        data = _complete_json(_SYNTH_SYSTEM, user, cfg)
        desc = (data.get("description") or "").strip() if data else ""
        if desc:
            return {"description": desc,
                    "keywords": [str(k).strip() for k in (data.get("keywords") or []) if k]}
    except Exception as exc:  # noqa: BLE001 - fall back to raw snippet
        _LOGGER.info("model synthesis failed, using snippet: %s", exc)
    top = results[0] if results else {}
    return {"description": (top.get("content") or top.get("title") or "").strip()[:300],
            "keywords": []}


def rank_results(results):
    """Prefer retailer/manufacturer pages over barcode-aggregator listings (junk
    titles). Stable sort → aggregators sink, original order otherwise preserved."""
    def is_aggregator(r):
        host = (urlparse(r.get("url") or "").hostname or "").lower()
        return any(a in host for a in _AGGREGATOR_HOSTS)
    return sorted(results, key=is_aggregator)


def extract_product(results):
    """Identify the product from ranked web results via the local Ollama model (the
    same /api/generate call describe() uses). Returns {name, brand} or None — beats
    scraping a title because it reads the snippets too."""
    ranked = rank_results(results)
    snippets = "\n\n".join(f"{r.get('title', '')}\n{r.get('content', '')}"[:600]
                           for r in ranked[:3])
    if not snippets.strip():
        return None
    try:
        user = ('From the web results below, identify the single retail food/product. '
                'Respond ONLY as JSON: {"name":"<product name>","brand":"<brand or '
                'empty>"}. If the results are only barcode-lookup pages with no real '
                f'product, return an empty name.\n\n{snippets}')
        data = _complete_json("You identify retail products. Respond ONLY with JSON.", user)
        name = (data.get("name") or "").strip()[:80] if data else ""
        if name:
            return {"name": name, "brand": (data.get("brand") or "").strip()[:80]}
    except Exception as exc:  # noqa: BLE001 - best-effort; caller falls back
        _LOGGER.info("product extraction failed: %s", exc)
    return None


def describe(fields, cfg=None) -> dict | None:
    if not _search_key():
        return None
    query = _query(fields)
    if not query:
        return None
    results = web_search(query, key=_search_key())
    if not results:
        return None
    out = _synthesize(fields, results, cfg)
    if not out.get("description"):
        return None
    out["sources"] = [r.get("url") for r in results[:3] if r.get("url")]
    return out
