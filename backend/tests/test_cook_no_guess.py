"""Cooking consumes stock only on an UNAMBIGUOUS match — mutations don't guess.

cook_ingredients took match_products(...)[0] directly, so a weak
(SCORE_DESCRIPTION) or ambiguous top match silently ate stock — violating
ADR-0003 and the function's own docstring ("never guesses ... an unmatched item
is reported, not consumed"). It must route through resolve_for_mutation.
"""
from app.api.integrations import cook_ingredients
from app.extensions import db


def _add(client, name, **kw):
    body = {"name": name, "quantity": 5, "unit": "kg"}
    body.update(kw)
    return client.post("/api/v1/stock", json=body).get_json()


def _gid(app):
    from app.models import Group
    with app.app_context():
        return db.session.query(Group).first().id


def test_cooking_an_ambiguous_name_consumes_nothing(auth_client, app):
    # "milk" is a substring of both, so both are real (>= MEANINGFUL)
    # contenders — resolve_for_mutation must refuse rather than pick one.
    _add(auth_client, "Whole Milk", quantity=5)
    _add(auth_client, "Skim Milk", quantity=5)

    gid = _gid(app)
    with app.app_context():
        results = cook_ingredients(gid, [{"name": "milk", "quantity": 1}])

    r = results[0]
    assert r["consumed"] == 0, "an ambiguous match consumed stock (a guess)"
    assert r["matched"] is False


def test_cooking_an_unambiguous_name_still_consumes(auth_client, app):
    _add(auth_client, "Butter", quantity=5)
    gid = _gid(app)
    with app.app_context():
        results = cook_ingredients(gid, [{"name": "butter", "quantity": 2}])
    assert results[0]["consumed"] == 2
    assert results[0]["matched"] is True
