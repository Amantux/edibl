"""The chat assistant now requires an LLM. These tests drive the provider tool
loop with a mocked HTTP client to prove a tool call (e.g. add_stock) executes."""
import httpx

from app.extensions import db
from app.models import Group, StockLot, ConsumptionEvent
from app.services import assistant


class _FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _FakeClient:
    """Replays a scripted list of JSON responses for successive .post() calls."""

    def __init__(self, script):
        self._script = script
        self._i = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        resp = self._script[self._i]
        self._i += 1
        return _FakeResp(resp)


def _configure(app, provider="ollama"):
    app.config["LLM_PROVIDER"] = provider
    app.config["LLM_BASE_URL"] = "http://llm.test"
    app.config["LLM_API_KEY"] = "k"
    app.config["LLM_MODEL"] = "test-model"
    app.config["LLM_TIMEOUT"] = 5
    app.config["LLM_MAX_STEPS"] = 4


def test_config_reports_enabled_when_provider_set(app):
    _configure(app)
    with app.app_context():
        cfg = assistant.config_public()
    assert cfg["enabled"] is True and cfg["provider"] == "ollama"
    assert cfg["model"] == "test-model"


def test_ollama_tool_loop_executes_add_stock(app, monkeypatch):
    _configure(app, "ollama")
    script = [
        # round 1: the model asks to call add_stock
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "add_stock", "arguments": {
                "name": "Kiwi", "quantity": 3, "unit": "each", "category": "produce"}}}]}},
        # round 2: final answer
        {"message": {"role": "assistant", "content": "Added 3 kiwis."}},
    ]
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(script))

    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        result = assistant.run_chat(g.id, [{"role": "user", "content": "add 3 kiwis"}])
        lots = db.session.query(StockLot).filter_by(group_id=g.id).all()
        summary = [(lot.product.name, lot.quantity) for lot in lots]

    assert result["reply"] == "Added 3 kiwis."
    assert result["provider"] == "ollama" and result["enabled"] is True
    assert any(a["tool"] == "add_stock" for a in result["actions"])
    assert summary == [("Kiwi", 3)]


def test_provider_error_does_not_500(app, monkeypatch):
    _configure(app, "openai")

    def _boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", _boom)
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        result = assistant.run_chat(g.id, [{"role": "user", "content": "hi"}])
    assert result["provider"] == "openai:error" and "unreachable" in result["reply"]


# --- receipt / order extraction ---------------------------------------------
def test_extract_items_parses_llm_json(app, monkeypatch):
    _configure(app, "ollama")
    reply = ("Sure!\n```json\n"
             '[{"name":"Organic Milk","quantity":2,"unit":"l","category":"dairy"},'
             '{"name":"Bananas","quantity":6,"unit":"count"}]\n```')
    script = [{"message": {"role": "assistant", "content": reply}}]
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(script))
    with app.app_context():
        res = assistant.extract_items("2 ORGANIC MILK 3.99\nBANANAS x6 2.10\nTOTAL 6.09")
    assert res["enabled"] is True and res["provider"] == "ollama"
    names = [i["name"] for i in res["items"]]
    assert names == ["Organic Milk", "Bananas"]
    assert res["items"][0]["quantity"] == 2.0 and res["items"][0]["category"] == "dairy"


def test_extract_without_provider(app):
    with app.app_context():
        res = assistant.extract_items("some receipt")
    assert res["enabled"] is False and res["items"] == [] and res["error"]


def test_extract_photo_via_vision(app, monkeypatch):
    _configure(app, "openai")
    app.config["LLM_MODEL"] = "gpt-4o"
    script = [{"choices": [{"message": {
        "content": '[{"name":"Bread","quantity":1,"unit":"loaf"}]'}}]}]
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(script))
    with app.app_context():
        res = assistant.extract_items(image="ZmFrZWltYWdl", media_type="image/png")
    assert res["items"] == [{"name": "Bread", "quantity": 1.0, "unit": "loaf"}]


def test_photo_extract_rejected_for_homeassistant(app):
    app.config["LLM_PROVIDER"] = "homeassistant"
    with app.app_context():
        res = assistant.extract_items(image="ZmFrZQ==")
    assert res["items"] == [] and "vision" in res["error"].lower()


def test_parse_items_handles_garbage(app):
    with app.app_context():
        assert assistant._parse_items("no json at all") == []
        assert assistant._parse_items('[{"name":"","quantity":1}]') == []  # blank name dropped


