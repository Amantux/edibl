"""Every chat MUTATOR refuses an ambiguous name, like consume already does.

h_record_consumption was fixed to run the shared tiered resolution, but the
other eight mutators still did a raw substring match and acted on lots[0] — so
"delete the milk" / "move the milk" / "correct the milk" silently picked
whichever product happened to expire first. Same defect class, same fix.

Reads (h_do_i_have, h_whats_in_stock) are deliberately NOT gated: summarising
everything that matches is the right answer for a question.
"""
import pytest

from app.extensions import db
from app.models import Group


def _gid(app):
    with app.app_context():
        return db.session.query(Group).first().id


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
def test_ambiguous_name_changes_nothing_and_asks(label, auth_client, app):
    import app.services.assistant as asst
    handler_name, kwargs = _MUTATORS[label]
    a, c = _two_milks(auth_client)
    auth_client.post("/api/v1/locations", json={"name": "Fridge"})
    gid = _gid(app)

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
def test_unambiguous_name_still_acts(label, auth_client, app):
    """The gate must not break the normal path."""
    import app.services.assistant as asst
    handler_name, kwargs = _MUTATORS[label]
    _stock(auth_client, "Butter", 5, category="dairy")
    auth_client.post("/api/v1/locations", json={"name": "Fridge"})
    gid = _gid(app)

    with app.app_context():
        out = getattr(asst, handler_name)(gid, "butter", **kwargs)
        db.session.commit()

    msg = out if isinstance(out, str) else out[0]
    assert "which" not in msg.lower() and "matches" not in msg.lower(), \
        f"{label} refused an unambiguous name: {msg}"


def test_reads_are_not_gated(auth_client, app):
    """A question about ambiguous stock should ANSWER, not refuse."""
    from app.services.assistant import h_do_i_have, h_whats_in_stock
    _two_milks(auth_client)
    gid = _gid(app)

    with app.app_context():
        have = h_do_i_have(gid, "milk")
        listing = h_whats_in_stock(gid, "milk")

    assert "Yes" in have, have
    assert "Almond Milk" in listing and "Coconut Milk" in listing
