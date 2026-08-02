#!/usr/bin/env sh
# Home Assistant add-on entrypoint: hand off to the app's standard entrypoint
# (config validation, preboot DB init, MCP server, gunicorn).
#
# /data/options.json is deliberately NOT parsed here. The application reads it
# via one tested adapter (app.settings.load_ha_options), so the shell and Python
# cannot disagree about what the configuration is. This file used to translate
# sixteen options into env vars with one `python3 -c` each, via helpers that
# carried two real defects:
#
#   getb() { python3 -c "...print(str(bool(v)).lower())"; }
#
#   * ANY string was truthy, so `disable_auth: "false"` resolved to TRUE and
#     silently disabled authentication.
#   * A MISSING key became false (bool(None)), while config.yaml ships
#     `disable_auth: true` and `mcp_enabled: true` — so a partial options.json
#     put a login screen in front of every HA user behind ingress and turned
#     voice control off.
#
# parse_bool in the registry accepts 1/true/yes/on and 0/false/no/off and
# REJECTS anything else instead of coercing it.
set -e

# The data dir is the one value the shell needs before Python runs.
export EDIBL_DATA_DIR=/data

# Behind HA ingress the requests come from the trusted Supervisor proxy. Not an
# add-on option: it is a fact about the deployment, not a preference.
export EDIBL_PROXY_HOPS="1"

exec /app/docker-entrypoint.sh
