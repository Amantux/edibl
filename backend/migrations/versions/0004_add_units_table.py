"""add units table (user-managed units of measure; myMeal-shared vocabulary)

Idempotent create: the metadata baseline (0001) create_all already builds this
table on fresh/newly-adopted databases, so this delta only adds it to databases
stamped before the model existed. Dialect-safe (SQLite + Postgres).

Revision ID: 0004_add_units_table
Revises: 0003_normalize_units_and_families
Create Date: 2026-07-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_add_units_table"
down_revision = "0003_normalize_units_and_families"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "units" in insp.get_table_names():
        return
    op.create_table(
        "units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("plural_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("abbreviation", sa.String(32), nullable=False, server_default=""),
        sa.Column("dimension", sa.String(16), nullable=False, server_default=""),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("group_id", "name", name="uq_units_group_name"),
    )
    op.create_index("ix_units_name", "units", ["name"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "units" in insp.get_table_names():
        op.drop_table("units")
