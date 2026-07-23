"""SQLite robustness: WAL mode + a consistent whole-DB backup download."""
from sqlalchemy import text


def test_sqlite_wal_is_enabled(app):
    from app.extensions import db
    with app.app_context():
        mode = db.session.execute(text("PRAGMA journal_mode")).scalar()
        assert str(mode).lower() == "wal"
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_backup_db_downloads_a_sqlite_file(auth_client):
    auth_client.post("/api/v1/stock", json={"name": "Milk", "quantity": 1})
    r = auth_client.get("/api/v1/export/backup.db")
    assert r.status_code == 200
    assert r.data[:16] == b"SQLite format 3\x00"   # a real SQLite file
    assert "attachment" in r.headers.get("Content-Disposition", "")
    assert r.headers.get("Content-Type") == "application/x-sqlite3"
