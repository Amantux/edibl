"""Confidence-tiered stock resolution.

Resolving a spoken name to food is a guess, and acting on a wrong guess throws
away the wrong carton. These tests pin the policy:

* a confident match resolves to ONE lot and the caller acts;
* an ambiguous one returns ranked candidates and the caller changes NOTHING;
* ranking uses brand, category and description as well as the name, and says
  which of them matched;
* several lots of the SAME product is a queue, not a question — FEFO still picks
  the soonest-to-expire without asking.

The MCP tests drive the real tool functions against the real Flask app, so a
tool that acts on a low-confidence match fails here rather than in a kitchen.
"""
import httpx
import pytest

import edibl_mcp
from app.extensions import db
from app.models import Group, Product
from app.services import matching


def _fn(tool):
    """The plain function behind an @mcp.tool()-decorated object."""
    return getattr(tool, "fn", tool)


@pytest.fixture()
def mcp_api(app, monkeypatch):
    """Point the MCP server's HTTP client at the test app, authenticated."""
    client = app.test_client()
    client.post("/api/v1/users/register",
                json={"email": "r@t.com", "password": "password", "name": "R"})
    token = client.post("/api/v1/users/login",
                        json={"email": "r@t.com", "password": "password"}).get_json()["token"]
    http = httpx.Client(transport=httpx.WSGITransport(app=app),
                        base_url="http://testserver/api/v1",
                        headers={"Authorization": token}, timeout=15)
    monkeypatch.setattr(edibl_mcp, "_HTTP", http)
    client.environ_base["HTTP_AUTHORIZATION"] = token
    yield client
    http.close()


def _add(client, name, **kw):
    body = {"productName": name, "quantity": kw.pop("quantity", 1),
            "unit": kw.pop("unit", "count")}
    body.update(kw)
    return client.post("/api/v1/stock", json=body).get_json()


def _set_product(client, name, **fields):
    """Set brand/category/description on the product behind a stock name."""
    items = client.get("/api/v1/stock").get_json()["items"]
    pid = next(i["product"]["id"] for i in items if i["product"]["name"] == name)
    client.put(f"/api/v1/products/{pid}", json=fields)
    return pid


# --- scoring: brand/category/description count, and we know which matched ---

def _group_with(app, products):
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        for kw in products:
            db.session.add(Product(group_id=g.id, **kw))
        db.session.commit()
        return g.id


def test_exact_name_outranks_brand_which_outranks_description(app):
    gid = _group_with(app, [
        {"name": "Zest", "search_text": "a citrus zest"},           # description
        {"name": "Aaa", "brand": "Zest"},                          # brand
        {"name": "Zest Deluxe"},                                   # substring
    ])
    with app.app_context():
        ranked = matching.match_products(gid, "zest")
        by_name = {c.product.name: c for c in ranked}
        # exact name > substring > brand: a stronger KIND of evidence always wins.
        assert by_name["Zest"].score > by_name["Zest Deluxe"].score
        assert by_name["Zest Deluxe"].score > by_name["Aaa"].score
        assert [c.product.name for c in ranked][0] == "Zest"


def test_a_description_only_match_is_found_and_explained(app):
    gid = _group_with(app, [{"name": "Stock base", "search_text": "rich creamy broth"}])
    with app.app_context():
        ranked = matching.match_products(gid, "creamy")
        assert [c.product.name for c in ranked] == ["Stock base"]
        assert ranked[0].reasons == ["description"]


def test_a_brand_only_match_is_found_and_explained(app):
    gid = _group_with(app, [{"name": "Almond milk", "brand": "Califia"}])
    with app.app_context():
        ranked = matching.match_products(gid, "califia")
        assert ranked[0].product.name == "Almond milk"
        assert ranked[0].reasons == ["brand"]


