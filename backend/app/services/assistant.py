"""Edibl chat assistant — a provider-neutral, tool-using kitchen helper.

The same inventory actions the MCP server exposes are available here as in-process
tools, so an LLM can answer "what's expiring?", add what you bought, record what
you ate, and manage the shopping list — from a chat box on every screen.

Provider is operator-configured (built for Home Assistant deployments):

    EDIBL_LLM_PROVIDER = ollama | openai | anthropic
    EDIBL_LLM_BASE_URL, EDIBL_LLM_API_KEY, EDIBL_LLM_MODEL

`ollama` and `openai` speak the OpenAI-style function-calling shape; `anthropic`
speaks the Messages API. An LLM provider is required — with none configured the
chat returns setup guidance instead of a degraded experience.

Tool handlers operate directly on the DB (no HTTP hop) and are fully unit-tested;
the network provider loops follow each vendor's documented tool-calling shape.
"""
import json
import logging
from datetime import datetime

from flask import current_app

from ..extensions import db
from ..models import (StockLot, Product, ShoppingItem, ConsumptionEvent, utcnow,
                      STORAGE_METHODS, OUTCOMES, LOSS_OUTCOMES)
from ..services.estimation import estimate_expiry, product_insights, waste_insights

_LOGGER = logging.getLogger("edibl.assistant")

SYSTEM_PROMPT = (
    "You are Edibl, a concise, friendly kitchen-inventory assistant. You know what "
    "food the household actually has on hand, where it is, and how fresh it is. Use "
    "the provided tools to look things up and to make changes (adding stock, "
    "recording what was eaten or thrown out, and editing the shopping list). Prefer "
    "calling a tool over guessing. When you record that food went bad, be gentle and "
    "offer a practical tip. Keep replies short and to the point."
)


# --------------------------------------------------------------------------- #
# In-process inventory tools (handlers take group_id + kwargs, return text)
# --------------------------------------------------------------------------- #
def _active(gid):
    return (db.session.query(StockLot)
            .filter_by(group_id=gid, finished=False).all())


def _match_lots(gid, name):
    q = (name or "").lower().strip()
    lots = [s for s in _active(gid) if s.product and q and q in s.product.name.lower()]
    lots.sort(key=lambda s: (s.expiry_date is None, s.expiry_date or s.created_at))
    return lots


def _resolve_product(gid, name, category="other", family=""):
    name = (name or "").strip()
    p = db.session.query(Product).filter_by(group_id=gid, name=name).first()
    if not p:
        p = Product(name=name, category=(category or "other").strip(),
                    family=(family or "").strip(), group_id=gid)
        db.session.add(p)
        db.session.flush()
    elif family and not p.family:
        p.family = family.strip()
    return p


def h_do_i_have(gid, ingredient=""):
    lots = _match_lots(gid, ingredient)
    if not lots:
        return f"No, there's no {ingredient} in stock."
    total = round(sum(s.quantity or 0 for s in lots), 2)
    where = sorted({s.location.name for s in lots if s.location})
    unit = lots[0].unit
    loc = f" ({', '.join(where)})" if where else ""
    return f"Yes — about {total} {unit} of {ingredient}{loc}."


def h_whats_in_stock(gid, query=""):
    lots = _active(gid)
    if query:
        ql = query.lower()
        lots = [s for s in lots if s.product and ql in s.product.name.lower()]
    if not lots:
        return "Nothing matching in stock." if query else "Stock is empty."
    lines = [f"{s.product.name if s.product else '?'}: {s.quantity} {s.unit}"
             f"{' — ' + s.location.name if s.location else ''}" for s in lots[:40]]
    return "\n".join(lines)


def h_expiring_soon(gid, days=5):
    from ..schemas.serializers import expiry_status, _days_to_expiry
    out = []
    for s in _active(gid):
        st = expiry_status(s.expiry_date)
        d = _days_to_expiry(s.expiry_date)
        if st in ("expiring", "expired") and (d is None or d <= int(days)):
            out.append((d if d is not None else 9999, s))
    out.sort(key=lambda t: t[0])
    if not out:
        return f"Nothing expiring within {days} days. 🥬"
    return "\n".join(
        f"{s.product.name if s.product else '?'} — "
        f"{'expired' if (d is not None and d < 0) else str(d) + 'd'}"
        for d, s in out[:30])


