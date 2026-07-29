"""add Product.nutrition (JSON) for captured Open Food Facts nutrition

A barcode lookup returns per-100g / per-serving nutrition that Edibl previously threw
away; this column stores the normalized subset so it can be shown per item and summed
across the pantry. Nullable JSON (TEXT on SQLite) — None means "not looked up / unknown".

Idempotent + dialect-safe: skipped when the column already exists (fresh DBs get it from
the 0001 metadata baseline); real downgrade drops it.

Revision ID: 0009_product_nutrition
Revises: 0008_lot_price_numeric
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_product_nutrition"
down_revision = "0008_lot_price_numeric"
branch_labels = None
depends_on = None


def _columns(table) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if "nutrition" not in _columns("products"):
        op.add_column("products", sa.Column("nutrition", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "nutrition" in _columns("products"):
        with op.batch_alter_table("products") as batch:
            batch.drop_column("nutrition")
