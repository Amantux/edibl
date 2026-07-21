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

# Behind HA ingress the requests come from the trusted supervisor proxy.
export EDIBL_PROXY_HOPS="1"

# Auto-discover the Edibl integration in Home Assistant whenever we run under the
# Supervisor (matches the HomeHoard add-on). The integration probes the discovered
# add-on before offering setup, so an unreachable instance is aborted cleanly
# rather than creating a broken entry.
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
  python3 /register-discovery.py &
fi

echo "Starting Edibl (auth_disabled=${EDIBL_DISABLE_AUTH}, llm=${EDIBL_LLM_PROVIDER:-rules}, mcp=${EDIBL_MCP_ENABLED})"
exec /app/docker-entrypoint.sh