def _find_location(gid, location):
    if not location:
        return None
    from ..models import Location
    loc = (db.session.query(Location)
           .filter(Location.group_id == gid, Location.name.ilike(location)).first())
    return loc.id if loc else None


def h_add_stock(gid, name, quantity=1, unit="count", category="other",
                storage_method="refrigerated", location="", freshness="",
                source="", family=""):
    if storage_method not in STORAGE_METHODS:
        storage_method = "refrigerated"
    product = _resolve_product(gid, name, category, family)
    purchase = utcnow()
    expiry, estimated = estimate_expiry(purchase, product.category, storage_method,
                                        product.shelf_life_days,
                                        group_id=gid, product_id=product.id)
    lot = StockLot(product_id=product.id, location_id=_find_location(gid, location),
                   quantity=float(quantity or 1), unit=unit or "count",
                   storage_method=storage_method, state=freshness or "",
                   source=source or "", purchase_date=purchase, expiry_date=expiry,
                   expiry_estimated=estimated, group_id=gid)
    db.session.add(lot)
    db.session.commit()
    exp = expiry.date().isoformat() if expiry else "n/a"
    return f"Added {quantity} {unit} of {name} ({storage_method}); best-by ~{exp}."


def h_update_stock(gid, name, quantity=None, unit=None, location=None,
                   storage_method=None, freshness=None, expiry=None,
                   source=None, notes=None):
    """Edit the soonest-to-expire lot matching `name`."""
    lots = _match_lots(gid, name)
    if not lots:
        return f"No stock matching '{name}' to update."
    s = lots[0]
    if quantity is not None:
        s.quantity = float(quantity)
        if s.quantity <= 0:
            s.finished = True
    if unit:
        s.unit = unit
    if storage_method and storage_method in STORAGE_METHODS:
        s.storage_method = storage_method
    if freshness is not None:
        s.state = freshness.strip()
    if source is not None:
        s.source = source
    if notes is not None:
        s.notes = notes
    if location is not None:
        s.location_id = _find_location(gid, location)
    if expiry:
        try:
            from datetime import datetime as _dt
            s.expiry_date = _dt.fromisoformat(str(expiry).replace("Z", "").replace("+00:00", ""))
            s.expiry_estimated = False
        except ValueError:
            pass
    db.session.commit()
    return f"Updated {s.product.name}: now {s.quantity} {s.unit}."


def h_delete_stock(gid, name):
    """Remove the soonest-to-expire lot matching `name` (discard, no history)."""
    lots = _match_lots(gid, name)
    if not lots:
        return f"No stock matching '{name}' to remove."
    s = lots[0]
    label = f"{s.quantity} {s.unit} of {s.product.name}"
    db.session.delete(s)
    db.session.commit()
    return f"Removed {label} from stock."


def h_grouped_stock(gid, query=""):
    """Stock grouped by product family (organic + filtered milk read as 'Milk')."""
    lots = _active(gid)
    if query:
        ql = query.lower()
        lots = [s for s in lots if s.product and ql in (s.product.family or s.product.name).lower()]
    groups = {}
    for s in lots:
        if not s.product:
            continue
        key = s.product.family or s.product.name
        g = groups.setdefault(key, {"qty": 0.0, "unit": s.unit, "lots": 0})
        g["qty"] = round(g["qty"] + (s.quantity or 0), 2)
        g["lots"] += 1
    if not groups:
        return "Nothing in stock." if not query else f"Nothing grouped under '{query}'."
    return "\n".join(f"{k}: {g['qty']} {g['unit']} across {g['lots']} lot(s)"
                     for k, g in sorted(groups.items()))


