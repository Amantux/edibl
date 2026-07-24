"""AI web-searched searchable descriptions for products (Ollama web search).

The live web-search call is monkeypatched; with no LLM provider configured the
synthesis falls back to the top result's snippet, so tests are deterministic.
"""
from sqlalchemy import inspect

from app.auth import create_token
from app.extensions import db
from app.models import Group, Product, User


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


def test_describe_missing_only_blank(app, auth_client, monkeypatch):
    gid = _gid(app)
    _enable(app, monkeypatch, [{"content": "a dairy product"}])
    _mk(app, gid, name="Blank", search_text="")
    _mk(app, gid, name="Already", search_text="already described")

    r = auth_client.post("/api/v1/products/describe-missing").get_json()

    assert r["described"] == 1 and r["scanned"] == 1


def test_describe_missing_forbidden_for_non_owner(app):
    # The bulk (paid) batch is owner-only.
    with app.app_context():
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        member = User(name="M", email="m@x.com", password_hash="x",
                      is_owner=False, group_id=g.id)
        db.session.add(member)
        db.session.commit()
        token = create_token(member)
    r = app.test_client().post("/api/v1/products/describe-missing",
                               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
