"""Downgrades that batch-rebuild an FK-referenced table on SQLite must suspend
foreign-key enforcement, exactly as the upgrades do. 0008 (acquisition_lots,
referenced by stock_lots), 0009 (products, 6 referrers) and 0010 (locations)
each DROP a column via a table rebuild; under this app's enforced FKs the
rebuild's table-drop raised "FOREIGN KEY constraint failed" on any populated DB
and left an _alembic_tmp corpse that wedged the next boot. An empty DB hid it.
"""
import os
import sqlite3
import subprocess


def _alembic(db, *args):
    env = dict(os.environ, EDIBL_DATABASE_URL=f"sqlite:///{db}")
    return subprocess.run(["python3", "-m", "alembic", *args],
                          capture_output=True, text=True, env=env,
                          cwd=os.path.dirname(os.path.dirname(__file__)))


def _insert(conn, table, values):
    row = dict(values)
    for _cid, name, ctype, notnull, default, _pk in conn.execute(
            f"PRAGMA table_info({table})"):
        if name in row or not notnull or default is not None:
            continue
        if name in ("created_at", "updated_at"):
            row[name] = "2026-01-01 00:00:00"
        elif any(t in (ctype or "").upper() for t in ("INT", "REAL", "FLOA", "NUM")):
            row[name] = 0
        else:
            row[name] = ""
    cols = ",".join(row)
    marks = ",".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))


def _seed_head(tmp_path):
    """A head DB with the inbound-FK rows that make each rebuild dangerous."""
    db = str(tmp_path / "d.db")
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-800:]
    c = sqlite3.connect(db)
    _insert(c, "groups", {"id": "g", "name": "G"})
    _insert(c, "locations", {"id": "loc", "group_id": "g", "name": "Fridge"})
    _insert(c, "products", {"id": "p", "group_id": "g", "name": "Milk"})
    _insert(c, "acquisition_lots", {"id": "al", "group_id": "g", "product_id": "p"})
    # references products, locations AND acquisition_lots — the three referrers
    _insert(c, "stock_lots", {"id": "sl", "group_id": "g", "product_id": "p",
                              "location_id": "loc", "acquisition_lot_id": "al"})
    c.commit()
    c.close()
    return db


def _assert_clean(db, expect_stock=True):
    c = sqlite3.connect(db)
    if expect_stock:
        assert c.execute("SELECT COUNT(*) FROM stock_lots").fetchone()[0] == 1
    tmp = c.execute("SELECT name FROM sqlite_master WHERE name LIKE "
                    "'_alembic_tmp_%'").fetchall()
    c.close()
    assert tmp == [], f"leftover temp table wedges the next boot: {tmp}"


def test_downgrade_0010_drops_location_description_on_seeded_fk(tmp_path):
    db = _seed_head(tmp_path)
    r = _alembic(db, "downgrade", "0009_product_nutrition")   # runs 0010 down
    assert r.returncode == 0, f"0010 downgrade failed:\n{r.stderr[-800:]}"
    _assert_clean(db)


def test_downgrade_0009_drops_product_nutrition_on_seeded_fk(tmp_path):
    db = _seed_head(tmp_path)
    r = _alembic(db, "downgrade", "0008_lot_price_numeric")   # runs 0010+0009 down
    assert r.returncode == 0, f"0009 downgrade failed:\n{r.stderr[-800:]}"
    _assert_clean(db)


def test_downgrade_0008_rebuilds_acquisition_lots_on_seeded_fk(tmp_path):
    db = _seed_head(tmp_path)
    r = _alembic(db, "downgrade", "0007_dedupe_product_barcode")  # 0010+0009+0008
    assert r.returncode == 0, f"0008 downgrade failed:\n{r.stderr[-800:]}"
    _assert_clean(db)
