"""add StockLot.location_estimated — flags a low-confidence auto-placed location

At ingestion Edibl auto-places a lot in a suggested area; a LOW-confidence guess is
marked so the UI can flag it (distinct colour) and surface it in a "to place" review
list. Cleared when the user confirms or changes the location.

Idempotent + dialect-safe: skipped when the column already exists (fresh DBs get it
from the 0001 metadata baseline); real downgrade drops it.

Revision ID: 0011_lot_location_estimated
Revises: 0010_location_description
Create Date: 2026-07-30
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_lot_location_estimated"
down_revision = "0010_location_description"
branch_labels = None
depends_on = None


def _columns(table) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if "location_estimated" not in _columns("stock_lots"):
        op.add_column("stock_lots", sa.Column(
            "location_estimated", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    if "location_estimated" in _columns("stock_lots"):
        with op.batch_alter_table("stock_lots") as batch:
            batch.drop_column("location_estimated")
