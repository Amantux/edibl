"""Reprocess existing inventory with the improved ingestion logic.

Items added before the ingestion fixes are often stuck in category ""/"other"
(old scans skipped classification) with a fallback expiry and no display family.
This walks the group's products and corrects those — WITHOUT ever overwriting a
value the user set explicitly:

- category "" or "other"  → re-classify with the same classifier the manual add
  uses (skips a result that's still "other").
- family missing          → assign a generic family from the name.
- ESTIMATED lot expiries of a just-re-classified product → recomputed from the
  corrected category. A user-set (non-estimated) expiry is never touched.

Idempotent: a second run finds nothing to fix. Commits per product (via bump) so
progress + partial results survive a worker kill.
"""
from ..extensions import db
from ..models import Product, StockLot
from .assistant import classify_food
from .estimation import estimate_expiry
from .families import generic_family
from .jobs import bump

_STALE = ("", "other")


def run_reprocess(job) -> dict:
    gid = job.group_id
    products = (db.session.query(Product)
                .filter(Product.group_id == gid)
                .order_by(Product.created_at.asc()).all())
    bump(job, done=0, total=len(products))
    reclassified = family_assigned = expiry_updated = 0

    for i, p in enumerate(products, 1):
        # 1) family — only when missing (never override a user/AI-set family).
        if not (p.family or "").strip():
            fam = generic_family(p.name)
            if fam:
                p.family = fam
                family_assigned += 1

        # 2) category — only when empty/other (never override an explicit category).
        recategorized = False
        if (p.category or "").strip().lower() in _STALE:
            c = classify_food(p.name)
            new_cat = (c.get("category") or "").strip()
            if new_cat and new_cat != "other" and new_cat != p.category:
                p.category = new_cat
                reclassified += 1
                recategorized = True
                if not (p.family or "").strip() and c.get("family"):
                    p.family = c["family"]

        # 3) recompute ESTIMATED expiries of this product's lots from the new category.
        #    expiry_estimated is False for a printed/user date, so those stay put.
        if recategorized:
            lots = (db.session.query(StockLot)
                    .filter(StockLot.product_id == p.id,
                            StockLot.expiry_estimated.is_(True)).all())
            for lot in lots:
                exp, est = estimate_expiry(lot.purchase_date, p.category, lot.storage_method,
                                           group_id=gid, product_id=p.id)
                if exp and lot.expiry_date != exp:
                    lot.expiry_date = exp
                    lot.expiry_estimated = est
                    expiry_updated += 1

        bump(job, done=i)  # commits this product's changes + progress + heartbeat

    return {"reclassified": reclassified, "familyAssigned": family_assigned,
            "expiryUpdated": expiry_updated, "scanned": len(products)}
