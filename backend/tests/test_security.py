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
