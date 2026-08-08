"""Low-confidence consumption asks for confirmation instead of guessing.

Consuming is destructive and not recoverable by re-reading a number, so an
ambiguous name must never pick for the user. The chat surface had NO gate at all
(raw substring -> lots[0], so "use the milk" ate whichever expired first); REST
required an exact name or 404'd. Both now run the shared tiered resolution:
HIGH acts, LOW returns ranked candidates + a preview of what each choice would
consume + a reason they differ, and changes nothing.

The LLM only ever explains and ranks on a destructive path — it can never turn a
LOW match into an action.
"""
from app.extensions import db
from app.models import Group


def _gid(app):
    with app.app_context():
        return db.session.query(Group).first().id


def _stock(c, name, quantity=5, **kw):
    body = {"name": name, "quantity": quantity, "unit": "count",
            "category": "dairy", **kw}
    return c.post("/api/v1/stock", json=body).get_json()


def _qty(c, lot_id):
    return c.get(f"/api/v1/stock/{lot_id}").get_json()["quantity"]


def _two_milks(c):
    return (_stock(c, "Almond Milk", 5, category="dairy-alt"),
            _stock(c, "Coconut Milk", 5, category="dairy-alt"))


# ---- chat: the live defect --------------------------------------------------

def test_chat_ambiguous_consume_changes_nothing(auth_client, app):
    """Was: silently consumed lots[0]. Now: asks."""
    from app.services.assistant import h_record_consumption
    a, c = _two_milks(auth_client)
    gid = _gid(app)

    with app.app_context():
        out = h_record_consumption(gid, "milk", quantity=1)
        db.session.commit()

    msg = out if isinstance(out, str) else out[0]
    assert _qty(auth_client, a["id"]) == 5, "chat consumed an ambiguous match"
    assert _qty(auth_client, c["id"]) == 5, "chat consumed an ambiguous match"
    assert "Almond Milk" in msg and "Coconut Milk" in msg, msg


def test_chat_unambiguous_consume_still_acts(auth_client, app):
    from app.services.assistant import h_record_consumption
    lot = _stock(auth_client, "Butter", 4)
    gid = _gid(app)

    with app.app_context():
        h_record_consumption(gid, "butter", quantity=1)
        db.session.commit()

    assert _qty(auth_client, lot["id"]) == 3


def test_chat_exact_name_after_asking_acts(auth_client, app):
    """The confirm round-trip: user answers, model re-calls with the exact name."""
    from app.services.assistant import h_record_consumption
    a, c = _two_milks(auth_client)
    gid = _gid(app)

    with app.app_context():
        h_record_consumption(gid, "milk", quantity=1)          # asks
        h_record_consumption(gid, "Almond Milk", quantity=1)   # confirms
        db.session.commit()

    assert _qty(auth_client, a["id"]) == 4, "exact name did not act"
    assert _qty(auth_client, c["id"]) == 5


# ---- REST -------------------------------------------------------------------

def test_rest_low_confidence_returns_confirmation_and_changes_nothing(auth_client):
    a, c = _two_milks(auth_client)

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "milk", "quantity": 2})

    assert r.status_code == 409, r.get_data(as_text=True)
    body = r.get_json()
    assert body["status"] == "needs_confirmation"
    names = {cand["name"] for cand in body["candidates"]}
    assert names == {"Almond Milk", "Coconut Milk"}
    assert body["reasoning"], "no reasoning offered"
    assert _qty(auth_client, a["id"]) == 5 and _qty(auth_client, c["id"]) == 5


def test_rest_partial_name_resolves_and_acts(auth_client):
    """'butter' with only one butter is HIGH — it acts (used to 404)."""
    lot = _stock(auth_client, "Salted Butter", 4)

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "butter", "quantity": 1})

    assert r.status_code == 200, r.get_data(as_text=True)
    assert _qty(auth_client, lot["id"]) == 3


def test_rest_confirming_with_product_id_acts(auth_client):
    a, _c = _two_milks(auth_client)
    body = auth_client.post("/api/v1/stock/consume",
                            json={"name": "milk", "quantity": 2}).get_json()
    chosen = next(x for x in body["candidates"] if x["name"] == "Almond Milk")

    r = auth_client.post("/api/v1/stock/consume",
                         json={"productId": chosen["productId"], "quantity": 2})

    assert r.status_code == 200
    assert _qty(auth_client, a["id"]) == 3


def test_preview_matches_what_confirming_actually_consumes(auth_client):
    """A preview that lies is worse than none."""
    a, _c = _two_milks(auth_client)
    body = auth_client.post("/api/v1/stock/consume",
                            json={"name": "milk", "quantity": 2}).get_json()
    prev = next(p for p in body["preview"] if p["name"] == "Almond Milk")

    res = auth_client.post("/api/v1/stock/consume",
                           json={"productId": prev["productId"],
                                 "quantity": 2}).get_json()

    assert prev["wouldConsume"] == res["consumed"]
    assert prev["shortfall"] == res["shortfall"]
    assert _qty(auth_client, a["id"]) == 3