def test_evidence_kind_beats_evidence_count(app):
    """One name match outranks a pile of weak brand/category/description hits."""
    gid = _group_with(app, [
        {"name": "Pasta bake"},                                     # prefix
        {"name": "A", "category": "pasta"},
        {"name": "B", "search_text": "pasta night"},
        {"name": "C", "brand": "Pasta Co"},
    ])
    with app.app_context():
        ranked = matching.match_products(gid, "pasta")
        assert ranked[0].product.name == "Pasta bake"
        res = matching.resolve_for_mutation(gid, "pasta")
        assert res.product is not None and res.product.name == "Pasta bake"


def test_weak_evidence_alone_never_picks_between_products(app):
    """Three descriptions mentioning "creamy" is a question, not an answer."""
    gid = _group_with(app, [
        {"name": "A", "search_text": "creamy sauce"},
        {"name": "B", "search_text": "creamy soup"},
    ])
    with app.app_context():
        res = matching.resolve_for_mutation(gid, "creamy")
        assert res.ambiguous is True and res.product is None


def test_weak_noise_does_not_block_a_clear_name_match(app):
    """A description hit is ranking colour, not a rival interpretation."""
    gid = _group_with(app, [
        {"name": "Whole milk"},
        {"name": "Cookies", "search_text": "best with milk"},
    ])
    with app.app_context():
        res = matching.resolve_for_mutation(gid, "whole milk")
        assert res.product is not None and res.product.name == "Whole milk"


# --- the endpoint: high / low / none ---------------------------------------

def test_resolve_high_for_a_unique_name(auth_client):
    _add(auth_client, "Butter")
    r = auth_client.get("/api/v1/stock/resolve?q=butter").get_json()
    assert r["confidence"] == "high"
    assert r["lot"]["product"]["name"] == "Butter"


def test_resolve_high_for_a_lot_id(auth_client):
    lot = _add(auth_client, "Butter")
    r = auth_client.get(f"/api/v1/stock/resolve?q={lot['id']}").get_json()
    assert r["confidence"] == "high" and r["matchedOn"] == ["id"]
    assert r["lot"]["id"] == lot["id"]


def test_resolve_low_returns_candidates_with_brand_category_location(auth_client):
    _add(auth_client, "Almond milk")
    _add(auth_client, "Oat milk")
    _set_product(auth_client, "Almond milk", brand="Califia", category="dairy-alt")
    r = auth_client.get("/api/v1/stock/resolve?q=milk").get_json()
    assert r["confidence"] == "low"
    assert 2 <= len(r["candidates"]) <= 5
    almond = next(c for c in r["candidates"] if c["name"] == "Almond milk")
    assert almond["brand"] == "Califia" and almond["category"] == "dairy-alt"
    assert "location" in almond and "matchedOn" in almond


def test_resolve_none_when_nothing_matches(auth_client):
    _add(auth_client, "Butter")
    r = auth_client.get("/api/v1/stock/resolve?q=xyzzy").get_json()
    assert r["confidence"] == "none"


def test_resolve_is_not_swallowed_by_the_lot_id_route(auth_client):
    """"resolve" must reach the resolver, never be read as a lot id."""
    _add(auth_client, "Butter")
    assert auth_client.get("/api/v1/stock/resolve?q=butter").status_code == 200


# --- the domain rule: lots queue, products disambiguate --------------------

def test_many_lots_of_one_product_resolve_high_and_pick_soonest_expiry(auth_client):
    """Two cartons of the same milk is FEFO, not a question."""
    _add(auth_client, "Whole milk", expiryDate="2030-01-20")
    early = _add(auth_client, "Whole milk", expiryDate="2030-01-02")
    r = auth_client.get("/api/v1/stock/resolve?q=whole milk").get_json()
    assert r["confidence"] == "high"
    assert r["lot"]["id"] == early["id"], "must pick the soonest-to-expire lot"


