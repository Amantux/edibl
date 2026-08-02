"""normalize existing units + backfill generic families (data migration)

Standardizes data that predates unit canonicalization / generic grouping:
  * StockLot.unit and Product.default_unit → the canonical spelling (so 'Units',
    'pieces', 'Piece', 'Ounce'… all collapse to one token).
  * Product.family (the grocery group) → a brand-stripped generic where a known
    store/house brand can be stripped from the name ('Wegmans Teriyaki Marinade'
    → 'Teriyaki Marinade'); products with no derivable generic are left as-is for
    the LLM backfill endpoint. Never overwrites an existing family.

Data-only and re-runnable: a second pass finds everything already canonical and
changes nothing. Imports the same pure helpers the app uses at write time.

Revision ID: 0003_normalize_units_families
Revises: 0002_add_product_search_text
Create Date: 2026-07-26

NOTE: the revision id is deliberately shorter than this file's name. Alembic's
`alembic_version.version_num` column is VARCHAR(32) (hardcoded in alembic's
ddl/impl.py — there is no length option), and the original id
"0003_normalize_units_and_families" is 33 characters. SQLite does not enforce
VARCHAR lengths, so this only ever failed on PostgreSQL, where the upgrade died
with StringDataRightTruncation and no database could be created at all. Keep
every revision id at 32 characters or fewer.
"""
import sqlalchemy as sa
from alembic import op

from app.services.units import canonical_unit
from app.services.families import generic_family

revision = "0003_normalize_units_families"
down_revision = "0002_add_product_search_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    for table, col in (("stock_lots", "unit"), ("products", "default_unit")):
        rows = conn.execute(sa.text(
            f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")).fetchall()
        for _id, raw in rows:
            canon = canonical_unit(raw)
            if canon != raw:
                conn.execute(sa.text(f"UPDATE {table} SET {col} = :u WHERE id = :i"),
                             {"u": canon, "i": _id})

    # Backfill a generic family only where it's currently empty and a brand can be
    # stripped deterministically. The LLM backfill endpoint handles the rest.
    prods = conn.execute(sa.text(
        "SELECT id, name FROM products "
        "WHERE family IS NULL OR family = ''")).fetchall()
    for _id, name in prods:
        fam = generic_family(name)
        if fam and fam.lower() != (name or "").lower():
            conn.execute(sa.text("UPDATE products SET family = :f WHERE id = :i"),
                         {"f": fam, "i": _id})


def downgrade() -> None:
    # Data normalization has no meaningful inverse (the original spellings are lost).
    pass
