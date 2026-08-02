"""Shared-PostgreSQL auto-provisioning client (app/pg_provision.py) + the
sqlalchemy_uri precedence that consumes its output.

No real network: the Supervisor reader and the provision POST are both mocked.
The security-critical invariants under test:
  - the provisioned DSN is validated (psycopg-only) before it's ever adopted;
  - main() writes the DSN file 0600 on success and NOT on a bad/foreign response;
  - main() short-circuits (never touches the network) unless every precondition
    holds — explicit URL wins, flag must be on, no existing DSN, under Supervisor.
"""
import logging
import os
import stat

import pytest

from app import _warn_stranded_sqlite, pg_provision
from app.config import Config


class _FakeApp:
    def __init__(self, uri, data_dir):
        self.config = {"SQLALCHEMY_DATABASE_URI": uri, "DATA_DIR": data_dir}


def test_warns_when_postgres_adopted_beside_populated_sqlite(tmp_path, caplog):
    (tmp_path / "edibl.db").write_text("data")
    app = _FakeApp("postgresql+psycopg://edibl:pw@shared/edibl", str(tmp_path))
    with caplog.at_level(logging.WARNING):
        _warn_stranded_sqlite(app, "edibl.db")
    assert any("migrate_from_sqlite" in r.message for r in caplog.records)


def test_no_warning_on_sqlite_target(tmp_path, caplog):
    (tmp_path / "edibl.db").write_text("data")
    app = _FakeApp(f"sqlite:///{tmp_path}/edibl.db", str(tmp_path))
    with caplog.at_level(logging.WARNING):
        _warn_stranded_sqlite(app, "edibl.db")
    assert not caplog.records


def test_no_warning_without_local_sqlite(tmp_path, caplog):
    app = _FakeApp("postgresql+psycopg://edibl:pw@shared/edibl", str(tmp_path))
    with caplog.at_level(logging.WARNING):
        _warn_stranded_sqlite(app, "edibl.db")
    assert not caplog.records


# --- sqlalchemy_uri precedence -------------------------------------------------

def _cfg(tmp_path, **attrs):
    """A Config subclass with a temp DATA_DIR and the given overrides."""
    return type("C", (Config,), {"DATA_DIR": str(tmp_path), **attrs})


def test_explicit_url_wins_over_shared_postgres(tmp_path):
    (tmp_path / ".database_url").write_text("postgresql+psycopg://p:p@dsn-host/edibl")
    C = _cfg(tmp_path, DATABASE_URL="postgresql+psycopg://u:u@env-host/edibl",
             USE_SHARED_POSTGRES=True)
    assert "env-host" in C.sqlalchemy_uri()


def test_shared_postgres_reads_provisioned_dsn(tmp_path):
    (tmp_path / ".database_url").write_text("postgresql+psycopg://edibl:pw@shared/edibl\n")
    C = _cfg(tmp_path, DATABASE_URL="", USE_SHARED_POSTGRES=True)
    assert C.sqlalchemy_uri() == "postgresql+psycopg://edibl:pw@shared/edibl"


def test_shared_postgres_normalizes_bare_driver(tmp_path):
    (tmp_path / ".database_url").write_text("postgresql://edibl:pw@shared/edibl")
    C = _cfg(tmp_path, DATABASE_URL="", USE_SHARED_POSTGRES=True)
    assert C.sqlalchemy_uri() == "postgresql+psycopg://edibl:pw@shared/edibl"


def test_shared_postgres_missing_file_falls_back_to_sqlite(tmp_path):
    C = _cfg(tmp_path, DATABASE_URL="", USE_SHARED_POSTGRES=True)
    assert C.sqlalchemy_uri().startswith("sqlite")


def test_shared_postgres_off_ignores_dsn_file(tmp_path):
    (tmp_path / ".database_url").write_text("postgresql+psycopg://edibl:pw@shared/edibl")
    C = _cfg(tmp_path, DATABASE_URL="", USE_SHARED_POSTGRES=False)
    assert C.sqlalchemy_uri().startswith("sqlite")


def test_provisioned_dsn_with_bad_driver_raises(tmp_path):
    (tmp_path / ".database_url").write_text("postgresql+asyncpg://edibl:pw@shared/edibl")
    C = _cfg(tmp_path, DATABASE_URL="", USE_SHARED_POSTGRES=True)
    with pytest.raises(RuntimeError, match="psycopg"):
        C.sqlalchemy_uri()


# --- main() short-circuits (must not hit the network) --------------------------

@pytest.fixture()
def no_network(monkeypatch):
    """Fail loudly if any provisioning path tries to reach the network."""
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("network call attempted during a short-circuit path")
    monkeypatch.setattr(pg_provision, "_candidate_provision_urls", boom)
    monkeypatch.setattr(pg_provision, "_discovery_config", boom)


