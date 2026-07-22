"""New-household bootstrap: give every kitchen somewhere to put things.

A brand-new household starts with a **Kitchen** containing a **Fridge** and a
**Freezer**, so intake works immediately without first setting up storage. Guarded
by a per-household `locations_seeded` flag so it runs exactly once and never
re-adds places the user has since deleted.
"""
from flask import current_app

from ..extensions import db
from ..models import Location, Setting

_SEEDED_KEY = "locations_seeded"


def seed_default_locations(group_id: str) -> None:
    """Idempotent: seeds a Kitchen → Fridge/Freezer for a household that has none,
    then marks it done. Safe to call on every group creation AND at startup. The
    caller is responsible for committing."""
    if not current_app.config.get("SEED_DEFAULTS", True):
        return
    already = (db.session.query(Setting)
               .filter_by(group_id=group_id, key=_SEEDED_KEY).first())
    if already:
        return
    # Only add defaults to a genuinely empty household — never clobber existing
    # storage the user set up before this shipped.
    if db.session.query(Location).filter_by(group_id=group_id).count() == 0:
        kitchen = Location(name="Kitchen", kind="room", group_id=group_id)
        db.session.add(kitchen)
        db.session.flush()
        db.session.add(Location(name="Fridge", kind="fridge",
                                group_id=group_id, parent_id=kitchen.id))
        db.session.add(Location(name="Freezer", kind="freezer",
                                group_id=group_id, parent_id=kitchen.id))
    db.session.add(Setting(group_id=group_id, key=_SEEDED_KEY, value="1"))
    db.session.flush()


def seed_all_households() -> None:
    """Startup backfill so existing installs (and their households) get the default
    Kitchen/Fridge/Freezer too — once each."""
    from ..models import Group
    seeded = False
    for g in db.session.query(Group).all():
        before = db.session.query(Setting).filter_by(
            group_id=g.id, key=_SEEDED_KEY).first()
        if before:
            continue
        seed_default_locations(g.id)
        seeded = True
    if seeded:
        db.session.commit()
