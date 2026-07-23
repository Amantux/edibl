"""End-to-end journey tests: boot the real backend serving the built SPA, drive it
with a headless browser. Run with `pytest e2e` from the repo root AFTER building the
frontend (`npm --prefix frontend run build`). Chromium comes from Playwright.

These cover the user-facing flows the unit/API tests can't: the browser actually
clicking through add→classify, consume→undo, mark-low, reconcile, and review."""
import os
import socket
import subprocess
import tempfile
import time
import urllib.request

import pytest
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def app_url():
    port = _free_port()
    db = os.path.join(tempfile.mkdtemp(), "e2e.db")
    env = {**os.environ, "EDIBL_DISABLE_AUTH": "1", "EDIBL_SEED_DEFAULTS": "1",
           "EDIBL_PORT": str(port), "EDIBL_DATABASE_URL": f"sqlite:///{db}",
           "EDIBL_FRONTEND_DIST": os.path.join(ROOT, "frontend", "dist")}
    proc = subprocess.Popen(["python3", "run.py"], cwd=os.path.join(ROOT, "backend"),
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/api/v1/meta", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("edibl backend did not start for e2e")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        proc.kill()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 1100, "height": 900})
    pg = ctx.new_page()
    yield pg
    ctx.close()
