"""Pulling the meal plan FROM myMeal is a full-snapshot upsert, not an append.

The outbound pull used to blindly db.session.add() every item on every call, so
pulling twice doubled every planned ingredient's demand. myMeal tags each row
with a sourceRef, so it upserts through the same (sourceRef, name) path the
inbound push uses.
"""
import app.api.integrations as integ

R1 = "mymeal:recipe:R1"


def _fake_plan(items):
    def _get(_path):
        return {"configured": True, "reachable": True, "data": {"items": items}}
    return _get


def _planned(client, ref=R1):
    rows = client.get("/api/v1/plan").get_json()["planned"]
    return [r for r in rows if r.get("sourceRef") == ref]


def test_pulling_twice_does_not_double_demand(auth_client, monkeypatch):
    items = [{"name": "flour", "quantity": 200, "unit": "g", "sourceRef": R1},
             {"name": "sugar", "quantity": 100, "unit": "g", "sourceRef": R1}]
    monkeypatch.setattr(integ, "mymeal_get", _fake_plan(items))

    r1 = auth_client.post("/api/v1/integrations/mymeal/pull")
    assert r1.status_code == 200 and r1.get_json()["pulled"] == 2
    assert len(_planned(auth_client)) == 2

    r2 = auth_client.post("/api/v1/integrations/mymeal/pull")
    assert r2.status_code == 200
    # still two rows, not four
    rows = _planned(auth_client)
    assert len(rows) == 2, f"pull doubled demand: {[x['name'] for x in rows]}"


def test_pull_prunes_a_dropped_ingredient(auth_client, monkeypatch):
    monkeypatch.setattr(integ, "mymeal_get", _fake_plan(
        [{"name": "flour", "quantity": 200, "unit": "g", "sourceRef": R1},
         {"name": "sugar", "quantity": 100, "unit": "g", "sourceRef": R1}]))
    auth_client.post("/api/v1/integrations/mymeal/pull")

    # sugar removed from the plan
    monkeypatch.setattr(integ, "mymeal_get", _fake_plan(
        [{"name": "flour", "quantity": 200, "unit": "g", "sourceRef": R1}]))
    r = auth_client.post("/api/v1/integrations/mymeal/pull")

    assert r.get_json()["pruned"] == 1
    assert [x["name"] for x in _planned(auth_client)] == ["flour"]
