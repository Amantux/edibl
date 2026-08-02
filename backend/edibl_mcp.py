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
import json as _json
import sys

import httpx
from mcp.server.fastmcp import FastMCP

from app.settings import load_settings

# Resolved through the one registry, not raw os.environ: this is a separate
# process, and the entrypoint no longer translates options.json into the
# environment. Reading env directly is also how this file grew its own boolean
# parser for MCP_EXPOSE_EXTERNAL, a third implementation of the same rule.
_SETTINGS = load_settings()

API = _SETTINGS.mcp_api
TOKEN = _SETTINGS.MCP_API_TOKEN or None
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


def _resolve_lot(name):
    """``(lot, candidates)`` — resolve a name/id to one lot, never guessing.

    Delegates to ``/stock/resolve``, which ranks a household's PRODUCTS by name,
    aliases, family, brand, category and description and returns a confidence.
    A confident hit comes back as ``(lot, [])`` — that product's
    soonest-to-expire lot, so FEFO is unchanged. Anything ambiguous comes back as
    ``(None, candidates)`` so the caller asks which food was meant instead of
    acting on a coin-flip.

    Ambiguity is judged across PRODUCTS, not lots: two cartons of the same milk
    is a queue, not a question — only "Milk" vs "Almond Milk" is.
    """
    try:
        data = _get("/stock/resolve", {"q": name})
    except httpx.HTTPStatusError:
        return None, []
    if data.get("confidence") == "high":
        return data.get("lot"), []
    return None, data.get("candidates") or []


def _describe_candidate(c):
    """One candidate as a line a user can actually choose between."""
    bits = [str(c.get("name") or "")]
    detail = [c.get("brand") or "", c.get("category") or ""]
    where = c.get("location")
    if where:
        detail.append(f"in {where}")
    detail = [d for d in detail if d]
    if detail:
        bits.append(f"({', '.join(detail)})")
    if (c.get("lots") or 0) > 1:
        bits.append(f"{c['lots']} lots")
    if c.get("nextExpiry"):
        bits.append(f"next expiry {str(c['nextExpiry'])[:10]}")
    reasons = [r for r in (c.get("matchedOn") or []) if r and r != "exact name"]
    if reasons:
        bits.append(f"matched on {', '.join(reasons)}")
    return " ".join(bits)


def _clarify(name, candidates):
    """Ask which food was meant. Returned INSTEAD of acting, so nothing changes."""
    if not candidates:
        return f"No stock matching '{name}'."
    listed = "; ".join(_describe_candidate(c) for c in candidates)
    return (f"'{name}' matches several things: {listed}. Nothing was changed — "
            "show these to the user, ask which they mean, then call again with "
            "that product's exact name.")


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
    source, notes).
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
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
    record_consumption instead to log that it was eaten/spoiled).
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
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
    from the soonest-to-expire matching lot.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
    res = _post(f"/stock/{lot['id']}/consume", {"quantity": quantity, "outcome": outcome})
    msg = f"Recorded {quantity} {lot['unit']} of {lot['product']['name']} ({outcome})."
    if res.get("insight"):
        msg += " " + res["insight"]
    return msg


@mcp.tool()
def open_stock(name: str) -> str:
    """Mark a package opened (e.g. an opened carton) — an orthogonal facet, separate
    from using it up. Affects freshness/shelf-life, not quantity.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
    res = _post(f"/stock/{lot['id']}/open", {})
    return res.get("summary", f"Opened {lot['product']['name']}.")


@mcp.tool()
def adjust_stock(name: str, quantity: float) -> str:
    """Correct a lot to a measured amount (e.g. an estimated bin you just weighed).
    Sets the exact quantity on the soonest-to-expire matching lot; reversible.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
    _post(f"/stock/{lot['id']}/adjust", {"quantity": quantity, "quantityKind": "exact"})
    return f"Corrected {lot['product']['name']} to {quantity} {lot['unit']}."


@mcp.tool()
def move_stock(name: str, location: str) -> str:
    """Move the soonest-to-expire lot matching `name` to another location.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
    loc_id = _location_id(location)
    if not loc_id:
        return f"No location named '{location}'. Add it first, or use an existing one."
    _post(f"/stock/{lot['id']}/move", {"locationId": loc_id})
    return f"Moved {lot['product']['name']} to {location}."


@mcp.tool()
def split_stock(name: str, quantity: float, location: str = "") -> str:
    """Split `quantity` off the soonest-to-expire lot matching `name` into a new
    position (e.g. portioning). Conserves the total; reversible.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
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
    way to 'use the milk' without picking a specific lot.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    # Resolve to ONE product first (so a partial name works and an ambiguous one
    # asks), then let the server draw across that product's lots by policy.
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
    product = lot.get("product") or {}
    try:
        res = _post("/stock/consume", {"productId": product.get("id"),
                                       "quantity": quantity, "outcome": outcome})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"No stock matching '{name}'."
        raise
    if res.get("consumed", 0) == 0:
        return f"No stock matching '{name}'."
    label = product.get("name") or name
    msg = f"Used {res['consumed']} of {label} across {len(res.get('draws', []))} lot(s)."
    if res.get("shortfall"):
        msg += f" Short by {res['shortfall']}."
    return msg