def _patch_config(monkeypatch, tmp_path, **attrs):
    """Configure through the ENVIRONMENT, not by setting Config attributes.

    pg_provision resolves via app.settings.load_settings(), which is what the
    entrypoint relies on now that addon/run.sh no longer translates options.json
    into env vars. Patching Config would test nothing.
    """
    values = {"DATA_DIR": str(tmp_path), "DATABASE_URL": "",
              "USE_SHARED_POSTGRES": True, "POSTGRES_PROVISION_TOKEN": "", **attrs}
    for k, v in values.items():
        name = f"EDIBL_{k}"
        if v == "" or v is None:
            monkeypatch.delenv(name, raising=False)
        elif isinstance(v, bool):
            monkeypatch.setenv(name, "true" if v else "false")
        else:
            monkeypatch.setenv(name, str(v))


def test_main_noop_when_explicit_url_set(monkeypatch, tmp_path, no_network):
    _patch_config(monkeypatch, tmp_path, DATABASE_URL="postgresql+psycopg://u@h/db")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()


def test_main_noop_when_flag_off(monkeypatch, tmp_path, no_network):
    _patch_config(monkeypatch, tmp_path, USE_SHARED_POSTGRES=False)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    assert pg_provision.main() == 0


def test_main_noop_when_dsn_already_present(monkeypatch, tmp_path, no_network):
    (tmp_path / ".database_url").write_text("postgresql+psycopg://edibl:pw@shared/edibl")
    _patch_config(monkeypatch, tmp_path)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    assert pg_provision.main() == 0


def test_main_noop_when_not_under_supervisor(monkeypatch, tmp_path, no_network):
    _patch_config(monkeypatch, tmp_path)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()


def test_main_noop_when_no_token(monkeypatch, tmp_path):
    # Under Supervisor + flag on, but neither operator token nor discovery token.
    _patch_config(monkeypatch, tmp_path)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(pg_provision, "_discovery_config", lambda: None)
    called = []
    monkeypatch.setattr(pg_provision, "_candidate_provision_urls",
                        lambda cfg: called.append(1) or [])
    assert pg_provision.main() == 0
    assert not called  # bailed before building candidates


# --- main() happy + rejection paths -------------------------------------------

def _arm(monkeypatch, tmp_path, dsn_response, token="tok"):
    _patch_config(monkeypatch, tmp_path, POSTGRES_PROVISION_TOKEN=token)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(pg_provision, "_discovery_config", lambda: None)
    monkeypatch.setattr(pg_provision, "_candidate_provision_urls",
                        lambda cfg: ["http://shared-postgres:8087/provision"])
    seen = {}

    def fake_provision(url, tok):
        seen["url"], seen["token"] = url, tok
        return dsn_response

    monkeypatch.setattr(pg_provision, "_provision", fake_provision)
    return seen


def test_main_writes_dsn_0600_on_success(monkeypatch, tmp_path):
    dsn = "postgresql+psycopg://edibl:secret@shared-postgres:5432/edibl"
    seen = _arm(monkeypatch, tmp_path, dsn)
    assert pg_provision.main() == 0
    path = tmp_path / ".database_url"
    assert path.read_text() == dsn
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert seen["token"] == "tok"  # the token reached _provision


def test_main_rejects_foreign_dsn_scheme(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path, "mysql://edibl:secret@shared/edibl")
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()  # never persisted


def test_main_rejects_bare_postgresql_scheme(monkeypatch, tmp_path):
    # A response without the +psycopg driver would crash at first query — reject it.
    _arm(monkeypatch, tmp_path, "postgresql://edibl:secret@shared/edibl")
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()


def test_main_stays_on_sqlite_when_existing_data_and_no_migrate(monkeypatch, tmp_path, no_network):
    # Existing populated SQLite + migrate_from_sqlite off → don't provision an empty
    # Postgres and strand the data; stay on SQLite (no network, no DSN written).
    (tmp_path / "edibl.db").write_bytes(b"SQLite format 3\x00 with data")
    _patch_config(monkeypatch, tmp_path, POSTGRES_PROVISION_TOKEN="tok",
                  MIGRATE_FROM_SQLITE=False)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()


def test_main_provisions_over_existing_data_when_migrate_on(monkeypatch, tmp_path):
    # With migrate_from_sqlite on, the boot-migrate step will copy the data, so
    # provisioning proceeds even though a populated SQLite file exists.
    (tmp_path / "edibl.db").write_bytes(b"SQLite format 3\x00 with data")
    dsn = "postgresql+psycopg://edibl:secret@shared-postgres:5432/edibl"
    _arm(monkeypatch, tmp_path, dsn)
    monkeypatch.setenv("EDIBL_MIGRATE_FROM_SQLITE", "true")
    assert pg_provision.main() == 0
    assert (tmp_path / ".database_url").read_text() == dsn


def test_main_survives_provision_exception(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path, POSTGRES_PROVISION_TOKEN="tok")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(pg_provision, "_discovery_config", lambda: None)
    monkeypatch.setattr(pg_provision, "_candidate_provision_urls",
                        lambda cfg: ["http://a:8087/provision", "http://b:8087/provision"])

    def boom(url, tok):
        raise OSError("connection refused")

    monkeypatch.setattr(pg_provision, "_provision", boom)
    assert pg_provision.main() == 0  # tried both, gave up, stayed on SQLite
    assert not (tmp_path / ".database_url").exists()
