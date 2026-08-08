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
    if "description" not in _columns("locations"):
        return
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_column("locations", "description")
        return
    # SQLite batch_alter_table rebuilds `locations`, which stock_lots and
    # locations.parent_id FK-reference under enforced foreign keys — so the
    # rebuild's DROP raised "FOREIGN KEY constraint failed" on a populated DB
    # and left an _alembic_tmp corpse. Suspend enforcement around it (PRAGMA is
    # a no-op inside a transaction → autocommit_block).
    with op.get_context().autocommit_block():
        op.execute('DROP TABLE IF EXISTS "_alembic_tmp_locations"')
        op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("locations") as batch:
            batch.drop_column("description")
    finally:
        with op.get_context().autocommit_block():
            op.execute("PRAGMA foreign_keys=ON")