@mcp.tool()
def freeze_stock(name: str) -> str:
    """Freeze the soonest-to-expire lot matching `name` — extends its shelf life and
    records the freeze date. Reversible.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
    _post(f"/stock/{lot['id']}/freeze", {})
    return f"Froze {lot['product']['name']}."


@mcp.tool()
def thaw_stock(name: str) -> str:
    """Thaw the soonest-to-expire frozen lot matching `name` — shortens shelf life
    and records the thaw date. Reversible.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    lot, candidates = _resolve_lot(name)
    if not lot:
        return _clarify(name, candidates)
    _post(f"/stock/{lot['id']}/thaw", {})
    return f"Thawed {lot['product']['name']}."


@mcp.tool()
def make_from(source_name: str, source_quantity: float, product_name: str,
              product_quantity: float = 1, product_unit: str = "portion",
              category: str = "other") -> str:
    """Turn stock into other stock, preserving lineage (e.g. 'made chicken stock
    from the carcass', 'cooked 2 lb chicken into 4 servings'). Consumes
    `source_quantity` of `source_name` and creates the product.
    If the name matches several different products this asks which you meant and changes NOTHING — show the options, then call again with the exact name.
    """
    src, candidates = _resolve_lot(source_name)
    if not src:
        return _clarify(source_name, candidates)
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


def _usable_mcp_key_exists() -> bool:
    """True if ANY key that `_key_ok` would accept exists — scope `mcp` OR `full`.
    Used by refuse-to-serve: the question there is "is there a usable credential to
    let a client in?", so a Full key counts (it authenticates every request), matching
    the docs' "mint an MCP or Full key". (`_auth_required` uses the stricter mcp-only
    `_mcp_key_exists` — minting a Full key must not silently lock an open endpoint.)
    Raises on a DB error so the caller can fail closed."""
    app = _get_app()
    from app.extensions import db
    from app.models import ApiToken
    with app.app_context():
        exists = (db.session.query(ApiToken.id)
                  .filter(ApiToken.scope.in_(("mcp", "full"))).first() is not None)
        db.session.remove()
        return exists


def _expose_external() -> bool:
    """Operator opted the MCP port out of Home Assistant (`mcp_expose_external`).
    When on, auth is mandatory regardless of mode and the server refuses to bind
    without a usable key (see `_should_refuse_to_serve`).

    Resolved per call, not captured at import: this gates whether the endpoint
    demands a key, so it must reflect the configuration as it is now. Resolution
    is a small JSON read plus a field loop — at household request volumes that is
    not worth caching, and caching it is exactly what made the value untestable.
    """
    return bool(load_settings().MCP_EXPOSE_EXTERNAL)


def _should_refuse_to_serve() -> bool:
    """When the port is exposed outside HA, refuse to bind unless a usable key
    (scope `mcp` or `full`) exists to authenticate external clients — never open a
    WAN-reachable MCP with no credential set. Fails CLOSED (any DB error → refuse).
    A no-op when `mcp_expose_external` is off."""
    if not _expose_external():
        return False
    try:
        return not _usable_mcp_key_exists()
    except Exception as exc:  # noqa: BLE001 — can't verify keys ⇒ don't serve
        print(f"edibl-mcp: could not verify MCP keys, refusing to serve: {exc}",
              file=sys.stderr)
        return True


def _auth_required(server_token: str) -> bool:
    """Whether the MCP endpoint requires authentication. Required when: the port is
    exposed outside HA (`mcp_expose_external`), a legacy server token is set, the app
    runs in hardened mode (`DISABLE_AUTH` off — so a hardened app means a hardened
    MCP), or an `mcp`-scoped key has been minted. Fails CLOSED — any error resolves
    to *required* (unlike the key check, here returning False would serve the request
    unauthenticated)."""
    if _expose_external():
        return True
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


# Read-only allowlist: the MCP tools a `read`-access key may invoke. Anything NOT
# listed (mutating tools + any future/unknown tool) is refused to a read-only key —
# fail-safe by default. Keep in sync with the @mcp.tool() registrations above.
READ_TOOLS = frozenset({
    "do_i_have", "whats_in_stock", "expiring_soon", "runout_forecast",
    "freezer_inventory", "wine_cellar", "check_recipe", "plan_status",
    "grouped_stock", "food_insights", "shopping_list", "search_products",
    "reorder_suggestions",
})


