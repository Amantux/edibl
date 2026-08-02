#!/usr/bin/env sh
set -e
# The data dir is the one value the shell needs BEFORE Python can run (mkdir +
# chown happen as root, before the privilege drop). Everything else — port, MCP
# settings, auth — is resolved from the registry below, so there is no second
# set of defaults here to drift from the declared ones.
: "${EDIBL_DATA_DIR:=/data}"
export EDIBL_DATA_DIR

# NOTE: EDIBL_SECRET_KEY is intentionally NOT defaulted here. It used to be
# `head -c 32 /dev/urandom`, which minted a NEW signing key on every container
# start — silently logging every user out and voiding every issued API token
# (including scoped MCP keys) on each restart. The application now generates one
# once and persists it under EDIBL_DATA_DIR instead. An explicitly-set
# EDIBL_SECRET_KEY still wins.

mkdir -p "$EDIBL_DATA_DIR"

RUN_AS=""
if [ "$(id -u)" = "0" ]; then
  # A root-owned /data from a previous release is fixed here (root can always
  # chown). The failure case is a read-only or non-chownable mount (ro bind,
  # some SMB/NFS-backed setups): the chown no-ops, we drop to uid 1000, and the
  # first request dies with "unable to open database file" hours later with no
  # explanation. So verify writability AS the app user and fail loudly now.
  chown -R app:app "$EDIBL_DATA_DIR" 2>/dev/null || true
  if ! gosu app test -w "$EDIBL_DATA_DIR"; then
    echo "Edibl: $EDIBL_DATA_DIR is not writable by the 'app' user (uid 1000)." >&2
    echo "       The data directory must be writable by the container's app user." >&2
    exit 1
  fi
  RUN_AS="gosu app"
fi

cd /app/backend

# Validate the configuration before anything starts, so a bad options.json or
# env var fails immediately with EVERY problem listed, rather than booting into
# a confusing 500 on some later request.
if ! $RUN_AS python3 -m app.config_check; then
  echo "Edibl: refusing to start with invalid configuration (see above)." >&2
  exit 1
fi

