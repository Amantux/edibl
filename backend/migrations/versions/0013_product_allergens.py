"""add Product.label_allergens (+ ruleset version / source) for scanned label allergens

A barcode scan already read Open Food Facts' `allergens_tags` and `traces_tags` and
turned them into foodcore's four-state verdicts, but there was nowhere to put them: the
`found: true` branch of GET /products/barcode/<code> serialises a Product, so a code you
already own came back with NO allergen data while an unknown code came back WITH it.
Scanning something in your own pantry told you less than scanning something new, which
is the one shape that makes `unknown` read as "nothing to worry about" (ADR-0005).

Three columns, because a verdict is only interpretable with its ruleset:
  * `label_allergens`   JSON, NULL = never screened. A screened product stores an
                        explicit verdict for EVERY ruleset key, so a label declaring
                        nothing is twelve `unknown` verdicts — never an empty list.
                        NULL and "all unknown" are different answers and both must
                        stay expressible.
  * `allergen_ruleset_version`  the ruleset that produced them. A verdict whose rule
                        version is unknown cannot be re-interpreted later and is worse
                        than no verdict; the reader treats a blank version as NOT
                        screened rather than as data.
  * `allergen_source`   where they came from ("openfoodfacts"); "" = nothing screened.

The two string columns carry `server_default=""` so a `create_all` database (0001
metadata baseline) and a migrated one agree: without it, existing rows would read NULL
while new rows read "", and the never-screened test would depend on which path built
the table.

Idempotent + dialect-safe: each column is skipped when already present; real downgrade
drops all three.

Revision ID: 0013_product_allergens
Revises: 0012_api_token_access
Create Date: 2026-10-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_product_allergens"
down_revision = "0012_api_token_access"
branch_labels = None
depends_on = None

_ADDED = ("label_allergens", "allergen_ruleset_version", "allergen_source")


def _columns(table) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _columns("products")
    if not cols:
        return  # table not created yet — the 0001 metadata baseline includes all three
    if "label_allergens" not in cols:
        op.add_column("products", sa.Column("label_allergens", sa.JSON(), nullable=True))
    if "allergen_ruleset_version" not in cols:
        op.add_column("products", sa.Column(
            "allergen_ruleset_version", sa.String(length=64),
            nullable=False, server_default=""))
    if "allergen_source" not in cols:
        op.add_column("products", sa.Column(
            "allergen_source", sa.String(length=32),
            nullable=False, server_default=""))


def downgrade() -> None:
    present = [c for c in _ADDED if c in _columns("products")]
    if not present:
        return
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in present:
            op.drop_column("products", name)
        return
    # SQLite batch_alter_table rebuilds `products`, which several tables FK-reference
    # under this app's enforced foreign keys — so the rebuild's DROP raises
    # "FOREIGN KEY constraint failed" on any populated DB and leaves an _alembic_tmp
    # corpse behind. Suspend enforcement around it (PRAGMA is a no-op inside a
    # transaction, hence autocommit_block). Same dance as 0009's downgrade.
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
