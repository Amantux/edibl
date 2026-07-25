"""Cross-tenant (IDOR) guards on group-scoped writes."""
from app.extensions import db
from app.models import User, FoodConcept


def _register(app, client, email):
    client.post("/api/v1/users/register",
                json={"email": email, "password": "password", "name": email})
    tok = client.post("/api/v1/users/login",
                      json={"email": email, "password": "password"}).get_json()["token"]
    client.environ_base["HTTP_AUTHORIZATION"] = tok


def test_product_rejects_cross_group_concept(app):
    """A product cannot be linked to another household's FoodConcept — the FK alone
    would accept it and product_out would then leak the concept's canonicalName."""
    a, b = app.test_client(), app.test_client()
    _register(app, a, "a@a.com")
    _register(app, b, "b@b.com")

    with app.app_context():
        gid_a = db.session.query(User).filter_by(email="a@a.com").first().group_id
        concept = FoodConcept(canonical_name="Milk", group_id=gid_a)
        db.session.add(concept)
        db.session.commit()
        a_concept_id = concept.id

    # B (a different household) may not attach A's concept, on create or update.
    assert b.post("/api/v1/products",
                  json={"name": "Sneaky", "conceptId": a_concept_id}).status_code == 404
    own = b.post("/api/v1/products", json={"name": "Mine"}).get_json()["id"]
    assert b.put(f"/api/v1/products/{own}",
                 json={"conceptId": a_concept_id}).status_code == 404


def test_reservation_rejects_cross_group_concept(app):
    """add_reservation must not accept another household's FoodConcept id either."""
    a, b = app.test_client(), app.test_client()
    _register(app, a, "a2@a.com")
    _register(app, b, "b2@b.com")
    with app.app_context():
        gid_a = db.session.query(User).filter_by(email="a2@a.com").first().group_id
        concept = FoodConcept(canonical_name="Eggs", group_id=gid_a)
        db.session.add(concept)
        db.session.commit()
        a_concept_id = concept.id

    r = b.post("/api/v1/reservations",
               json={"name": "x", "conceptId": a_concept_id})
    assert r.status_code == 404


def test_whole_db_endpoints_require_instance_admin(app):
    """A later-registered household owner cannot pull the whole-DB backup or migrate
    the entire multi-tenant database — only the primary (first) group's owner can."""
    admin, other = app.test_client(), app.test_client()
    _register(app, admin, "admin@x.com")   # first registration → primary group
    _register(app, other, "other@x.com")   # a later, separate household

    assert other.get("/api/v1/export/backup.db").status_code == 403
    assert other.post("/api/v1/migrate/postgres",
                      json={"targetUrl": "x"}).status_code == 403
    # The instance admin (primary group owner) is not blocked (sqlite → 200).
    assert admin.get("/api/v1/export/backup.db").status_code == 200


def test_assistant_base_url_blocks_link_local_ssrf(app, auth_client):
    """The user-supplied LLM base URL can't point at link-local (cloud metadata),
    but a local Ollama URL is still allowed."""
    for url in ("http://169.254.169.254/latest/meta-data/",   # plain metadata IP
                "http://[::ffff:169.254.169.254]/",           # IPv4-mapped bypass
                "ftp://169.254.169.254/",                     # non-http scheme
                "http://2852039166/"):                        # decimal-encoded IP
        r = auth_client.put("/api/v1/assistant/settings", json={"baseUrl": url})
        assert r.status_code == 422, url

    # A malformed host must be a clean 422, never a 500.
    assert auth_client.put("/api/v1/assistant/settings",
                           json={"baseUrl": "http://\x80host/"}).status_code in (200, 422)

    # A local Ollama URL is still allowed.
    good = auth_client.put("/api/v1/assistant/settings",
                           json={"baseUrl": "http://127.0.0.1:11434"})
    assert good.status_code == 200
