"""Edibl MCP server — AI tooling over your real food inventory.

Runs alongside the app (a lightweight second process) and calls the local REST
API. A conversation agent (in myMeal, Home Assistant, or a chat client) uses
these tools to answer "do I have X?", "what's expiring?", "can I make this
recipe / what's the shortfall?", and to push planned meals + record consumption.

Edibl is the source of truth for what's ACTUALLY on hand; myMeal owns recipes.

Run:  python edibl_mcp.py   (SSE on EDIBL_MCP_HOST:EDIBL_MCP_PORT/sse)

Inbound auth (who may call this MCP server):
  * A key minted in Settings → Access & keys with scope `mcp` or `full` (validated
    against the same ApiToken store the REST API uses), OR
  * the legacy static EDIBL_MCP_SERVER_TOKEN (if set).
  Auth is REQUIRED once a server token is set OR any `mcp`-scoped key exists;
  otherwise the endpoint stays open (zero-config), same as before.
Outbound auth (this server → REST API): set EDIBL_MCP_API_TOKEN when app auth is on
  (the add-on wires the minted integration key here in hardened mode).
"""
import hmac
import os
import sys

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
        loc_id = _location_id(location)
        if not loc_id:
            return f"No location named '{location}'. Add it first, or use an existing one."
        body["locationId"] = loc_id
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
def open_stock(name: str) -> str:
    """Mark a package opened (e.g. an opened carton) — an orthogonal facet, separate
    from using it up. Affects freshness/shelf-life, not quantity."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    res = _post(f"/stock/{lot['id']}/open", {})
    return res.get("summary", f"Opened {lot['product']['name']}.")


@mcp.tool()
def adjust_stock(name: str, quantity: float) -> str:
    """Correct a lot to a measured amount (e.g. an estimated bin you just weighed).
    Sets the exact quantity on the soonest-to-expire matching lot; reversible."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    _post(f"/stock/{lot['id']}/adjust", {"quantity": quantity, "quantityKind": "exact"})
    return f"Corrected {lot['product']['name']} to {quantity} {lot['unit']}."


@mcp.tool()
def move_stock(name: str, location: str) -> str:
    """Move the soonest-to-expire lot matching `name` to another location."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    loc_id = _location_id(location)
    if not loc_id:
        return f"No location named '{location}'. Add it first, or use an existing one."
    _post(f"/stock/{lot['id']}/move", {"locationId": loc_id})
    return f"Moved {lot['product']['name']} to {location}."


@mcp.tool()
def split_stock(name: str, quantity: float, location: str = "") -> str:
    """Split `quantity` off the soonest-to-expire lot matching `name` into a new
    position (e.g. portioning). Conserves the total; reversible."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    body = {"quantity": quantity}
    if location:
        loc_id = _location_id(location)
        if not loc_id:
            return f"No location named '{location}'. Add it first, or use an existing one."
        body["locationId"] = loc_id
    _post(f"/stock/{lot['id']}/split", body)
    return f"Split off {quantity} {lot['unit']} of {lot['product']['name']}."


@mcp.tool()
def use_stock(name: str, quantity: float, outcome: str = "eaten") -> str:
    """Use an amount of a product, drawing across its lots by policy (prefer-open,
    then first-expiring-first-out) and spilling to the next lot as needed — the safe
    way to 'use the milk' without picking a specific lot."""
    try:
        res = _post("/stock/consume", {"name": name, "quantity": quantity, "outcome": outcome})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"No stock matching '{name}'."
        raise
    if res.get("consumed", 0) == 0:
        return f"No stock matching '{name}'."
    msg = f"Used {res['consumed']} of {name} across {len(res.get('draws', []))} lot(s)."
    if res.get("shortfall"):
        msg += f" Short by {res['shortfall']}."
    return msg


