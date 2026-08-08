"""Migration 0008 (cost->Numeric) must not crash on a POPULATED legacy SQLite
install. It batch-rebuilds acquisition_lots, which stock_lots FK-references
under enforced foreign keys — so the rebuild's table drop raised "FOREIGN KEY
constraint failed", and the leftover _alembic_tmp table then wedged every boot.
An empty DB migrates fine, which is why the suite missed it.
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
