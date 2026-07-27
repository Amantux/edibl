"""AI tooling: confidence-gated auto-categorize (product category), cluster (family)
proposals, review queue, ICL feedback. No live vendor calls (the LLM is stubbed)."""
from app.extensions import db
from app.models import AiSuggestion, Group, Product, User
from app.services import jobs


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _use_llm(monkeypatch, reply):
    monkeypatch.setattr("app.services.assistant._cfg", lambda: {
        "provider": "ollama", "base_url": "", "api_key": "", "model": "m",
        "timeout": 5, "max_steps": 6, "agent_id": ""})
    monkeypatch.setattr("app.services.assistant._complete",
                        lambda cfg, system, user: reply)


def _run(kind, gid):
    jobs.enqueue(kind, gid)
    jobs.run_job(jobs.claim_one())


def test_categorize_auto_applies_high_confidence_known_category(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        db.session.add(Product(name="Whole milk", group_id=gid, category="other"))
        db.session.commit()
        _use_llm(monkeypatch, '{"category": "dairy", "confidence": 0.95, "rationale": "milk"}')

        _run("categorize", gid)

        p = db.session.query(Product).filter_by(name="Whole milk").first()
        assert p.category == "dairy"
        assert db.session.query(AiSuggestion).filter_by(status="accepted").count() == 1


def test_categorize_queues_unknown_category(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        db.session.add(Product(name="Mystery", group_id=gid, category="other"))
        db.session.commit()
        _use_llm(monkeypatch, '{"category": "exotica", "confidence": 0.99}')  # not in CATEGORIES

        _run("categorize", gid)

        p = db.session.query(Product).filter_by(name="Mystery").first()
        assert p.category == "other"  # unchanged
        assert db.session.query(AiSuggestion).filter_by(status="pending").count() == 1


def test_categorize_malformed_confidence_completes(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        db.session.add(Product(name="Thing", group_id=gid, category="other"))
        db.session.commit()
        _use_llm(monkeypatch, '{"category": "dairy", "confidence": "high"}')
        _run("categorize", gid)
        from app.models import Job
        assert db.session.query(Job).filter_by(kind="categorize").first().status == "done"


def test_accept_categorize_sets_category(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        p = Product(name="Cheddar", group_id=gid, category="other")
        db.session.add(p)
        db.session.flush()
        s = AiSuggestion(kind="categorize", status="pending", label="dairy",
                         confidence=0.4, product_id=p.id, group_id=gid)
        db.session.add(s)
        db.session.commit()
        sid, pid = s.id, p.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/accept").status_code == 200
    with app.app_context():
        assert db.session.get(Product, pid).category == "dairy"


def test_cluster_and_accept_sets_family(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        for n in ("Whole milk", "Skimmed milk", "Bread"):
            db.session.add(Product(name=n, group_id=gid))
        db.session.commit()
        _use_llm(monkeypatch, '{"clusters": [{"name": "Milk", "indices": [0, 1], "confidence": 0.9}]}')

        _run("cluster", gid)

        s = db.session.query(AiSuggestion).filter_by(kind="cluster", status="pending").first()
        assert s and s.label == "Milk"
        sid = s.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/accept").status_code == 200
    with app.app_context():
        fams = {p.name: p.family for p in db.session.query(Product).all()}
        assert fams["Whole milk"] == "Milk" and fams["Skimmed milk"] == "Milk"
        assert fams["Bread"] == ""  # not a member


def test_reject_suggestion(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        s = AiSuggestion(kind="categorize", status="pending", label="dairy",
                         confidence=0.3, group_id=gid)
        db.session.add(s)
        db.session.commit()
        sid = s.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/reject").status_code == 200
    with app.app_context():
        assert db.session.get(AiSuggestion, sid).status == "rejected"


def test_categorize_job_errors_without_provider(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        db.session.add(Product(name="X", group_id=gid, category="other"))
        db.session.commit()
        monkeypatch.setattr("app.services.assistant._cfg", lambda: {"provider": ""})
        job = jobs.enqueue("categorize", gid)
        jobs.run_job(jobs.claim_one())
        from app.models import Job
        assert db.session.get(Job, job.id).status == "error"


def test_delete_product_with_suggestion_succeeds(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        p = Product(name="Disposable", group_id=gid)
        db.session.add(p)
        db.session.flush()
        db.session.add(AiSuggestion(kind="categorize", status="accepted", label="dairy",
                                    confidence=0.9, product_id=p.id, group_id=gid))
        db.session.commit()
        pid = p.id
    assert auth_client.delete(f"/api/v1/products/{pid}").status_code in (200, 204)
    with app.app_context():
        assert db.session.query(AiSuggestion).filter_by(product_id=pid).count() == 0


def test_suggestion_cross_group_404(auth_client, app):
    with app.app_context():
        other = Group(name="Other")
        db.session.add(other)
        db.session.flush()
        s = AiSuggestion(kind="categorize", status="pending", label="x", group_id=other.id)
        db.session.add(s)
        db.session.commit()
        sid = s.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/accept").status_code == 404
