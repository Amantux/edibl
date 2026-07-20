#!/usr/bin/env bash
# Regenerate the vendored frontend the add-on ships (so ARM devices don't run
# npm). Run from the repo root whenever the frontend changes, then commit
# addon/webroot/.
set -euo pipefail
cd "$(dirname "$0")/.."
npm --prefix frontend run build
rm -rf addon/webroot
cp -r frontend/dist addon/webroot
echo "Rebuilt addon/webroot — commit it."
