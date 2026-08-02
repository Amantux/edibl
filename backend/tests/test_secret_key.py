"""The signing key must survive a restart.

It used to be defaulted in the entrypoint from /dev/urandom, so every container
start minted a NEW key — silently logging every user out and voiding every issued
API token (including scoped MCP keys). These tests pin the fix: generate once,
persist 0600, reuse thereafter, and never override an operator-supplied key.
"""
import os
import stat

from app.config import Config, ensure_secret_key


def _cfg(tmp_path, **attrs):
    return type("C", (Config,), {"DATA_DIR": str(tmp_path), **attrs})


def test_generated_key_is_stable_across_restarts(tmp_path):
    # Two independent "boots" against the same data dir must agree, or every
    # session and API token dies on restart.
    cfg = _cfg(tmp_path, SECRET_KEY="")
    first, generated_first = ensure_secret_key(cfg)
    second, generated_second = ensure_secret_key(cfg)

    assert generated_first is True      # minted on the first boot
    assert generated_second is False    # reused on the second
    assert first == second
    assert len(first) >= 32


def test_generated_key_is_persisted_0600(tmp_path):
    ensure_secret_key(_cfg(tmp_path, SECRET_KEY=""))
    path = tmp_path / ".secret_key"

    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_an_explicit_key_wins_and_is_not_persisted(tmp_path):
    supplied = "an-operator-supplied-key-that-is-long-enough"
    secret, generated = ensure_secret_key(_cfg(tmp_path, SECRET_KEY=supplied))

    assert secret == supplied
    assert generated is False
    assert not (tmp_path / ".secret_key").exists()


def test_the_shipped_placeholder_counts_as_unset(tmp_path):
    # Otherwise every install would share one public signing key.
    secret, generated = ensure_secret_key(
        _cfg(tmp_path, SECRET_KEY="change-me-in-production"))

    assert generated is True
    assert secret != "change-me-in-production"


def test_create_app_reuses_the_persisted_key(tmp_path):
    # End-to-end: the app factory itself must not mint a new key per boot.
    from app import create_app

    cfg = _cfg(tmp_path, SECRET_KEY="", DATABASE_URL=f"sqlite:///{tmp_path}/t.db",
               DISABLE_AUTH=True, RATELIMIT_ENABLED=False, SEED_DEFAULTS=False,
               WORKER_ENABLED=False)
    first = create_app(cfg).config["SECRET_KEY"]
    second = create_app(cfg).config["SECRET_KEY"]

    assert first == second