def _key_access(raw: str) -> str:
    """The access class ('write' | 'read') for a valid MCP key, else '' if the key
    is unknown/invalid. Fails safe to 'read' on a DB error (deny writes, never open)."""
    if not raw:
        return ""
    try:
        app = _get_app()
        from app.extensions import db
        from app.models import ApiToken, hash_token
        with app.app_context():
            rec = db.session.query(ApiToken).filter_by(token_hash=hash_token(raw)).first()
            if rec is None or (rec.scope or "full") not in ("mcp", "full"):
                db.session.remove()
                return ""
            acc = (rec.access or "write")
            db.session.remove()
            return acc
    except Exception as exc:  # noqa: BLE001 — a broken lookup must not grant write
        print(f"edibl-mcp: access check failed, treating key as read-only: {exc}",
              file=sys.stderr)
        return "read"


def _header_access(header_value: str, server_token: str) -> str:
    """Access class for the presented credential. The static server token is full
    write; a Bearer API key resolves via _key_access; anything else is ''."""
    if server_token and hmac.compare_digest(header_value, f"Bearer {server_token}"):
        return "write"
    if header_value.startswith("Bearer "):
        return _key_access(header_value[len("Bearer "):].strip())
    return ""


async def _drain_body(receive):
    """Buffer the full ASGI request body and return (body_bytes, replay_receive) so
    the body can be inspected and then passed through to the downstream app intact."""
    chunks, more = [], True
    while more:
        msg = await receive()
        if msg["type"] == "http.request":
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        else:  # http.disconnect
            break
    body = b"".join(chunks)
    sent = False

    async def replay():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return body, replay


def _body_has_write_toolcall(body: bytes) -> bool:
    """True if the JSON-RPC body invokes a tool NOT on the READ_TOOLS allowlist.
    Handles a single message or a batch; an unparseable body is left to the
    downstream server to reject (not our decision)."""
    try:
        msg = _json.loads(body or b"{}")
    except Exception:  # noqa: BLE001
        return False
    items = msg if isinstance(msg, list) else [msg]
    for it in items:
        if isinstance(it, dict) and it.get("method") == "tools/call":
            name = (it.get("params") or {}).get("name")
            if name not in READ_TOOLS:
                return True
    return False


def _guard(asgi_app, server_token: str):
    """ASGI gate. Enforces authentication (see _auth_required) and, for a read-only
    key, refuses MCP write-tools — a `read` key may only invoke READ_TOOLS."""
    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            header = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            if _auth_required(server_token) and not _authorized(header, server_token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
            # A read-only key may only call read tools. Inspect the tools/call body;
            # write keys and non-POST requests pass through untouched.
            if scope.get("method") == "POST" and _header_access(header, server_token) == "read":
                body, receive = await _drain_body(receive)
                if _body_has_write_toolcall(body):
                    payload = b'{"error":"this API key is read-only"}'
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": payload})
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
    # Configure logging explicitly here: this process builds its Flask app
    # LAZILY (only when a key lookup needs the DB), so waiting for create_app
    # meant the sidecar produced no log file at all — its own startup and, from
    # here on, its tool-call audit trail went only to stderr.
    from app.logging_setup import configure as _configure_logging
    _configure_logging(_SETTINGS, process="mcp")

    host = _SETTINGS.MCP_HOST
    port = _SETTINGS.MCP_PORT
    server_token = _SETTINGS.MCP_SERVER_TOKEN
    # Always wrap: the guard itself decides per-request whether auth is required, so
    # minting an MCP key later gates the endpoint without a restart.
    # Fail closed: an externally-exposed endpoint must have a mintable client key.
    if _should_refuse_to_serve():
        print("edibl-mcp: mcp_expose_external is ON but no 'mcp'-scoped API key "
              "exists — mint one in Settings → Access & keys, then restart. Refusing "
              "to serve an externally-reachable MCP with no client credential.",
              file=sys.stderr)
        sys.exit(1)
    app = _guard(mcp.sse_app(), server_token)
    if _expose_external():
        print("edibl-mcp: mcp_expose_external ON — every request must carry a Bearer "
              "MCP/Full API key. Map port 7767 in the add-on Network tab to reach it.",
              file=sys.stderr)
    elif not server_token:
        print("edibl-mcp: no EDIBL_MCP_SERVER_TOKEN — MCP is open until you mint an "
              "'mcp'-scoped key in Settings → Access & keys.", file=sys.stderr)
    import uvicorn
    uvicorn.run(app, host=host, port=port)
