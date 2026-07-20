"""Edibl MCP server — AI tooling over your real food inventory.

Runs alongside the app (a lightweight second process) and calls the local REST
API. A conversation agent (in myMeal, Home Assistant, or a chat client) uses
these tools to answer "do I have X?", "what's expiring?", "can I make this
recipe / what's the shortfall?", and to push planned meals + record consumption.

Edibl is the source of truth for what's ACTUALLY on hand; myMeal owns recipes.

Run:  python edibl_mcp.py   (SSE on EDIBL_MCP_HOST:EDIBL_MCP_PORT/sse)
Optional auth: set EDIBL_MCP_SERVER_TOKEN (clients then send Authorization:
Bearer <token>). API auth: set EDIBL_MCP_API_TOKEN when app auth is enabled.
"""
import hmac
import os

import httpx
from mcp.server.fastmcp import FastMCP

API = os.environ.get("EDIBL_MCP_API", "http://127.0.0.1:7746/api/v1")
TOKEN = os.environ.get("EDIBL_MCP_API_TOKEN")
_HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
_HTTP = httpx.Client(base_url=API, headers=_HEADERS, timeout=10)

_fastmcp_kwargs = {}
try:  # relax DNS-rebinding host check so HA/myMeal can reach us by hostname
    from mcp.server.transport_security import TransportSecuritySettings
    _fastmcp_kwargs["transport_security"] = TransportSecuritySettings(
        enable_dns_rebinding_protection=False)
except ImportError:
    pass

mcp = FastMCP("Edibl", **_fastmcp_kwargs)


def _get(path, params=None):
    r = _HTTP.get(path, params=params or {})
    r.raise_for_status()
    return r.json()


def _post(path, json=None):
    r = _HTTP.post(path, json=json or {})
    r.raise_for_status()
    return r.json()


def _put(path, json=None):
    r = _HTTP.put(path, json=json or {})
    r.raise_for_status()
    return r.json()


def _delete(path):
    r = _HTTP.delete(path)
    r.raise_for_status()
    return True


# --------------------------------------------------------------------------- #
# Query the lay of the land
# --------------------------------------------------------------------------- #
@mcp.tool()
def do_i_have(ingredient: str) -> dict:
    """Do I have an ingredient, how much, and where? Use for 'do we have milk?'."""
    d = _get("/have", {"ingredient": ingredient})
    return {"ingredient": d["ingredient"], "have": d["have"], "onHand": d["onHand"],
            "locations": d["locations"]}


@mcp.tool()
def whats_in_stock(query: str = "") -> list:
    """List current stock (optionally filtered by a product-name query)."""
    data = _get("/stock")
    items = data.get("items", [])
    if query:
        q = query.lower()
        items = [i for i in items if i.get("product") and q in i["product"]["name"].lower()]
    return [{"name": i["product"]["name"] if i["product"] else "?",
             "quantity": i["quantity"], "unit": i["unit"],
             "location": i["location"]["name"] if i["location"] else None,
             "expiryStatus": i["expiryStatus"], "daysToExpiry": i["daysToExpiry"]}
            for i in items[:60]]


@mcp.tool()
def expiring_soon(days: int = 5) -> list:
    """Items expiring within N days (use-it-or-lose-it)."""
    data = _get("/dashboard/expiring", {"days": days})
    return [{"name": i["product"]["name"] if i["product"] else "?",
             "daysToExpiry": i["daysToExpiry"], "quantity": i["quantity"],
             "unit": i["unit"],
             "location": i["location"]["name"] if i["location"] else None}
            for i in data.get("items", [])]


@mcp.tool()
def runout_forecast() -> list:
    """Products predicted to run out soon, based on how fast they're consumed."""
    return _get("/dashboard/runout").get("items", [])


@mcp.tool()
def freezer_inventory() -> list:
    """What's in the freezer (frozen + vacuum-sealed), incl. butchered meat cuts."""
    data = _get("/dashboard/freezer")
    return [{"name": i["product"]["name"] if i["product"] else "?",
             "quantity": i["quantity"], "unit": i["unit"], "attrs": i["attrs"],
             "location": i["location"]["name"] if i["location"] else None}
            for i in data.get("items", [])]