def test_extract_endpoint(auth_client, monkeypatch):
    app = auth_client.application
    app.config["LLM_PROVIDER"] = "ollama"
    app.config["LLM_BASE_URL"] = "http://x"
    app.config["LLM_MODEL"] = "m"
    script = [{"message": {"role": "assistant",
                           "content": '[{"name":"Eggs","quantity":12,"unit":"count"}]'}}]
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(script))
    r = auth_client.post("/api/v1/stock/extract", json={"text": "EGGS 12ct 4.50"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["items"] == [{"name": "Eggs", "quantity": 12.0, "unit": "count"}]


# --- Home Assistant conversation provider -----------------------------------
def test_homeassistant_enabled_but_completion_only(app):
    app.config["LLM_PROVIDER"] = "homeassistant"
    with app.app_context():
        cfg = assistant.config_public()
    assert cfg["enabled"] is True and cfg["provider"] == "homeassistant"
    assert cfg["tools"] is False  # completion-only (no Edibl tool CRUD)


def test_homeassistant_relay_chat(app, monkeypatch):
    app.config["LLM_PROVIDER"] = "homeassistant"
    app.config["LLM_API_KEY"] = "supervisor-token"
    ha_response = {"response": {"speech": {"plain": {"speech": "You have 2 litres of milk."}}}}
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient([ha_response]))
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        res = assistant.run_chat(g.id, [{"role": "user", "content": "do I have milk?"}])
    assert res["provider"] == "homeassistant" and "milk" in res["reply"].lower()


# --- surfaced + undoable tool calls -----------------------------------------
def _group(app):
    g = Group(name="H")
    db.session.add(g)
    db.session.flush()
    return g


def test_run_tool_surfaces_undoable_action(app):
    with app.app_context():
        g = _group(app)
        acts = []
        assistant._run_tool(g.id, "add_stock",
                            {"name": "Milk", "quantity": 2, "unit": "l", "category": "dairy"}, acts)
        a = acts[-1]
        assert a["undoable"] is True and a["undo"]["op"] == "delete_lot"
        assert a["label"].startswith("Added")
        msg = assistant.apply_undo(g.id, a["undo"])
        assert "Undone" in msg
        assert db.session.query(StockLot).filter_by(group_id=g.id, finished=False).count() == 0


def test_readonly_action_not_undoable(app):
    with app.app_context():
        g = _group(app)
        acts = []
        assistant._run_tool(g.id, "whats_in_stock", {}, acts)
        assert acts[-1]["undoable"] is False and acts[-1]["undo"] is None


def test_undo_update_and_consume(app):
    with app.app_context():
        g = _group(app)
        acts = []
        assistant._run_tool(g.id, "add_stock",
                            {"name": "Milk", "quantity": 4, "unit": "l", "category": "dairy"}, acts)
        assistant._run_tool(g.id, "update_stock",
                            {"name": "Milk", "quantity": 9, "freshness": "opened"}, acts)
        assistant.apply_undo(g.id, acts[-1]["undo"])
        lot = db.session.query(StockLot).filter_by(group_id=g.id).first()
        db.session.refresh(lot)
        assert lot.quantity == 4.0 and lot.state == ""
        assistant._run_tool(g.id, "record_consumption",
                            {"name": "Milk", "quantity": 1, "outcome": "eaten"}, acts)
        assert db.session.query(ConsumptionEvent).count() == 1
        assistant.apply_undo(g.id, acts[-1]["undo"])
        db.session.refresh(lot)
        assert lot.quantity == 4.0 and db.session.query(ConsumptionEvent).count() == 0


def test_undo_endpoint(auth_client):
    lot = auth_client.post("/api/v1/stock",
                           json={"productName": "Butter", "category": "dairy",
                                 "quantity": 2, "unit": "stick"}).get_json()
    r = auth_client.post("/api/v1/assistant/undo",
                         json={"undo": {"op": "delete_lot", "lotId": lot["id"]}})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert auth_client.get(f"/api/v1/stock/{lot['id']}").status_code == 404


def test_undo_endpoint_requires_op(auth_client):
    assert auth_client.post("/api/v1/assistant/undo", json={}).status_code == 422


def test_undo_endpoint_unknown_id_is_safe(auth_client):
    r = auth_client.post("/api/v1/assistant/undo",
                         json={"undo": {"op": "delete_lot", "lotId": "nope"}})
    assert r.status_code == 200 and "Nothing to undo" in r.get_json()["message"]
