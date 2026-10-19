"""The Alembic schema-init path — fresh create AND the pre-Alembic ADOPTION/stamp
branch (the code that touches every existing install's database exactly once).

This is the riskiest code in the migration switch, so it gets direct coverage:
a future edit that breaks the stamp revision id, env.py URL passing, or the
create_all→stamp→upgrade ordering must fail here, not in production.
"""
import os

import pytest
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app import _run_migrations
from app.config import Config
from app.extensions import db
from app.models import Group, User


def _revision():
    with db.engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _head():
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AlembicConfig(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_the_migration_graph_has_exactly_one_head():
    """Two heads is a BOOT failure, not a CI nag.

    `app/__init__.py` runs `command.upgrade(cfg, "head")` in-process at startup,
    and Alembic raises on an ambiguous head — so a branched graph means the
    container does not come up at all. The way it happens is mundane: two
    migrations authored in parallel off the same parent, each correct on its own
    branch and only broken once both land. `get_current_head()` itself raises on
    a branch, so assert the count rather than trusting it to return something.
    """
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AlembicConfig(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "migrations"))

    # Resolve the graph FIRST and report a dangling parent in its own words.
    # ScriptDirectory walks the graph eagerly, so a revision naming a
    # down_revision that does not exist raises a bare `KeyError: '0013_...'`
    # here — before any assertion runs — and that traceback says nothing about
    # which file is wrong or why every other test in the suite also just errored.
    # This is the common case when two migrations are authored in parallel.
    try:
        scripts = ScriptDirectory.from_config(cfg)
        heads = scripts.get_heads()
    except KeyError as exc:
        raise AssertionError(
            f"a migration names a down_revision that does not exist: {exc}. "
            "Every revision in migrations/versions must have a parent present "
            "in this checkout — Alembic cannot resolve the graph otherwise, and "
            "app/__init__.py upgrades in-process at startup, so this is a boot "
            "failure rather than a test-only problem."
        ) from None

    assert len(heads) == 1, f"migration graph has branched: {heads}"


def test_fresh_db_is_at_head(app):
    # create_app already migrated the fresh temp DB.
    with app.app_context():
        assert inspect(db.engine).has_table("alembic_version")
        assert _revision() == _head()


def test_pre_alembic_db_is_adopted_and_data_survives(app):
    with app.app_context():
        # Seed real data through the ORM (real constraints)…
        g = Group(name="H")
        db.session.add(g)
        db.session.flush()
        db.session.add(User(name="Alex", email="a@x.com", password_hash="x", group_id=g.id))
        db.session.commit()
        # …then simulate a PRE-Alembic install by removing the version marker.
        db.session.execute(text("DROP TABLE alembic_version"))
        db.session.commit()
        assert not inspect(db.engine).has_table("alembic_version")

        # Adoption: fill gaps → stamp baseline → upgrade to head, data intact.
        _run_migrations(app)

        assert _revision() == _head()  # reaches head (baseline + deltas)
        assert db.session.query(User).filter_by(email="a@x.com").count() == 1


def test_url_validation_and_normalization(tmp_path):
    assert Config._normalize_db_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert Config._normalize_db_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"

    class Base(Config):
        DATA_DIR = str(tmp_path)

    class Blank(Base):
        DATABASE_URL = "   "
    assert Blank.sqlalchemy_uri().startswith("sqlite")  # whitespace → SQLite default

    class Async(Base):
        DATABASE_URL = "postgresql+asyncpg://u@h/db"
    with pytest.raises(RuntimeError, match="psycopg"):
        Async.sqlalchemy_uri()

    class Mysql(Base):
        DATABASE_URL = "mysql://u@h/db"
    with pytest.raises(RuntimeError, match="unsupported"):
        Mysql.sqlalchemy_uri()
