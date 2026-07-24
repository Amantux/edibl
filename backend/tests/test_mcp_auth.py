"""MCP inbound authorization: UI-minted keys (scope mcp/full) unlock the MCP
server; rest-only keys and unknown tokens don't; the legacy static server token
still works; and the gate only turns on once an mcp-scoped key exists."""
import edibl_mcp
from app.auth import _default_user
from app.extensions import db
from app.models import ApiToken, generate_raw_token, hash_token


def _add_key(app, scope):
    raw = generate_raw_token()
    with app.app_context():
        u = _default_user()
        db.session.add(ApiToken(name=f"k-{scope}", scope=scope, token_hash=hash_token(raw),
                                hint=raw[:9], user_id=u.id, group_id=u.group_id))
        db.session.commit()
    return raw


def test_key_scope_gates_mcp(app, monkeypatch):
    monkeypatch.setattr(edibl_mcp, "_app", app)
    full = _add_key(app, "full")
    mcpk = _add_key(app, "mcp")
    restk = _add_key(app, "rest")

    assert edibl_mcp._key_ok(full) is True
    assert edibl_mcp._key_ok(mcpk) is True
    assert edibl_mcp._key_ok(restk) is False        # rest-only can't reach MCP
    assert edibl_mcp._key_ok("edbl_not_real") is False
    assert edibl_mcp._key_ok("") is False


def test_authorized_accepts_key_and_server_token(app, monkeypatch):
    monkeypatch.setattr(edibl_mcp, "_app", app)
    full = _add_key(app, "full")

    assert edibl_mcp._authorized(f"Bearer {full}", "") is True
    assert edibl_mcp._authorized("Bearer srv-tok", "srv-tok") is True   # legacy path
    assert edibl_mcp._authorized("Bearer wrong", "srv-tok") is False
    assert edibl_mcp._authorized("", "") is False


def test_gate_requires_mcp_scoped_key(app, monkeypatch):
    # A full/rest key alone does NOT flip the gate on (avoids silently locking a
    # previously-open endpoint via the auto integration key); an mcp key does.
    monkeypatch.setattr(edibl_mcp, "_app", app)
    app.config["DISABLE_AUTH"] = True   # isolate the mcp-key signal from hardened mode
    _add_key(app, "full")
    assert edibl_mcp._mcp_key_exists() is False
    _add_key(app, "mcp")
    assert edibl_mcp._mcp_key_exists() is True


def test_auth_required_hardened_mode(app, monkeypatch):
    # app fixture is DISABLE_AUTH=False → a hardened app means a hardened MCP.
    monkeypatch.setattr(edibl_mcp, "_app", app)
    assert edibl_mcp._auth_required("") is True


def test_auth_required_open_mode_tracks_mcp_key(app, monkeypatch):
    monkeypatch.setattr(edibl_mcp, "_app", app)
    app.config["DISABLE_AUTH"] = True
    assert edibl_mcp._auth_required("") is False   # open + no mcp key → open
    _add_key(app, "mcp")
    assert edibl_mcp._auth_required("") is True     # minting an mcp key gates it


def test_auth_required_server_token_always(app, monkeypatch):
    monkeypatch.setattr(edibl_mcp, "_app", app)
    assert edibl_mcp._auth_required("srv-tok") is True


def test_auth_required_fails_closed_on_db_error(monkeypatch):
    # A broken/locked DB must REQUIRE auth, never serve unauthenticated.
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(edibl_mcp, "_get_app", boom)
    assert edibl_mcp._auth_required("") is True
