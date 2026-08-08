"""A UNIQUE exact-name match resolves a mutation even when a weaker-KIND rival
(alias 0.9, family 0.8) sits within the 0.3 dominance gap. Exact identity beats
a pile of softer evidence — the old rule called 'Butter' (1.0) vs a 'butter'
family member (0.8) ambiguous and refused to act.
"""
from app.extensions import db
from app.models import Group, Product
from app.services import matching


def _gid():
    return db.session.query(Group).first().id


def test_unique_exact_name_beats_family_rival(auth_client, app):
    with app.app_context():
        gid = _gid()
        db.session.add(Product(name="Butter", category="dairy", group_id=gid))
        db.session.add(Product(name="Spread", family="butter", category="dairy",
                               group_id=gid))
        db.session.commit()
        res = matching.resolve_for_mutation(gid, "butter", item_types={"food"})
        assert res.ambiguous is False, "unique exact match wrongly ambiguous"
        assert res.product is not None and res.product.name == "Butter"


def test_two_exact_matches_stay_ambiguous(auth_client, app):
    with app.app_context():
        gid = _gid()
        db.session.add(Product(name="Milk", category="dairy", group_id=gid))
        db.session.add(Product(name="Milk", category="dairy", group_id=gid))
        db.session.commit()
        res = matching.resolve_for_mutation(gid, "milk", item_types={"food"})
        assert res.ambiguous is True, "two literally-'Milk' products aren't one thing"
        assert res.product is None