def h_record_consumption(gid, name, quantity=1, outcome="eaten"):
    outcome = (outcome or "eaten").lower()
    if outcome not in OUTCOMES:
        outcome = "eaten"
    lots = _match_lots(gid, name)
    if not lots:
        return f"No stock matching '{name}' to update."
    s = lots[0]
    amount = min(float(quantity or 1), s.quantity)
    s.quantity = round(s.quantity - amount, 4)
    days_kept = None
    if s.purchase_date:
        pd = s.purchase_date.replace(tzinfo=None) if s.purchase_date.tzinfo else s.purchase_date
        days_kept = max((datetime.utcnow() - pd).days, 0)
    if s.quantity <= 0:
        s.finished = True
        s.quantity = 0
    db.session.add(ConsumptionEvent(
        product_id=s.product_id, quantity=amount, unit=s.unit,
        reason="used" if outcome == "eaten" else outcome,
        outcome=outcome, days_kept=days_kept, group_id=gid))
    db.session.commit()
    verb = {"eaten": "used", "spoiled": "tossed (spoiled)",
            "expired": "tossed (expired)", "discarded": "discarded"}.get(outcome, "used")
    msg = f"Recorded {amount} {s.unit} of {s.product.name} {verb}."
    if outcome in LOSS_OUTCOMES:
        tip = product_insights(gid, s.product_id).get("suggestion")
        if tip:
            msg += " " + tip
    return msg


def h_add_to_shopping_list(gid, name, quantity=1, unit="count"):
    db.session.add(ShoppingItem(name=name, quantity=float(quantity or 1),
                                unit=unit or "count", source="manual", group_id=gid))
    db.session.commit()
    return f"Added {quantity} {unit} {name} to the shopping list."


def h_shopping_list(gid):
    items = (db.session.query(ShoppingItem)
             .filter_by(group_id=gid, status="needed").all())
    if not items:
        return "The shopping list is empty."
    return "\n".join(f"{i.quantity} {i.unit} {i.name}" for i in items)


def h_food_insights(gid, name=""):
    if name:
        p = db.session.query(Product).filter(
            Product.group_id == gid, Product.name.ilike(f"%{name}%")).first()
        if not p:
            return f"No history for '{name}' yet."
        ins = product_insights(gid, p.id)
        return ins.get("suggestion") or (
            f"{ins['productName']}: {ins['eaten']} eaten, {ins['wasted']} wasted.")
    rows = waste_insights(gid)
    if not rows:
        return "No waste recorded yet — nice."
    return "\n".join(f"{r['productName']}: wasted {r['wasted']}× — {r['suggestion']}"
                     for r in rows)


