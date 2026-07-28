"""Background job engine (ported from HomeHoard): enqueue, atomic claim, run, reap.

Worker poller is disabled in tests (WORKER_ENABLED=False); these drive the job
functions directly. No live vendor calls (enrich.describe is stubbed).
"""
from datetime import timedelta

from app.extensions import db
from app.models import Group, Job, Product, User, utcnow
from app.services import jobs


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def test_enqueue_creates_pending_and_dedups(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        a = jobs.enqueue("enrich", gid)
        b = jobs.enqueue("enrich", gid)
        assert a.id == b.id and a.status == "pending"


def test_claim_is_atomic_no_double_run(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        jobs.enqueue("enrich", gid)
        assert jobs.claim_one() is not None
        assert jobs.claim_one() is None


def test_enrich_job_describes_missing_products(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        db.session.add_all([Product(name="Milk", group_id=gid),
                            Product(name="Eggs", group_id=gid, search_text="already")])
        db.session.commit()
        monkeypatch.setattr("app.services.enrich.enabled", lambda: True)
        monkeypatch.setattr("app.services.enrich.describe",
                            lambda fields, cfg=None: {"description": "dairy", "keywords": ["food"]})

        job = jobs.enqueue("enrich", gid)
        jobs.run_job(jobs.claim_one())

        done = db.session.get(Job, job.id)
        assert done.status == "done"
        assert done.result["described"] == 1 and done.result["remaining"] == 0
        milk = db.session.query(Product).filter_by(name="Milk").first()
        assert "dairy" in milk.search_text


def test_enrich_job_errors_when_not_configured(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        monkeypatch.setattr("app.services.enrich.enabled", lambda: False)
        job = jobs.enqueue("enrich", gid)
        jobs.run_job(jobs.claim_one())
        assert db.session.get(Job, job.id).status == "error"


def test_reap_stale_requeues_dead_and_spares_live(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        dead = Job(kind="enrich", group_id=gid, status="running",
                   started_at=utcnow() - timedelta(hours=1))
        db.session.add(dead)
        db.session.commit()
        assert jobs.reap_stale() == 1
        assert db.session.get(Job, dead.id).status == "pending"
        # A different kind so it doesn't collide with the requeued 'enrich' on the
        # one-active-per-group+kind index. A live (recently heartbeated) running job
        # must NOT be reaped.
        live = Job(kind="cleanup", group_id=gid, status="running", started_at=utcnow())
        db.session.add(live)
        db.session.commit()
        assert jobs.reap_stale() == 0
        assert db.session.get(Job, live.id).status == "running"


def test_only_one_active_job_per_group_kind_enforced(auth_client, app):
    from sqlalchemy.exc import IntegrityError
    gid = _gid(app)
    with app.app_context():
        db.session.add(Job(kind="enrich", group_id=gid, status="pending"))
        db.session.commit()
        db.session.add(Job(kind="enrich", group_id=gid, status="running"))
        try:
            db.session.commit()
            assert False, "expected the partial-unique constraint to fire"
        except IntegrityError:
            db.session.rollback()


def test_create_job_endpoint_and_poll(auth_client):
    r = auth_client.post("/api/v1/jobs/enrich")
    assert r.status_code == 202
    jid = r.get_json()["id"]
    assert auth_client.get(f"/api/v1/jobs/{jid}").get_json()["status"] == "pending"


def test_create_job_unknown_kind_404(auth_client):
    assert auth_client.post("/api/v1/jobs/bogus").status_code == 404


def test_job_get_cross_group_404(auth_client, app):
    with app.app_context():
        other = Group(name="Other")
        db.session.add(other)
        db.session.flush()
        j = Job(kind="enrich", group_id=other.id)
        db.session.add(j)
        db.session.commit()
        jid = j.id
    assert auth_client.get(f"/api/v1/jobs/{jid}").status_code == 404