# Read back the handful of values the shell itself needs, from the SAME source
# of truth the app uses. One process, one parse. Only structural values go
# through this eval; a secret must never be eval'd.
#
# Two guards: the output is filtered to RESOLVED_* lines, so a stray print() on
# an import path cannot become shell code; and the result is captured and
# checked, because `eval "$(cmd)"` swallows cmd's exit status.
#
# Written as `python3 -c '...'` rather than a heredoc inside $( ): the image's
# /bin/sh is dash, which does not parse the heredoc form with a trailing pipe,
# and `sh -n` on a bash-as-sh host accepts it — only a container run catches it.
RESOLVED=$($RUN_AS python3 -c 'from app.settings import load_settings
s = load_settings()
print(f"RESOLVED_PORT={s.PORT}")
print("RESOLVED_MCP_ENABLED=" + ("true" if s.MCP_ENABLED else "false"))
print("RESOLVED_DISABLE_AUTH=" + ("true" if s.DISABLE_AUTH else "false"))
print(f"RESOLVED_MCP_PORT={s.MCP_PORT}")' | grep "^RESOLVED_[A-Z_]*=")
eval "$RESOLVED"
if [ -z "${RESOLVED_PORT:-}" ] || [ -z "${RESOLVED_MCP_PORT:-}" ]; then
  echo "Edibl: could not resolve settings for startup; refusing to start." >&2
  exit 1
fi

# Initialize/migrate the DB once before workers (avoids a create_all race). Mint the
# stable integration API key in the SAME process (race-free — no second create_all)
# whenever a machine client needs it: under the HA Supervisor (the integration), or
# in hardened mode with the MCP server on (its outbound calls need a REST key).
MINT_TOKEN=false
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then MINT_TOKEN=true; fi
if [ "${RESOLVED_DISABLE_AUTH}" != "true" ] && [ "${RESOLVED_MCP_ENABLED}" = "true" ]; then
  MINT_TOKEN=true
fi
# Shared PostgreSQL: when enabled, discover the add-on and provision our own
# database (writes the DSN to $EDIBL_DATA_DIR/.database_url, which the app reads).
# Runs as the app user (so it owns the 0600 DSN file) and BEFORE schema init, so
# create_app builds the engine against the right database. Best-effort — it
# self-selects SQLite if anything is missing, so it never blocks startup. It
# logs its own reason on every fall-back-to-SQLite path, so this `||` fires only
# when the module itself crashed — say that, rather than the misleading
# "skipped" (which would read as a normal outcome).
$RUN_AS python3 -m app.pg_provision \
  || echo "Edibl: pg_provision failed unexpectedly; continuing on SQLite." >&2

echo "Initializing database schema…"
# Also report which backend the app actually booted against. Every "why is my
# data missing / why didn't the migration take" question starts here, and until
# now the log never said. /api/v1/diagnostics reports it too, but that needs a
# login — the log is what an operator pastes into an issue.
$RUN_AS env MINT_TOKEN="$MINT_TOKEN" python3 -c "import os
from app import create_app
app = create_app()
uri = app.config['SQLALCHEMY_DATABASE_URI']
print('Edibl: database backend =', 'sqlite' if uri.startswith('sqlite') else 'postgresql')
if os.environ.get('MINT_TOKEN') == 'true':
    from app.integration_token import ensure_integration_token
    ensure_integration_token(app)"

# Register HA Supervisor discovery AFTER init, so the minted token file already
# exists when the publisher reads it. Add-on only (script present + under the
# Supervisor); the standalone image ships neither and skips this.
# Skipping is normal standalone, but it must not be SILENT: "the HA integration
# can't find Edibl" is otherwise unanswerable from the log. Name which of the two
# preconditions failed.
if [ -f /register-discovery.py ] && [ -n "${SUPERVISOR_TOKEN:-}" ]; then
  $RUN_AS python3 /register-discovery.py &
elif [ ! -f /register-discovery.py ]; then
  echo "Edibl: HA discovery skipped (standalone image — no register-discovery.py)."
else
  echo "Edibl: HA discovery skipped (no SUPERVISOR_TOKEN — not running as an HA add-on)."
fi

# MCP server (AI tooling for myMeal / Home Assistant), same container. In hardened
# mode its outbound calls to the REST API need a key — hand it the minted
# integration token (full scope). Open mode reaches the API without one.
MCP_PID=""
if [ "${RESOLVED_MCP_ENABLED}" = "true" ]; then
  MCP_API_TOKEN=""
  if [ "${RESOLVED_DISABLE_AUTH}" != "true" ] && [ -f "${EDIBL_DATA_DIR}/.integration_token" ]; then
    MCP_API_TOKEN="$(cat "${EDIBL_DATA_DIR}/.integration_token" 2>/dev/null || true)"
  fi
  EDIBL_MCP_API_TOKEN="$MCP_API_TOKEN" \
    $RUN_AS python3 /app/backend/edibl_mcp.py &
  MCP_PID=$!
  echo "Edibl MCP server started (pid $MCP_PID) on :${RESOLVED_MCP_PORT}/sse"
fi

# The MCP server was previously backgrounded and then orphaned by `exec gunicorn`,
# so a crashed MCP was invisible and SIGTERM never reached it. Keep a supervising
# shell that forwards signals and reports an MCP exit.
GUNICORN_PID=""
shutdown() {
  [ -n "$GUNICORN_PID" ] && kill -TERM "$GUNICORN_PID" 2>/dev/null || true
  [ -n "$MCP_PID" ] && kill -TERM "$MCP_PID" 2>/dev/null || true
  wait
  exit 0
}
trap shutdown TERM INT

$RUN_AS gunicorn -b "0.0.0.0:${RESOLVED_PORT}" -w 2 --timeout 120 "app:create_app()" &
GUNICORN_PID=$!
echo "Edibl gunicorn on :${RESOLVED_PORT}"

# Supervise. A dead MCP is reported rather than silently absent; a dead gunicorn
# takes the container down with its real exit status.
while true; do
  if [ -n "$MCP_PID" ] && ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "Edibl: WARNING - MCP server (pid $MCP_PID) exited. Assist voice control" \
         "is unavailable; the web app is unaffected." >&2
    MCP_PID=""
  fi
  if ! kill -0 "$GUNICORN_PID" 2>/dev/null; then
    wait "$GUNICORN_PID"
    status=$?
    echo "Edibl: gunicorn exited with status $status; shutting down." >&2
    [ -n "$MCP_PID" ] && kill -TERM "$MCP_PID" 2>/dev/null || true
    exit "$status"
  fi
  sleep 5
done