# name -> (handler, JSON-schema parameters, description)
TOOLS = {
    "do_i_have": (h_do_i_have,
                  {"type": "object", "properties": {
                      "ingredient": {"type": "string"}}, "required": ["ingredient"]},
                  "Check whether an ingredient is in stock, how much, and where."),
    "whats_in_stock": (h_whats_in_stock,
                       {"type": "object", "properties": {
                           "query": {"type": "string"}}},
                       "List current stock, optionally filtered by a name query."),
    "expiring_soon": (h_expiring_soon,
                      {"type": "object", "properties": {
                          "days": {"type": "integer"}}},
                      "List items expiring within N days (use-it-or-lose-it)."),
    "add_stock": (h_add_stock,
                  {"type": "object", "properties": {
                      "name": {"type": "string"},
                      "quantity": {"type": "number"},
                      "unit": {"type": "string"},
                      "category": {"type": "string"},
                      "storage_method": {"type": "string"},
                      "location": {"type": "string"},
                      "freshness": {"type": "string",
                                    "description": "condition: fresh/ripe/unripe/overripe"},
                      "family": {"type": "string",
                                 "description": "display group, e.g. Milk"},
                      "source": {"type": "string",
                                 "description": "where it came from, e.g. Costco, farm"}},
                   "required": ["name"]},
                  "Add something bought or stored. Expiry is auto-estimated."),
    "update_stock": (h_update_stock,
                     {"type": "object", "properties": {
                         "name": {"type": "string"},
                         "quantity": {"type": "number"},
                         "unit": {"type": "string"},
                         "location": {"type": "string"},
                         "storage_method": {"type": "string"},
                         "freshness": {"type": "string"},
                         "expiry": {"type": "string", "description": "ISO date"},
                         "source": {"type": "string"},
                         "notes": {"type": "string"}},
                      "required": ["name"]},
                     "Edit the soonest-to-expire lot matching a name (qty, location, "
                     "expiry, freshness, etc.)."),
    "delete_stock": (h_delete_stock,
                     {"type": "object", "properties": {
                         "name": {"type": "string"}}, "required": ["name"]},
                     "Remove the soonest-to-expire lot matching a name (discard, "
                     "no consumption history — use record_consumption to log why)."),
    "grouped_stock": (h_grouped_stock,
                      {"type": "object", "properties": {
                          "query": {"type": "string"}}},
                      "Stock grouped by product family (e.g. organic + filtered "
                      "milk shown together under 'Milk')."),
    "record_consumption": (h_record_consumption,
                           {"type": "object", "properties": {
                               "name": {"type": "string"},
                               "quantity": {"type": "number"},
                               "outcome": {"type": "string",
                                           "enum": list(OUTCOMES)}},
                            "required": ["name"]},
                           "Record that food was eaten, spoiled, expired, or discarded."),
    "add_to_shopping_list": (h_add_to_shopping_list,
                             {"type": "object", "properties": {
                                 "name": {"type": "string"},
                                 "quantity": {"type": "number"},
                                 "unit": {"type": "string"}},
                              "required": ["name"]},
                             "Add an item to the shopping list."),
    "shopping_list": (h_shopping_list,
                      {"type": "object", "properties": {}},
                      "Show the current shopping list."),
    "food_insights": (h_food_insights,
                      {"type": "object", "properties": {
                          "name": {"type": "string"}}},
                      "Lifecycle insight: what you waste + per-item suggestions."),
}


def _run_tool(gid, name, args, actions):
    fn = TOOLS.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        result = fn[0](gid, **(args or {}))
    except Exception as exc:  # noqa: BLE001 — surface tool errors to the model
        _LOGGER.warning("tool %s failed: %s", name, exc)
        db.session.rollback()
        return f"Tool error: {exc}"
    actions.append({"tool": name, "args": args or {}})
    return result


