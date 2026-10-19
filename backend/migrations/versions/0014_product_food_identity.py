"""add Product.food_key + food_key_dataset_version — a persisted food identity

`quantity.convert` refuses mass<->volume because it has no food and therefore no
density (ADR-0002). `convert_for_food` can answer, but only given a SPECIFIC food —
and the only food name available at a deduction is the product's free text, which
resolving there would make a guess at the worst possible moment (ADR-0003 reserves
that for the matching service). These two columns are the prerequisite ADR-0005
named: an identity chosen ONCE, deliberately, and looked up thereafter by exact key.

`food_key` is a foodcore dataset key ("usda:168384"); `food_key_dataset_version` is
the snapshot that resolved it ("SR Legacy 2018-04"), so a later dataset rebuild is
detectable instead of silently re-pointing an existing key.

Both default to "" — meaning NO identity — so every existing product keeps exactly
today's behaviour (fall back to `convert`, and to its refusal). That is the whole
point of the default: a guessed identity is worse than none.

Idempotent + dialect-safe: skipped when the columns already exist (fresh DBs get
them from the 0001 metadata baseline); real downgrade drops both.

Revision ID: 0014_product_food_identity
Revises: 0013_product_allergens
Create Date: 2026-10-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_product_food_identity"
down_revision = "0013_product_allergens"
branch_labels = None
depends_on = None

_COLUMNS = ("food_key", "food_key_dataset_version")


def _columns(table) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _columns("products")
    if not cols:
        return  # table not created yet — the 0001 metadata baseline includes both
    if "food_key" not in cols:
        op.add_column("products", sa.Column(
            "food_key", sa.String(length=128), nullable=False, server_default=""))
    if "food_key_dataset_version" not in cols:
        op.add_column("products", sa.Column(
            "food_key_dataset_version", sa.String(length=64), nullable=False,
            server_default=""))


def downgrade() -> None:
    cols = _columns("products")
    present = [c for c in _COLUMNS if c in cols]
    if not present:
        return
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in present:
            op.drop_column("products", name)
        return
    # SQLite batch_alter_table rebuilds `products`, which several tables
    # FK-reference under this app's enforced foreign keys — so the rebuild's
    # DROP raises "FOREIGN KEY constraint failed" on any populated DB and leaves
    # an _alembic_tmp corpse behind. Suspend enforcement around it (PRAGMA is a
    # no-op inside a transaction -> autocommit_block). Same dance as 0009.
    with op.get_context().autocommit_block():
        op.execute('DROP TABLE IF EXISTS "_alembic_tmp_products"')
        op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("products") as batch:
            for name in present:
                batch.drop_column(name)
    finally:
        with op.get_context().autocommit_block():
            op.execute("PRAGMA foreign_keys=ON")
