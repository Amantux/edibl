"""SQLite rebuilds a table to change or drop a column, and this app enforces
foreign keys — so any batch_alter on an FK-REFERENCED table fails on a populated
database and leaves an _alembic_tmp corpse that wedges the next boot.

An EMPTY database migrates fine, which is why the rest of the suite missed it:
every test here seeds the referring rows first. Upgrades and downgrades both,
because the downgrades were fixed later and for the same reason.
"""

import os
import sqlite3
import subprocess


# ---- shared harness ----

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


def _seed_at_0007(tmp_path):
    db = str(tmp_path / "m.db")
    r = _alembic(db, "upgrade", "0007_dedupe_product_barcode")
    assert r.returncode == 0, r.stderr[-600:]
    c = sqlite3.connect(db)
    # Force the LEGACY shape: alembic-to-0007 builds cost from the CURRENT model
    # (already Numeric), so 0008's "already converted" guard would skip the
    # batch. A real pre-0008 install had cost as FLOAT — rebuild the column that
    # way (FK off in this local connection) so 0008 actually runs the batch.
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("ALTER TABLE acquisition_lots RENAME COLUMN cost TO _cost_old")
    c.execute("ALTER TABLE acquisition_lots ADD COLUMN cost FLOAT")
    c.execute("ALTER TABLE acquisition_lots DROP COLUMN _cost_old")
    c.execute("PRAGMA foreign_keys=ON")
    _insert(c, "groups", {"id": "g", "name": "G"})
    _insert(c, "products", {"id": "p", "group_id": "g", "name": "Rice"})
    _insert(c, "acquisition_lots", {"id": "al", "group_id": "g", "product_id": "p"})
    # a stock lot referencing the acquisition lot — the FK that breaks the rebuild
    _insert(c, "stock_lots", {"id": "sl", "group_id": "g", "product_id": "p",
                              "acquisition_lot_id": "al"})
    c.commit()
    c.close()
    return db


def test_0008_upgrade_succeeds_on_a_seeded_legacy_db(tmp_path):
    db = _seed_at_0007(tmp_path)
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, f"upgrade failed on seeded data:\n{r.stderr[-800:]}"
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM stock_lots").fetchone()[0] == 1
    # no wedging temp table left behind
    tmp = c.execute("SELECT name FROM sqlite_master WHERE name LIKE "
                    "'_alembic_tmp_%'").fetchall()
    assert tmp == [], f"leftover temp table would wedge the next boot: {tmp}"
    c.close()


# ---- downgrades (0008 / 0009 / 0010) ----

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
