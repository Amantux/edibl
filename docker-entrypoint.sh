#!/usr/bin/env sh
set -e
: "${EDIBL_DATA_DIR:=/data}"
: "${EDIBL_DISABLE_AUTH:=false}"
: "${EDIBL_SECRET_KEY:=$(head -c 32 /dev/urandom | base64)}"
: "${EDIBL_PORT:=7746}"
: "${EDIBL_MCP_ENABLED:=true}"
: "${EDIBL_MCP_PORT:=7767}"
export EDIBL_DATA_DIR EDIBL_DISABLE_AUTH EDIBL_SECRET_KEY EDIBL_PORT EDIBL_MCP_PORT

mkdir -p "$EDIBL_DATA_DIR"

RUN_AS=""
if [ "$(id -u)" = "0" ]; then
  chown -R app:app "$EDIBL_DATA_DIR" 2>/dev/null || true
  RUN_AS="gosu app"
fi

cd /app/backend

# Initialize/migrate the DB once before workers (avoids a create_all race). Mint the
# stable integration API key in the SAME process (race-free — no second create_all)
# whenever a machine client needs it: under the HA Supervisor (the integration), or
# in hardened mode with the MCP server on (its outbound calls need a REST key).
MINT_TOKEN=false
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then MINT_TOKEN=true; fi
if [ "${EDIBL_DISABLE_AUTH}" != "true" ] && [ "${EDIBL_MCP_ENABLED}" = "true" ]; then
  MINT_TOKEN=true
fi
echo "Initializing database schema…"
$RUN_AS env MINT_TOKEN="$MINT_TOKEN" python3 -c "import os
from app import create_app
app = create_app()
if os.environ.get('MINT_TOKEN') == 'true':
    from app.integration_token import ensure_integration_token
    ensure_integration_token(app)"

# Register HA Supervisor discovery AFTER init, so the minted token file already
# exists when the publisher reads it. Add-on only (script present + under the
# Supervisor); the standalone image ships neither and skips this.
if [ -f /register-discovery.py ] && [ -n "${SUPERVISOR_TOKEN:-}" ]; then
  $RUN_AS python3 /register-discovery.py &
fi

# MCP server (AI tooling for myMeal / Home Assistant), same container. In hardened
# mode its outbound calls to the REST API need a key — hand it the minted
# integration token (full scope). Open mode reaches the API without one.
if [ "${EDIBL_MCP_ENABLED}" = "true" ]; then
  MCP_API_TOKEN=""
  if [ "${EDIBL_DISABLE_AUTH}" != "true" ] && [ -f "${EDIBL_DATA_DIR}/.integration_token" ]; then
    MCP_API_TOKEN="$(cat "${EDIBL_DATA_DIR}/.integration_token" 2>/dev/null || true)"
  fi
  EDIBL_MCP_API="http://127.0.0.1:${EDIBL_PORT}/api/v1" \
  EDIBL_MCP_API_TOKEN="$MCP_API_TOKEN" \
    $RUN_AS python3 /app/backend/edibl_mcp.py &
  echo "Edibl MCP server on :${EDIBL_MCP_PORT}/sse"
fi

exec $RUN_AS gunicorn -b "0.0.0.0:${EDIBL_PORT}" -w 2 --timeout 120 "app:create_app()"