@mcp.tool()
def wine_cellar() -> list:
    """Wine / spirits / beer on hand, with vintage/varietal/etc. where recorded."""
    data = _get("/dashboard/wine")
    return [{"name": i["product"]["name"] if i["product"] else "?",
             "category": i["product"]["category"] if i["product"] else None,
             "quantity": i["quantity"], "attrs": i["attrs"]}
            for i in data.get("items", [])]


# --------------------------------------------------------------------------- #
# Bridge to myMeal: recipes/plans ↔ inventory
# --------------------------------------------------------------------------- #
@mcp.tool()
def check_recipe(ingredients: list) -> dict:
    """Given a recipe's ingredients (from myMeal) as [{name, quantity?, unit?}],
    report what you have, what's short, and whether you can make it now."""
    return _post("/plan/check", {"ingredients": ingredients})


@mcp.tool()
def plan_status() -> dict:
    """The current planned demand (from myMeal) reconciled against on-hand stock:
    per-ingredient availability, the shortfall, and canMakeAll."""
    return _get("/plan")


@mcp.tool()
def ingest_meal_plan(items: list, meal: str = "", source_ref: str = "") -> dict:
    """Propagate planned ingredients from myMeal into Edibl so it tracks demand.
    `items` = [{name, quantity?, unit?, neededBy?}]."""
    return _post("/integrations/mymeal/plan",
                 {"items": items, "meal": meal, "sourceRef": source_ref})


@mcp.tool()
def order_shortfall() -> dict:
    """Add everything the current plan is short on to the shopping list."""
    return _post("/plan/order")


# --------------------------------------------------------------------------- #
# Act on inventory
# --------------------------------------------------------------------------- #
def _location_id(location):
    if not location:
        return None
    locs = _get("/locations")
    match = next((loc for loc in locs if loc["name"].lower() == location.lower()), None)
    return match["id"] if match else None


def _find_lot(name):
    """Soonest-to-expire active lot whose product name contains `name`."""
    items = _get("/stock").get("items", [])
    q = name.lower()
    matches = [i for i in items if i.get("product") and q in i["product"]["name"].lower()]
    return matches[0] if matches else None


@mcp.tool()
def add_stock(name: str, quantity: float = 1, unit: str = "count",
              category: str = "other", storage_method: str = "refrigerated",
              location: str = "", freshness: str = "", source: str = "",
              family: str = "") -> str:
    """Add something you just bought/stored. Categories/units/freshness are free-form.
    `family` is the display group (e.g. 'Milk' for both organic and filtered milk);
    `source` records where it came from. Expiry is auto-estimated."""
    body = {"productName": name, "quantity": quantity, "unit": unit,
            "category": category, "storageMethod": storage_method,
            "freshness": freshness, "source": source, "family": family}
    loc = _location_id(location)
    if loc:
        body["locationId"] = loc
    lot = _post("/stock", body)
    exp = lot.get("expiryDate", "")[:10] if lot.get("expiryDate") else "n/a"
    return f"Added {quantity} {unit} of {name} ({storage_method}); best-by ~{exp}."


@mcp.tool()
def update_stock(name: str, quantity: float = None, unit: str = "",
                 location: str = "", storage_method: str = "", freshness: str = "",
                 expiry: str = "", source: str = "", notes: str = "") -> str:
    """Edit the soonest-to-expire lot matching `name`. Only the fields you pass
    change (quantity, unit, location, storageMethod, freshness, expiry ISO date,
    source, notes)."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    body = {}
    if quantity is not None:
        body["quantity"] = quantity
    if unit:
        body["unit"] = unit
    if storage_method:
        body["storageMethod"] = storage_method
    if freshness:
        body["freshness"] = freshness
    if expiry:
        body["expiryDate"] = expiry
    if source:
        body["source"] = source
    if notes:
        body["notes"] = notes
    if location:
        body["locationId"] = _location_id(location)
    _put(f"/stock/{lot['id']}", body)
    return f"Updated {lot['product']['name']}."


@mcp.tool()
def delete_stock(name: str) -> str:
    """Remove the soonest-to-expire lot matching `name` (discard, no history — use
    record_consumption instead to log that it was eaten/spoiled)."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    _delete(f"/stock/{lot['id']}")
    return f"Removed {lot['quantity']} {lot['unit']} of {lot['product']['name']}."


