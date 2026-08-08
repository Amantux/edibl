"""money-as-Numeric on lots + a currency on stock_lots

Fulfils the long-standing ``cost: money-as-float; Numeric TODO`` on both
``acquisition_lots`` and ``stock_lots`` by converting the column to
``Numeric(10, 2)`` (Decimal in Python — matching the ``delta_value`` string/Decimal
precedent, never float for money), and adds ``stock_lots.currency`` so a lot records
the household currency it was priced in. This is what the price-capture + spend
dashboard reads.

Idempotent + dialect-safe (SQLite via batch recreate, Postgres via ALTER … TYPE).
On a fresh DB the 0001 metadata baseline already builds the columns with the new
types, so the conversion is a no-op there; the currency add is guarded on presence.

Revision ID: 0008_lot_price_numeric
Revises: 0007_dedupe_product_barcode
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_lot_price_numeric"
down_revision = "0007_dedupe_product_barcode"
branch_labels = None
depends_on = None


def _columns(table) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _type_of(table, column):
    insp = sa.inspect(op.get_bind())
    for c in insp.get_columns(table):
        if c["name"] == column:
            return str(c["type"]).upper()
    return ""


def _fk_safe_sqlite():
    """SQLite batch_alter_table rebuilds a table (create temp, copy, DROP,
    rename). acquisition_lots is FK-referenced by stock_lots and this app
    enforces foreign keys (extensions.py registers the PRAGMA on the Engine
    CLASS, so Alembic's engine gets it too) — so the rebuild's DROP raised
    "FOREIGN KEY constraint failed" on any populated legacy DB, and the leftover
    _alembic_tmp table then wedged every boot. Suspend FK enforcement around the
    rebuild and clean up stale temp tables. Ported from HomeHoard 0014; the
    PRAGMA is a no-op inside a transaction, hence autocommit_block."""
    with op.get_context().autocommit_block():
        for table in ("acquisition_lots", "stock_lots"):
            op.execute(f'DROP TABLE IF EXISTS "_alembic_tmp_{table}"')
        op.execute("PRAGMA foreign_keys=OFF")


def _fk_restore_sqlite():
    with op.get_context().autocommit_block():
        op.execute("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if not is_pg:
        _fk_safe_sqlite()
    try:
        for table in ("acquisition_lots", "stock_lots"):
            if "cost" not in _columns(table):
                continue
            if "NUMERIC" in _type_of(table, "cost"):
                continue  # already converted (fresh DB from metadata baseline)
            if is_pg:
                op.alter_column(
                    table, "cost", type_=sa.Numeric(10, 2),
                    postgresql_using="cost::numeric(10,2)", existing_nullable=True)
            else:
                with op.batch_alter_table(table) as batch:
                    batch.alter_column("cost", type_=sa.Numeric(10, 2),
                                       existing_nullable=True)
    finally:
        if not is_pg:
            _fk_restore_sqlite()

    if "currency" not in _columns("stock_lots"):
        op.add_column("stock_lots",
                      sa.Column("currency", sa.String(length=8), nullable=False,
                                server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if "currency" in _columns("stock_lots"):
        op.drop_column("stock_lots", "currency")

    for table in ("acquisition_lots", "stock_lots"):
        if "cost" not in _columns(table):
            continue
        if is_pg:
            op.alter_column(
                table, "cost", type_=sa.Float(),
                postgresql_using="cost::double precision", existing_nullable=True)
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column("cost", type_=sa.Float(), existing_nullable=True)
