"""dedupe products by (group_id, barcode) + add a partial unique index

Two products in one household sharing a barcode is the ingestion-dedup bug. This
migration (a) MERGES any existing duplicate-barcode products within a group onto
the earliest one — re-pointing every child row (lots, acquisitions, shopping,
reservations, consumption, detection matches) FIRST so a lot is never orphaned —
then (b) creates a partial unique index on (group_id, barcode) WHERE barcode<>''.

Idempotent + dialect-safe (SQLite + Postgres): the metadata baseline (0001)
create_all already builds the index on fresh DBs, so the index step is skipped
when it already exists; the merge is a no-op when there are no duplicates.

Revision ID: 0007_dedupe_product_barcode
Revises: 0006_add_ai_suggestions
Create Date: 2026-07-29
"""
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "0007_dedupe_product_barcode"
down_revision = "0006_add_ai_suggestions"
branch_labels = None
depends_on = None

_INDEX = "uq_products_group_barcode"
# Tables whose product_id FKs must be re-pointed before a duplicate is deleted.
_CHILD_PRODUCT_FK = ("acquisition_lots", "stock_lots", "shopping_items",
                     "reservations", "consumption_events")


def _has_index(table, name) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    return name in {ix["name"] for ix in insp.get_indexes(table)}


def _merge_duplicates(bind) -> None:
    rows = bind.execute(sa.text(
        "SELECT id, group_id, barcode FROM products "
        "WHERE barcode IS NOT NULL AND barcode <> '' "
        "ORDER BY group_id, barcode, created_at, id"
    )).fetchall()
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r.group_id, r.barcode)].append(r.id)

    for ids in by_key.values():
        if len(ids) < 2:
            continue
        keep, dupes = ids[0], ids[1:]
        for dupe in dupes:
            for table in _CHILD_PRODUCT_FK:
                bind.execute(
                    sa.text(f"UPDATE {table} SET product_id = :keep WHERE product_id = :dupe"),
                    {"keep": keep, "dupe": dupe})
            bind.execute(
                sa.text("UPDATE detections SET matched_product_id = :keep "
                        "WHERE matched_product_id = :dupe"),
                {"keep": keep, "dupe": dupe})
            # ai_suggestions.product_id is ON DELETE CASCADE, but drop them explicitly
            # so the merge is deterministic even if FK enforcement is off mid-migration
            # (a pending suggestion for a merged-away duplicate is discarded, not moved).
            bind.execute(sa.text("DELETE FROM ai_suggestions WHERE product_id = :dupe"),
                         {"dupe": dupe})
            bind.execute(sa.text("DELETE FROM products WHERE id = :dupe"), {"dupe": dupe})


def upgrade() -> None:
    bind = op.get_bind()
    _merge_duplicates(bind)  # must run before the unique index so it can't conflict
    if not _has_index("products", _INDEX):
        op.create_index(
            _INDEX, "products", ["group_id", "barcode"], unique=True,
            sqlite_where=sa.text("barcode != ''"),
            postgresql_where=sa.text("barcode != ''"))


def downgrade() -> None:
    # The dupe merge deletes rows and is NOT reversible; dropping the index is the
    # only meaningful downgrade.
    if _has_index("products", _INDEX):
        op.drop_index(_INDEX, table_name="products")
