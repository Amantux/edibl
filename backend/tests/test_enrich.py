"""AI web-searched searchable descriptions for products (Ollama web search).

The live web-search call is monkeypatched; with no LLM provider configured the
synthesis falls back to the top result's snippet, so tests are deterministic.
"""
from sqlalchemy import inspect

from app.extensions import db
from app.models import Product, User


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _mk(app, gid, name="Milk", search_text=""):
    with app.app_context():
        p = Product(name=name, group_id=gid, search_text=search_text)
        db.session.add(p)
        db.session.commit()
        return p.id


def _enable(app, monkeypatch, results):
    from app.services import enrich
    app.config["OLLAMA_SEARCH_KEY"] = "k"
    monkeypatch.setattr(enrich, "web_search", lambda q, **kw: results)


def test_migration_added_search_text_column(app):
    with app.app_context():
        cols = {c["name"] for c in inspect(db.engine).get_columns("products")}
    assert "search_text" in cols


def test_describe_stores_text_and_makes_product_findable(app, auth_client, monkeypatch):
    gid = _gid(app)
    _enable(app, monkeypatch, [{"url": "http://x", "content": "organic whole milk 2% fat"}])
    pid = _mk(app, gid)

    r = auth_client.post(f"/api/v1/products/{pid}/describe")

    assert r.status_code == 200
    assert "organic" in r.get_json()["searchText"].lower()
    found = auth_client.get("/api/v1/products?q=organic").get_json()
    assert any(p["name"] == "Milk" for p in found)


def test_describe_409_when_not_configured(app, auth_client):
    gid = _gid(app)
    app.config["OLLAMA_SEARCH_KEY"] = ""
    pid = _mk(app, gid)
    assert auth_client.post(f"/api/v1/products/{pid}/describe").status_code == 409


def test_describe_422_when_nothing_found(app, auth_client, monkeypatch):
    gid = _gid(app)
    _enable(app, monkeypatch, [])
    pid = _mk(app, gid)
    assert auth_client.post(f"/api/v1/products/{pid}/describe").status_code == 422


# Bulk enrichment moved to an async job (POST /jobs/enrich); see test_jobs.py for
# its owner-only guard and per-product processing.


def test_synthesis_is_provider_agnostic(app, monkeypatch):
    """Phase 5 parity: synthesis routes through assistant._complete, so it works with
    ANY provider (here anthropic), not just an Ollama-shaped /api/generate call."""
    from app.services import enrich
    with app.app_context():
        monkeypatch.setattr("app.services.assistant._cfg", lambda: {
            "provider": "anthropic", "base_url": "", "api_key": "k", "model": "claude",
            "timeout": 5, "max_steps": 6, "agent_id": ""})
        monkeypatch.setattr("app.services.assistant._complete",
                            lambda cfg, system, user: '{"description": "a tasty snack", "keywords": ["snack"]}')
        out = enrich._synthesize({"name": "Pretzels"}, [{"title": "t", "content": "c"}])
        assert out["description"] == "a tasty snack" and "snack" in out["keywords"]
