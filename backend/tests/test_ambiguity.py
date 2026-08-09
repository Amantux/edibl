"""The "never guess" doctrine (ADR-0003), asserted on every surface.

Consuming, deleting or moving stock is destructive, so a name matching
materially-different products must ASK rather than act on whichever happens to
expire first. Three layers are covered here because they were three separate
bugs:

  * the SCORER decides when one product is unambiguous (exact identity beats a
    pile of softer evidence; two exact matches stay a question);
  * COOK reports an ambiguous ingredient instead of eating one;
  * CHAT gates all eight mutators — while READS still answer, because
    summarising everything that matches is the right response to a question.

The confirmation workflow these refusals lead into lives in
test_consume_confirmation.py.
"""

import pytest

from app.extensions import db
from app.models import Product
from app.services import matching
from app.api.integrations import cook_ingredients


# ---- the scorer: when is ONE product unambiguous? ----


def test_unique_exact_name_beats_family_rival(auth_client, app, gid):
    with app.app_context():
        db.session.add(Product(name="Butter", category="dairy", group_id=gid))
        db.session.add(Product(name="Spread", family="butter", category="dairy",
                               group_id=gid))
        db.session.commit()
        res = matching.resolve_for_mutation(gid, "butter", item_types={"food"})
        assert res.ambiguous is False, "unique exact match wrongly ambiguous"
        assert res.product is not None and res.product.name == "Butter"


def test_two_exact_matches_stay_ambiguous(auth_client, app, gid):
    with app.app_context():
        db.session.add(Product(name="Milk", category="dairy", group_id=gid))
        db.session.add(Product(name="Milk", category="dairy", group_id=gid))
        db.session.commit()
        res = matching.resolve_for_mutation(gid, "milk", item_types={"food"})
        assert res.ambiguous is True, "two literally-'Milk' products aren't one thing"
        assert res.product is None


# ---- cook: an ambiguous ingredient consumes nothing ----

def _add(client, name, **kw):
    body = {"name": name, "quantity": 5, "unit": "kg"}
    body.update(kw)
    return client.post("/api/v1/stock", json=body).get_json()



def test_cooking_an_ambiguous_name_consumes_nothing(auth_client, app, gid):
    # "milk" is a substring of both, so both are real (>= MEANINGFUL)
    # contenders — resolve_for_mutation must refuse rather than pick one.
    _add(auth_client, "Whole Milk", quantity=5)
    _add(auth_client, "Skim Milk", quantity=5)

    with app.app_context():
        results = cook_ingredients(gid, [{"name": "milk", "quantity": 1}])

    r = results[0]
    assert r["consumed"] == 0, "an ambiguous match consumed stock (a guess)"
    assert r["matched"] is False


def test_cooking_an_unambiguous_name_still_consumes(auth_client, app, gid):
    _add(auth_client, "Butter", quantity=5)
    with app.app_context():
        results = cook_ingredients(gid, [{"name": "butter", "quantity": 2}])
    assert results[0]["consumed"] == 2
    assert results[0]["matched"] is True


# ---- chat: every MUTATOR asks; reads still answer ----


def _stock(c, name, quantity=5, **kw):
    return c.post("/api/v1/stock",
                  json={"name": name, "quantity": quantity, "unit": "count",
                        "category": "dairy-alt", **kw}).get_json()


def _two_milks(c):
    return _stock(c, "Almond Milk"), _stock(c, "Coconut Milk")


def _lot(c, lot_id):
    return c.get(f"/api/v1/stock/{lot_id}").get_json()


# name -> (handler, kwargs) for every mutator that acts on ONE resolved lot
_MUTATORS = {
    "delete": ("h_delete_stock", {}),
    "open": ("h_open_stock", {}),
    "freeze": ("h_freeze_stock", {}),
    "thaw": ("h_thaw_stock", {}),
    "adjust": ("h_adjust_stock", {"quantity": 1}),
    "move": ("h_move_stock", {"location": "Fridge"}),
    "split": ("h_split_stock", {"quantity": 1}),
    "update": ("h_update_stock", {"quantity": 2}),
}


@pytest.mark.parametrize("label", sorted(_MUTATORS))
def test_ambiguous_name_changes_nothing_and_asks(label, auth_client, app, gid):
    import app.services.assistant as asst
    handler_name, kwargs = _MUTATORS[label]
    a, c = _two_milks(auth_client)
    auth_client.post("/api/v1/locations", json={"name": "Fridge"})

    with app.app_context():
        out = getattr(asst, handler_name)(gid, "milk", **kwargs)
        db.session.commit()

    msg = out if isinstance(out, str) else out[0]
    assert "Almond Milk" in msg and "Coconut Milk" in msg, f"{label}: {msg}"
    # both lots untouched: still present, same quantity, not finished/frozen
    for lot in (a, c):
        after = _lot(auth_client, lot["id"])
        assert after["quantity"] == 5, f"{label} changed a quantity"
        assert after["finished"] is False, f"{label} finished a lot"


@pytest.mark.parametrize("label", sorted(_MUTATORS))
def test_unambiguous_name_still_acts(label, auth_client, app, gid):
    """The gate must not break the normal path."""
    import app.services.assistant as asst
    handler_name, kwargs = _MUTATORS[label]
    _stock(auth_client, "Butter", 5, category="dairy")
    auth_client.post("/api/v1/locations", json={"name": "Fridge"})

    with app.app_context():
        out = getattr(asst, handler_name)(gid, "butter", **kwargs)
        db.session.commit()

    msg = out if isinstance(out, str) else out[0]
    assert "which" not in msg.lower() and "matches" not in msg.lower(), \
        f"{label} refused an unambiguous name: {msg}"


def test_reads_are_not_gated(auth_client, app, gid):
    """A question about ambiguous stock should ANSWER, not refuse."""
    from app.services.assistant import h_do_i_have, h_whats_in_stock
    _two_milks(auth_client)

    with app.app_context():
        have = h_do_i_have(gid, "milk")
        listing = h_whats_in_stock(gid, "milk")

    assert "Yes" in have, have
    assert "Almond Milk" in listing and "Coconut Milk" in listing
