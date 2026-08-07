"""Stock list + dashboard must eager-load lot relations.

stock_out reads s.product / s.location / s.created_by_user / s.acquisition_lot
per lot — all lazy, so listing N lots fired ~2N+ SELECTs. /dashboard is polled
by the HACS coordinator every 300 s, so this N+1 runs unattended forever (the
SOP §3 incident class).
"""
from sqlalchemy import event

from app.extensions import db


def _loc(c, name="Pantry"):
    return c.post("/api/v1/locations", json={"name": name, "kind": "pantry"}).get_json()


def _add(c, name, loc_id):
    return c.post("/api/v1/stock",
                  json={"name": name, "quantity": 1, "unit": "kg",
                        "locationId": loc_id}).get_json()


def _count_queries(app, fn):
    n = {"q": 0}

    def rec(conn, cursor, statement, params, context, executemany):
        low = statement.lower()
        if low.startswith("select"):
            import re
            for t in re.findall(r"from (\w+)", low):
                if t in ("products", "acquisition_lots", "locations", "users"):
                    n["q"] += 1

    with app.app_context():
        engine = db.engine
    # Drop the scoped session so the measured request builds a COLD identity
    # map — otherwise the seeding session caches every product and the N+1 is
    # invisible (a real request from a browser/coordinator is always cold).
    with app.app_context():
        db.session.remove()
    event.listen(engine, "before_cursor_execute", rec)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", rec)
    return n["q"]


def _seed(c, k):
    loc = _loc(c)
    for i in range(k):
        _add(c, f"Item{i:02d}", loc["id"])


def test_stock_list_does_not_lazy_load_each_lot(auth_client, app):
    _seed(auth_client, 12)
    q = _count_queries(app, lambda: auth_client.get("/api/v1/stock").get_json())
    # eager-loaded: a small constant (one query per relation), not ~2N.
    assert q <= 6, f"{q} relation SELECTs listing 12 lots (N+1)"


def test_dashboard_does_not_lazy_load_each_lot(auth_client, app):
    _seed(auth_client, 12)
    q = _count_queries(app, lambda: auth_client.get("/api/v1/dashboard").get_json())
    assert q <= 8, f"{q} relation SELECTs on the machine-polled dashboard (N+1)"
