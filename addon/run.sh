#!/usr/bin/env sh
# Home Assistant add-on entrypoint: map add-on options → Edibl env, then hand off
# to the app's standard entrypoint (preboot DB init, MCP server, gunicorn).
set -e

CONFIG=/data/options.json

# Read a string option ("" when unset/null).
gets() {
  python3 -c "import json;v=json.load(open('$CONFIG')).get('$1');print('' if v is None else v)" 2>/dev/null || true
}
# Read a boolean option as lowercase true/false (docker-entrypoint.sh compares literally).
getb() {
  python3 -c "import json;v=json.load(open('$CONFIG')).get('$1');print(str(bool(v)).lower())" 2>/dev/null || echo false
}

export EDIBL_DATA_DIR=/data
export EDIBL_DISABLE_AUTH="$(getb disable_auth)"
export EDIBL_LLM_PROVIDER="$(gets llm_provider)"
export EDIBL_LLM_BASE_URL="$(gets llm_base_url)"
export EDIBL_LLM_API_KEY="$(gets llm_api_key)"
export EDIBL_LLM_MODEL="$(gets llm_model)"
export EDIBL_LLM_AGENT_ID="$(gets llm_agent_id)"
export EDIBL_BARCODE_LOOKUP="$(getb barcode_lookup)"
export EDIBL_MCP_ENABLED="$(getb mcp_enabled)"
export EDIBL_MCP_SERVER_TOKEN="$(gets mcp_server_token)"
# Optional external Postgres; blank keeps the built-in SQLite in /data.
export EDIBL_DATABASE_URL="$(gets database_url)"
export EDIBL_MIGRATE_FROM_SQLITE="$(getb migrate_from_sqlite)"
# Optional Ollama web-search key for AI product descriptions.
export EDIBL_OLLAMA_SEARCH_KEY="$(gets ollama_search_key)"

# Behind HA ingress the requests come from the trusted supervisor proxy.
export EDIBL_PROXY_HOPS="1"

# HA Supervisor auto-discovery (and the integration API-key mint it depends on)
# is handled by docker-entrypoint.sh AFTER DB init, so the token file exists when
# the publisher reads it. Keeping it there also makes it race-free with the
# one-shot schema init (no second create_all).

echo "Starting Edibl (auth_disabled=${EDIBL_DISABLE_AUTH}, llm=${EDIBL_LLM_PROVIDER:-rules}, mcp=${EDIBL_MCP_ENABLED})"
exec /app/docker-entrypoint.sh
