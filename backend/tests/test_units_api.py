"""Units-of-measure CRUD (group-scoped, canonicalized, myMeal-shaped)."""


def test_create_unit_canonicalizes_and_sets_dimension(auth_client):
    u = auth_client.post("/api/v1/units",
                         json={"name": "Cups", "abbreviation": "c"}).get_json()
    assert u["name"] == "cup"            # canonicalized
    assert u["dimension"] == "volume"    # dimension filled
    assert u["abbreviation"] == "c"


def test_create_unit_is_idempotent_by_canonical_name(auth_client):
    auth_client.post("/api/v1/units", json={"name": "each"})
    auth_client.post("/api/v1/units", json={"name": "pieces"})   # both → 'count'
    names = [u["name"] for u in auth_client.get("/api/v1/units").get_json()]
    assert names.count("count") == 1


def test_seed_defaults_populates_canonical_vocabulary(auth_client):
    r = auth_client.post("/api/v1/units/seed-defaults").get_json()
    assert r["added"] > 0 and r["total"] == r["added"]
    again = auth_client.post("/api/v1/units/seed-defaults").get_json()
    assert again["added"] == 0            # idempotent


def test_delete_unit(auth_client):
    uid = auth_client.post("/api/v1/units", json={"name": "clove"}).get_json()["id"]
    assert auth_client.delete(f"/api/v1/units/{uid}").status_code == 200
    assert auth_client.get("/api/v1/units").get_json() == []


def test_units_are_household_scoped(app, auth_client, client):
    uid = auth_client.post("/api/v1/units", json={"name": "cup"}).get_json()["id"]
    client.post("/api/v1/users/register",
                json={"email": "b@b.com", "password": "password", "name": "B"})
    tok = client.post("/api/v1/users/login",
                      json={"email": "b@b.com", "password": "password"}).get_json()["token"]
    r = client.delete(f"/api/v1/units/{uid}", headers={"Authorization": tok})
    assert r.status_code == 404           # can't delete another household's unit
