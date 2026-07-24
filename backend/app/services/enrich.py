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

import httpx
from flask import current_app

_LOGGER = logging.getLogger("edibl.enrich")
_SEARCH_URL = "https://ollama.com/api/web_search"
_TIMEOUT = 20.0


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


def _synthesize(fields, results):
    snippets = "\n\n".join(f"{r.get('title', '')}\n{r.get('content', '')}"[:600]
                           for r in results[:3])
    name = fields.get("name") or "this product"
    try:
        from .assistant import _cfg, _ollama_headers
        cfg = _cfg()
        base, model = cfg.get("base_url"), cfg.get("model")
        if base and model and cfg.get("provider") in ("ollama", "openai"):
            prompt = (f"From the web results below, write a concise factual description "
                      f"of the food/product '{name}' (1-2 sentences) and 6-10 search "
                      f'keywords. Respond ONLY as JSON: {{"description":"...","keywords":'
                      f'["..."]}}.\n\n{snippets}')
            r = httpx.post(f"{base.rstrip('/')}/api/generate", headers=_ollama_headers(cfg),
                           json={"model": model, "prompt": prompt, "stream": False,
                                 "format": "json"}, timeout=_TIMEOUT)
            r.raise_for_status()
            data = json.loads((r.json() or {}).get("response") or "{}")
            desc = (data.get("description") or "").strip()
            if desc:
                return {"description": desc,
                        "keywords": [str(k).strip() for k in (data.get("keywords") or []) if k]}
    except Exception as exc:  # noqa: BLE001 - fall back to raw snippet
        _LOGGER.info("model synthesis failed, using snippet: %s", exc)
    top = results[0] if results else {}
    return {"description": (top.get("content") or top.get("title") or "").strip()[:300],
            "keywords": []}


def describe(fields) -> dict | None:
    if not _search_key():
        return None
    query = _query(fields)
    if not query:
        return None
    results = web_search(query, key=_search_key())
    if not results:
        return None
    out = _synthesize(fields, results)
    if not out.get("description"):
        return None
    out["sources"] = [r.get("url") for r in results[:3] if r.get("url")]
    return out