# ---- the safety property ----------------------------------------------------

def _stub_confident_llm(monkeypatch):
    """An LLM that is sure it knows which product is meant.

    It picks a REAL productId out of the payload it is given — a stub that
    returned a name would be silently discarded by the id validation, making the
    safety test below vacuous (it was, until a mutation run caught it).
    """
    import json

    import app.services.assistant as asst

    monkeypatch.setattr(asst, "_cfg", lambda gid=None: {
        "provider": "anthropic", "base_url": "", "api_key": "k",
        "model": "m", "timeout": 5, "max_steps": 6, "agent_id": ""})

    def _complete(cfg, system, user):
        cands = json.loads(user)["candidates"]
        almond = next(c for c in cands if c["name"] == "Almond Milk")
        return json.dumps({"reasoning": "They clearly meant the almond one.",
                           "order": [c["productId"] for c in cands],
                           "pick": almond["productId"]})

    monkeypatch.setattr(asst, "_complete", _complete)


def test_a_confident_llm_still_cannot_consume_without_confirmation(
        auth_client, monkeypatch):
    """THE safety test: reasoning may explain, never act, on a destructive path."""
    _stub_confident_llm(monkeypatch)
    a, c = _two_milks(auth_client)

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "milk", "quantity": 2, "explain": True})

    assert r.status_code == 409, "LLM auto-resolved a destructive action"
    assert _qty(auth_client, a["id"]) == 5 and _qty(auth_client, c["id"]) == 5


def test_llm_reasoning_is_used_when_available(auth_client, monkeypatch):
    _stub_confident_llm(monkeypatch)
    _two_milks(auth_client)

    body = auth_client.post("/api/v1/stock/consume",
                            json={"name": "milk", "quantity": 2,
                                  "explain": True}).get_json()

    assert "almond one" in body["reasoning"].lower(), body["reasoning"]


def test_workflow_works_with_no_llm_configured(auth_client):
    """Default test config has no provider — deterministic reasoning."""
    _two_milks(auth_client)

    body = auth_client.post("/api/v1/stock/consume",
                            json={"name": "milk", "quantity": 2}).get_json()

    assert body["status"] == "needs_confirmation"
    assert "Almond Milk" in body["reasoning"] and "Coconut Milk" in body["reasoning"]


def test_llm_failure_still_refuses_cleanly(auth_client, monkeypatch):
    import app.services.assistant as asst

    def _boom(cfg, system, user):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(asst, "_cfg", lambda gid=None: {
        "provider": "anthropic", "base_url": "", "api_key": "k",
        "model": "m", "timeout": 5, "max_steps": 6, "agent_id": ""})
    monkeypatch.setattr(asst, "_complete", _boom)
    a, _c = _two_milks(auth_client)

    r = auth_client.post("/api/v1/stock/consume",
                         json={"name": "milk", "quantity": 2, "explain": True})

    assert r.status_code == 409, "an LLM failure broke the refusal path"
    assert r.get_json()["reasoning"], "no deterministic fallback reasoning"
    assert _qty(auth_client, a["id"]) == 5


# ---- /stock/resolve?explain=1 ----------------------------------------------

def test_resolve_explain_adds_reasoning_without_changing_confidence(auth_client):
    """Reasoning is advisory TEXT. It must never promote low -> high: every
    mutating MCP tool acts on 'high', so that would auto-resolve a destructive
    action through the back door."""
    _two_milks(auth_client)

    plain = auth_client.get("/api/v1/stock/resolve?q=milk").get_json()
    explained = auth_client.get("/api/v1/stock/resolve?q=milk&explain=1").get_json()

    assert plain["confidence"] == "low" and "reasoning" not in plain
    assert explained["confidence"] == "low", "explain changed the confidence"
    assert explained["reasoning"]
    assert len(explained["candidates"]) == len(plain["candidates"])


def test_resolve_explain_cannot_autoresolve_even_with_a_confident_llm(
        auth_client, monkeypatch):
    _stub_confident_llm(monkeypatch)
    _two_milks(auth_client)

    body = auth_client.get("/api/v1/stock/resolve?q=milk&explain=1").get_json()

    assert body["confidence"] == "low", "LLM promoted a low match to high"
    assert "lot" not in body, "LLM handed back an actionable lot"


# ---- cook: ambiguous is distinguishable from missing ------------------------

def test_cook_reports_ambiguity_separately_from_missing(auth_client, app):
    from app.api.integrations import cook_ingredients
    _two_milks(auth_client)
    gid = _gid(app)

    with app.app_context():
        res = cook_ingredients(gid, [{"name": "milk", "quantity": 1},
                                     {"name": "plutonium", "quantity": 1}])

    amb = next(r for r in res if r["name"] == "milk")
    missing = next(r for r in res if r["name"] == "plutonium")
    assert amb["status"] == "needs_confirmation"
    assert {c["name"] for c in amb["candidates"]} == {"Almond Milk", "Coconut Milk"}
    assert amb["consumed"] == 0
    assert missing["status"] == "unmatched" and "candidates" not in missing
