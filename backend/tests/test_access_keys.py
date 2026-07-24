"""Per-key access scopes for UI-minted API keys.

A key's `scope` decides what it unlocks: `full` (default) → everything, `rest` →
REST API only, `mcp` → the MCP server only (rejected on REST). Minted from the
owner-only /tokens endpoint; enforced in auth._user_from_api_token for REST.
"""
SUMMARY = "/api/v1/ha/sensors"  # auth-gated REST endpoint


def _mint(client, scope=None):
    body = {"name": f"k-{scope or 'default'}"}
    if scope is not None:
        body["scope"] = scope
    resp = client.post("/api/v1/tokens", json=body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def test_default_scope_is_full(auth_client):
    row = _mint(auth_client)
    assert row["scope"] == "full"


def test_full_key_authenticates_rest(auth_client):
    raw = _mint(auth_client, "full")["token"]
    fresh = auth_client.application.test_client()  # no ambient auth header
    resp = fresh.get(SUMMARY, headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200


def test_rest_key_authenticates_rest(auth_client):
    raw = _mint(auth_client, "rest")["token"]
    fresh = auth_client.application.test_client()
    resp = fresh.get(SUMMARY, headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200


def test_mcp_scoped_key_is_rejected_on_rest(auth_client):
    # An MCP-only key must NOT authenticate the REST API — it's for the MCP server.
    raw = _mint(auth_client, "mcp")["token"]
    fresh = auth_client.application.test_client()
    resp = fresh.get(SUMMARY, headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401


def test_invalid_scope_rejected(auth_client):
    resp = auth_client.post("/api/v1/tokens", json={"name": "bad", "scope": "root"})
    assert resp.status_code == 400