# --------------------------------------------------------------------------- #
# Provider config
# --------------------------------------------------------------------------- #
_DEFAULTS = {
    "ollama": ("http://localhost:11434", "llama3.1"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com", "claude-opus-4-8"),
}


def _cfg():
    c = current_app.config
    provider = c.get("LLM_PROVIDER") or ""
    base, model = _DEFAULTS.get(provider, ("", ""))
    return {
        "provider": provider,
        "base_url": c.get("LLM_BASE_URL") or base,
        "api_key": c.get("LLM_API_KEY") or "",
        "model": c.get("LLM_MODEL") or model,
        "timeout": c.get("LLM_TIMEOUT", 60),
        "max_steps": c.get("LLM_MAX_STEPS", 6),
    }


_PROVIDERS = ("ollama", "openai", "anthropic")

SETUP_MESSAGE = (
    "The chat assistant needs an LLM. In the Edibl add-on options (or via "
    "EDIBL_LLM_PROVIDER) set a provider — ollama, openai, or anthropic — e.g. a "
    "local Ollama at http://homeassistant.local:11434 with model llama3.1."
)


def config_public():
    cfg = _cfg()
    enabled = cfg["provider"] in _PROVIDERS
    return {"enabled": enabled, "provider": cfg["provider"] or "none",
            "model": cfg["model"] if enabled else None,
            "setup": None if enabled else SETUP_MESSAGE}


def run_chat(gid, messages):
    """messages: [{role: 'user'|'assistant', content: str}] → {reply, actions,
    provider, model, enabled}. Requires a configured LLM provider; without one it
    returns setup guidance rather than a degraded experience."""
    cfg = _cfg()
    provider = cfg["provider"]
    if provider not in _PROVIDERS:
        return {"reply": SETUP_MESSAGE, "actions": [], "provider": "none",
                "model": None, "enabled": False}
    actions = []
    try:
        if provider == "anthropic":
            reply = _loop_anthropic(gid, messages, cfg, actions)
        else:
            reply = _loop_openai_style(gid, messages, cfg, actions)
    except Exception as exc:  # noqa: BLE001 — never 500 the chat box
        _LOGGER.warning("assistant provider '%s' failed: %s", provider, exc)
        return {"reply": f"The '{provider}' assistant is unreachable ({exc}). "
                "Check the base URL and model in the add-on options.",
                "actions": actions, "provider": f"{provider}:error",
                "model": cfg["model"], "enabled": True}
    return {"reply": reply, "actions": actions, "provider": provider,
            "model": cfg["model"], "enabled": True}


# --------------------------------------------------------------------------- #
# OpenAI-style providers (OpenAI + Ollama share the function-calling shape)
# --------------------------------------------------------------------------- #
def _openai_tools():
    return [{"type": "function",
             "function": {"name": n, "description": d, "parameters": p}}
            for n, (_fn, p, d) in TOOLS.items()]


def _loop_openai_style(gid, user_messages, cfg, actions):
    import httpx

    is_ollama = cfg["provider"] == "ollama"
    convo = [{"role": "system", "content": SYSTEM_PROMPT}]
    convo += [{"role": m["role"], "content": m.get("content", "")}
              for m in user_messages if m.get("role") in ("user", "assistant")]
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    url = cfg["base_url"] + ("/api/chat" if is_ollama else "/chat/completions")

    with httpx.Client(timeout=cfg["timeout"]) as client:
        for _ in range(cfg["max_steps"]):
            body = {"model": cfg["model"], "messages": convo,
                    "tools": _openai_tools()}
            if is_ollama:
                body["stream"] = False
            r = client.post(url, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
            msg = (data["message"] if is_ollama
                   else data["choices"][0]["message"])
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return (msg.get("content") or "").strip() or "(no reply)"
            convo.append(msg)
            for tc in tool_calls:
                fn = tc["function"]
                raw_args = fn.get("arguments")
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
                result = _run_tool(gid, fn["name"], args, actions)
                tool_msg = {"role": "tool", "content": result}
                if not is_ollama:
                    tool_msg["tool_call_id"] = tc.get("id", "")
                convo.append(tool_msg)
    return "I've done what I can — ask me to continue if there's more."


# --------------------------------------------------------------------------- #
# Anthropic Messages API
# --------------------------------------------------------------------------- #
def _anthropic_tools():
    return [{"name": n, "description": d, "input_schema": p}
            for n, (_fn, p, d) in TOOLS.items()]


def _loop_anthropic(gid, user_messages, cfg, actions):
    import httpx

    convo = [{"role": m["role"], "content": m.get("content", "")}
             for m in user_messages if m.get("role") in ("user", "assistant")]
    headers = {"Content-Type": "application/json",
               "x-api-key": cfg["api_key"],
               "anthropic-version": "2023-06-01"}
    url = cfg["base_url"] + "/v1/messages"

    with httpx.Client(timeout=cfg["timeout"]) as client:
        for _ in range(cfg["max_steps"]):
            body = {"model": cfg["model"], "max_tokens": 1024,
                    "system": SYSTEM_PROMPT, "tools": _anthropic_tools(),
                    "messages": convo}
            r = client.post(url, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
            blocks = data.get("content", [])
            if data.get("stop_reason") != "tool_use":
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                return text.strip() or "(no reply)"
            convo.append({"role": "assistant", "content": blocks})
            results = []
            for b in blocks:
                if b.get("type") == "tool_use":
                    result = _run_tool(gid, b["name"], b.get("input") or {}, actions)
                    results.append({"type": "tool_result",
                                    "tool_use_id": b["id"], "content": result})
            convo.append({"role": "user", "content": results})
    return "I've done what I can — ask me to continue if there's more."
