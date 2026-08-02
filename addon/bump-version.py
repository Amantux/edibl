#!/usr/bin/env python3
"""Increment the add-on's patch version and print the new value. Used by CI to
auto-bump on every release push so Home Assistant always sees an update. Run
from the repo root.

Writes BOTH addon/config.yaml and custom_components/edibl/manifest.json. They
must stay in lockstep: HACS shows the manifest version while the Supervisor shows
the add-on version, so a drift means users report a version that means two
different builds. (They HAD drifted — 1.6.1 vs 1.0.0 — because this script only
ever wrote config.yaml.) CI asserts the two are equal.
"""
import json
import pathlib
import re
import sys

CONFIG = pathlib.Path("addon/config.yaml")
MANIFEST = pathlib.Path("custom_components/edibl/manifest.json")


def main() -> int:
    text = CONFIG.read_text()
    m = re.search(r'^version:\s*"(\d+)\.(\d+)\.(\d+)"', text, re.M)
    if not m:
        print("could not find a semver version in addon/config.yaml", file=sys.stderr)
        return 1
    major, minor, patch = (int(g) for g in m.groups())
    new = f"{major}.{minor}.{patch + 1}"

    CONFIG.write_text(
        re.sub(r'^version:\s*"[\d.]+"', f'version: "{new}"', text, count=1, flags=re.M)
    )

    # Keep the HACS integration in lockstep. Rewritten via json so the file stays
    # valid; hassfest enforces domain/name-then-alphabetical key order, which
    # json.dumps preserves from the existing file.
    manifest = json.loads(MANIFEST.read_text())
    manifest["version"] = new
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")

    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