def test_distinct_products_sharing_a_word_are_low(auth_client):
    _add(auth_client, "Almond milk")
    _add(auth_client, "Coconut milk")
    r = auth_client.get("/api/v1/stock/resolve?q=milk").get_json()
    assert r["confidence"] == "low"
    assert {c["name"] for c in r["candidates"]} == {"Almond milk", "Coconut milk"}


# --- MCP tools: act when confident, ask when not ---------------------------

def test_mcp_acts_on_a_confident_match(mcp_api):
    _add(mcp_api, "Butter", quantity=2)
    out = _fn(edibl_mcp.adjust_stock)("butter", 5)
    assert "Corrected Butter" in out
    items = mcp_api.get("/api/v1/stock").get_json()["items"]
    assert items[0]["quantity"] == 5


def test_mcp_delete_refuses_a_low_match_and_changes_nothing(mcp_api):
    _add(mcp_api, "Almond milk")
    _add(mcp_api, "Coconut milk")
    out = _fn(edibl_mcp.delete_stock)("milk")
    assert "matches several things" in out
    assert "Almond milk" in out and "Coconut milk" in out
    items = mcp_api.get("/api/v1/stock").get_json()["items"]
    assert len(items) == 2, "nothing may be deleted on an ambiguous name"


def test_mcp_consumption_refuses_a_low_match(mcp_api):
    _add(mcp_api, "Almond milk", quantity=4)
    _add(mcp_api, "Coconut milk", quantity=4)
    out = _fn(edibl_mcp.record_consumption)("milk", 1)
    assert "matches several things" in out
    items = mcp_api.get("/api/v1/stock").get_json()["items"]
    assert all(i["quantity"] == 4 for i in items), "nothing consumed"


def test_mcp_use_stock_refuses_a_low_match(mcp_api):
    _add(mcp_api, "Almond milk", quantity=4)
    _add(mcp_api, "Coconut milk", quantity=4)
    out = _fn(edibl_mcp.use_stock)("milk", 1)
    assert "matches several things" in out
    items = mcp_api.get("/api/v1/stock").get_json()["items"]
    assert all(i["quantity"] == 4 for i in items)


def test_mcp_use_stock_works_on_a_partial_but_unambiguous_name(mcp_api):
    """Resolution makes partial names work — the old exact-name lookup 404'd."""
    _add(mcp_api, "Whole milk", quantity=4)
    out = _fn(edibl_mcp.use_stock)("whole", 1)
    assert "Used 1" in out and "Whole milk" in out


def test_mcp_clarification_shows_brand_and_location_so_a_user_can_choose(mcp_api):
    _add(mcp_api, "Almond milk")
    _add(mcp_api, "Coconut milk")
    _set_product(mcp_api, "Almond milk", brand="Califia", category="dairy-alt")
    out = _fn(edibl_mcp.move_stock)("milk", "Fridge")
    assert "Califia" in out and "dairy-alt" in out


def test_mcp_acts_when_given_the_exact_name_of_one_of_several(mcp_api):
    _add(mcp_api, "Almond milk", quantity=2)
    _add(mcp_api, "Coconut milk", quantity=2)
    out = _fn(edibl_mcp.adjust_stock)("Almond milk", 7)
    assert "Corrected Almond milk" in out
    items = mcp_api.get("/api/v1/stock").get_json()["items"]
    almond = next(i for i in items if i["product"]["name"] == "Almond milk")
    coconut = next(i for i in items if i["product"]["name"] == "Coconut milk")
    assert almond["quantity"] == 7 and coconut["quantity"] == 2


def test_mcp_many_lots_of_one_product_still_acts(mcp_api):
    """The domain rule end-to-end: same product, many lots, no nagging."""
    _add(mcp_api, "Whole milk", quantity=1, expiryDate="2030-01-20")
    _add(mcp_api, "Whole milk", quantity=1, expiryDate="2030-01-02")
    out = _fn(edibl_mcp.freeze_stock)("whole milk")
    assert "Froze Whole milk" in out
