"""add ApiToken.access (write|read) — and backfill the model-only scope column

`access` is the read/write class for API keys: `write` (default, legacy behaviour)
or `read` (read-only — mutating REST + MCP write-tools refused). Orthogonal to scope.

Also adds `scope` (write|read... i.e. full|rest|mcp) which existed only on the model
with no prior migration, so an existing Edibl DB may be missing it. Both are added
idempotently with server_defaults that keep every existing key working exactly as
before (`scope="full"`, `access="write"`).

Idempotent + dialect-safe; real downgrade drops `access` (leaves `scope`, which the
app relies on).

Revision ID: 0012_api_token_access
Revises: 0011_lot_location_estimated
Create Date: 2026-07-31
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_api_token_access"
down_revision = "0011_lot_location_estimated"
branch_labels = None
depends_on = None


def _columns(table) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _columns("api_tokens")
    if not cols:
        return  # table not created yet — the 0001 metadata baseline includes both columns
    if "scope" not in cols:
        op.add_column("api_tokens", sa.Column(
            "scope", sa.String(length=16), nullable=False, server_default="full"))
    if "access" not in cols:
        op.add_column("api_tokens", sa.Column(
            "access", sa.String(length=8), nullable=False, server_default="write"))


def downgrade() -> None:
    if "access" in _columns("api_tokens"):
        with op.batch_alter_table("api_tokens") as batch:
            batch.drop_column("access")
