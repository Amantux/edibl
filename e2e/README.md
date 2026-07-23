# End-to-end journey tests

Playwright drives the **real, built app** (backend serving the SPA) through the core
user flows the unit/API tests can't see — add→classify, use→undo, the produce
ripeness scale, the Dashboard add button, and no-horizontal-overflow at tablet width.

## Run locally

```bash
# from the repo root
npm --prefix frontend run build          # the tests serve frontend/dist
pip install -r e2e/requirements.txt
python -m playwright install chromium     # first time only
pytest e2e -q
```

`e2e/conftest.py` boots `backend/run.py` on a free port with a throwaway SQLite DB
(`EDIBL_DISABLE_AUTH=1`, `EDIBL_SEED_DEFAULTS=1`) and points a headless Chromium at it.
It runs in CI as the **E2E** job in `.github/workflows/ci.yml`.

Add a new journey by dropping a `test_*` function in `e2e/test_journeys.py` — each
gets a fresh browser `page` and the shared `app_url`.
