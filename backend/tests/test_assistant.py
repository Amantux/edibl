"""The chat assistant now requires an LLM. These tests drive the provider tool
loop with a mocked HTTP client to prove a tool call (e.g. add_stock) executes."""
import httpx

from app.extensions import db
from app.models import Group, StockLot
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