@mcp.tool()
def freeze_stock(name: str) -> str:
    """Freeze the soonest-to-expire lot matching `name` — extends its shelf life and
    records the freeze date. Reversible."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    _post(f"/stock/{lot['id']}/freeze", {})
    return f"Froze {lot['product']['name']}."


@mcp.tool()
def thaw_stock(name: str) -> str:
    """Thaw the soonest-to-expire frozen lot matching `name` — shortens shelf life
    and records the thaw date. Reversible."""
    lot = _find_lot(name)
    if not lot:
        return f"No stock matching '{name}'."
    _post(f"/stock/{lot['id']}/thaw", {})
    return f"Thawed {lot['product']['name']}."


@mcp.tool()
def make_from(source_name: str, source_quantity: float, product_name: str,
              product_quantity: float = 1, product_unit: str = "portion",
              category: str = "other") -> str:
    """Turn stock into other stock, preserving lineage (e.g. 'made chicken stock
    from the carcass', 'cooked 2 lb chicken into 4 servings'). Consumes
    `source_quantity` of `source_name` and creates the product."""
    src = _find_lot(source_name)
    if not src:
        return f"No stock matching '{source_name}'."
    res = _post("/stock/transform", {
        "sources": [{"lotId": src["id"], "quantity": source_quantity}],
        "products": [{"name": product_name, "quantity": product_quantity,
                      "unit": product_unit, "category": category}]})
    return res.get("summary", f"Made {product_name}.")


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


@mcp.tool()
def search_products(query: str) -> list:
    """Search the product catalog by name, brand, barcode, OR the AI-generated
    description — so a vague query ("something for a stir-fry", a model number)
    can still find a product even when the name alone wouldn't match. Returns
    [{name, brand, category}]."""
    products = _get("/products", {"q": query})
    return [{"name": p.get("name"), "brand": p.get("brand"),
             "category": p.get("category")} for p in products]


@mcp.tool()
def describe_product(name: str) -> str:
    """Look a product up online (web search) and store a short searchable
    description for it, so future searches find it by what it actually is.
    Requires a matching product and a configured Ollama search key."""
    products = _get("/products", {"q": name})
    if not products:
        return f"No product matching '{name}'."
    try:
        r = _post(f"/products/{products[0]['id']}/describe")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            return "Web search isn't configured (set an Ollama search key)."
        if e.response.status_code == 422:
            return f"Couldn't find a description for '{name}' online."
        raise
    return r.get("description") or f"Described {products[0]['name']}."


@mcp.tool()
def reorder_suggestions() -> list:
    """What to buy now — items below their reorder level, accounting for reserved
    stock (richer than the meal-plan shortfall). Returns the suggestion list."""
    return _get("/shopping/reorder").get("suggestions", [])


# --------------------------------------------------------------------------- #
# Inbound authorization — validate presented keys against the app's ApiToken store
# (the same keys managed in the UI), plus the legacy static server token.
_app = None


def _get_app():
    """Build the Flask app once (reused across requests) for DB-backed key checks.
    The entrypoint initializes the schema before launching this process, so this is
    a cheap idempotent create_all against an existing DB."""
    global _app
    if _app is None:
        from app import create_app
        _app = create_app()
    return _app


def _key_ok(raw: str) -> bool:
    """True if `raw` is a live ApiToken whose scope allows MCP (`mcp` or `full`)."""
    if not raw:
        return False
    try:
        app = _get_app()
        from app.extensions import db
        from app.models import ApiToken, hash_token
        with app.app_context():
            rec = db.session.query(ApiToken).filter_by(token_hash=hash_token(raw)).first()
            ok = rec is not None and (rec.scope or "full") in ("mcp", "full")
            db.session.remove()
            return ok
    except Exception as exc:  # noqa: BLE001 — fail closed on any lookup error
        print(f"edibl-mcp: key check failed: {exc}", file=sys.stderr)
        return False


def _mcp_key_exists() -> bool:
    """True if the owner has minted an explicit `mcp`-scoped key — one signal that
    MCP access should be gated. A general `full` key (e.g. the auto integration key)
    does NOT flip this on, to avoid silently locking a previously-open endpoint.
    Raises on a DB error so the caller can fail closed."""
    app = _get_app()
    from app.extensions import db
    from app.models import ApiToken
    with app.app_context():
        exists = db.session.query(ApiToken.id).filter_by(scope="mcp").first() is not None
        db.session.remove()
        return exists


def _auth_required(server_token: str) -> bool:
    """Whether the MCP endpoint requires authentication. Required when: a legacy
    server token is set, the app runs in hardened mode (`DISABLE_AUTH` off — so a
    hardened app means a hardened MCP), or an `mcp`-scoped key has been minted.
    Fails CLOSED — any error resolves to *required* (unlike the key check, here
    returning False would serve the request unauthenticated)."""
    if server_token:
        return True
    try:
        app = _get_app()
        if not app.config.get("DISABLE_AUTH", False):
            return True  # hardened app ⇒ hardened MCP
        return _mcp_key_exists()
    except Exception as exc:  # noqa: BLE001 — never fail open
        print(f"edibl-mcp: auth-required check failed, requiring auth: {exc}", file=sys.stderr)
        return True


def _guard(asgi_app, server_token: str):
    """ASGI gate. See _auth_required for when auth is enforced; otherwise the
    endpoint is open (zero-config)."""
    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            header = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            if _auth_required(server_token) and not _authorized(header, server_token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await asgi_app(scope, receive, send)

    return wrapper


def _authorized(header_value: str, server_token: str) -> bool:
    if server_token and hmac.compare_digest(header_value, f"Bearer {server_token}"):
        return True
    if header_value.startswith("Bearer "):
        return _key_ok(header_value[len("Bearer "):].strip())
    return False


if __name__ == "__main__":
    host = os.environ.get("EDIBL_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("EDIBL_MCP_PORT", "7767"))
    server_token = os.environ.get("EDIBL_MCP_SERVER_TOKEN", "")
    # Always wrap: the guard itself decides per-request whether auth is required, so
    # minting an MCP key later gates the endpoint without a restart.
    app = _guard(mcp.sse_app(), server_token)
    if not server_token:
        print("edibl-mcp: no EDIBL_MCP_SERVER_TOKEN — MCP is open until you mint an "
              "'mcp'-scoped key in Settings → Access & keys.", file=sys.stderr)
    import uvicorn
    uvicorn.run(app, host=host, port=port)