@mcp.tool()
def grouped_stock(query: str = "") -> list:
    """Stock rolled up by group (product family, else name) — e.g. organic and
    filtered milk shown together under 'Milk', each lot keeping its own expiry."""
    groups = _get("/stock/grouped").get("groups", [])
    if query:
        q = query.lower()
        groups = [g for g in groups if q in g["group"].lower()]
    return [{"group": g["group"], "totalQuantity": g["totalQuantity"], "unit": g["unit"],
             "lots": g["lotCount"], "products": g["products"],
             "nextExpiry": g["nextExpiry"], "expiring": g["expiring"]}
            for g in groups]


@mcp.tool()
def record_consumption(name: str, quantity: float = 1, outcome: str = "eaten") -> str:
    """Record how some of an ingredient left inventory. `outcome` = eaten (default),
    spoiled, expired, or discarded. Feeds runout prediction AND personalized
    shelf-life learning (losses teach Edibl the item goes bad sooner). Consumes
    from the soonest-to-expire matching lot."""
    items = _get("/stock").get("items", [])
    q = name.lower()
    matches = [i for i in items if i.get("product") and q in i["product"]["name"].lower()]
    if not matches:
        return f"No stock matching '{name}'."
    lot = matches[0]  # already sorted soonest-expiry first
    res = _post(f"/stock/{lot['id']}/consume", {"quantity": quantity, "outcome": outcome})
    msg = f"Recorded {quantity} {lot['unit']} of {lot['product']['name']} ({outcome})."
    if res.get("insight"):
        msg += " " + res["insight"]
    return msg


@mcp.tool()
def bulk_add_stock(items: list, storage_method: str = "refrigerated",
                   category: str = "other", location: str = "", source: str = "") -> str:
    """Add many items at once (a grocery haul, a farm box, a butchered animal).
    `items` = [{name, quantity?, unit?, category?, storageMethod?, state?}]. Shared
    args are per-item defaults. Expiry is auto-estimated per item."""
    shared = {"storageMethod": storage_method, "category": category, "source": source}
    if location:
        locs = _get("/locations")
        match = next((loc for loc in locs if loc["name"].lower() == location.lower()), None)
        if match:
            shared["locationId"] = match["id"]
    res = _post("/stock/bulk", {"shared": shared, "items": items})
    return f"Added {res.get('created', 0)} items."


@mcp.tool()
def food_insights(name: str = "") -> dict:
    """Lifecycle insight. With a `name`, per-item stats + a suggestion ('your
    bananas usually last ~5 days'). Without, what you tend to waste, group-wide."""
    if name:
        items = _get("/products", {"q": name})
        if not items:
            return {"error": f"no product matching '{name}'"}
        return _get(f"/products/{items[0]['id']}/insights")
    return _get("/dashboard/lifecycle")


@mcp.tool()
def add_to_shopping_list(name: str, quantity: float = 1, unit: str = "count") -> str:
    """Add an item to the shopping list."""
    _post("/shopping", {"name": name, "quantity": quantity, "unit": unit})
    return f"Added {quantity} {unit} {name} to the shopping list."


@mcp.tool()
def shopping_list() -> str:
    """The current shopping list as paste-ready text (for Uber Eats / Instacart)."""
    return _get("/shopping/export", {"format": "json"}).get("text", "")


# --------------------------------------------------------------------------- #
def _require_token(asgi_app, token: str):
    expected = f"Bearer {token}"

    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if not hmac.compare_digest(headers.get(b"authorization", b"").decode(), expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await asgi_app(scope, receive, send)

    return wrapper


if __name__ == "__main__":
    host = os.environ.get("EDIBL_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("EDIBL_MCP_PORT", "7767"))
    server_token = os.environ.get("EDIBL_MCP_SERVER_TOKEN", "")
    app = mcp.sse_app()
    if server_token:
        app = _require_token(app, server_token)
    else:
        import sys
        print("WARNING: EDIBL_MCP_SERVER_TOKEN unset — MCP endpoint is UNAUTHENTICATED.",
              file=sys.stderr)
    import uvicorn
    uvicorn.run(app, host=host, port=port)
