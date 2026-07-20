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

# Initialize/migrate the DB once before workers (avoids a create_all race).
echo "Initializing database schema…"
$RUN_AS python3 -c "from app import create_app; create_app()"

# MCP server (AI tooling for myMeal / Home Assistant), same container.
if [ "${EDIBL_MCP_ENABLED}" = "true" ]; then
  EDIBL_MCP_API="http://127.0.0.1:${EDIBL_PORT}/api/v1" \
    $RUN_AS python3 /app/backend/edibl_mcp.py &
  echo "Edibl MCP server on :${EDIBL_MCP_PORT}/sse"
fi

exec $RUN_AS gunicorn -b "0.0.0.0:${EDIBL_PORT}" -w 2 --timeout 120 "app:create_app()"
