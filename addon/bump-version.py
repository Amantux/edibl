#!/usr/bin/env python3
"""Increment the add-on's patch version in addon/config.yaml and print the new
value. Used by CI to auto-bump on every release push so Home Assistant always
sees an update. Run from the repo root."""
import pathlib
import re
import sys

CONFIG = pathlib.Path("addon/config.yaml")


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
    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
