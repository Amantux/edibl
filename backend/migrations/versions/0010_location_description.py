"""add Location.description — "what this area normally stores"

Drives smart default placement (milk→fridge, ice cream→freezer) and is AI-assisted /
generated + refined via the chat bot. Nullable-safe: a plain TEXT column defaulting to
"".

Idempotent + dialect-safe: skipped when the column already exists (fresh DBs get it from
the 0001 metadata baseline); real downgrade drops it.

Revision ID: 0010_location_description
Revises: 0009_product_nutrition
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_location_description"
down_revision = "0009_product_nutrition"
branch_labels = None
depends_on = None


def _columns(table) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if "description" not in _columns("locations"):
        op.add_column("locations",
                      sa.Column("description", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    if "description" in _columns("locations"):
        with op.batch_alter_table("locations") as batch:
            batch.drop_column("description")
