"""Core / "always in stock" items → keep the shopping list in sync.

A product marked `staple` should auto-appear on the shopping list the moment its
on-hand drops to/below its reorder threshold ("low" or "empty"), and disappear again
once it's restocked — without ever touching a line the user added or already checked
off. The auto-added rows carry `source='auto'` so they're the only ones this code will
remove.

The "is it low" decision is delegated to `reorder.reorder_state` so chat, the Home
Assistant restock feed, and this auto-list all agree on one threshold rule.
"""
from ..extensions import db
from ..models import Product, ShoppingItem

_AUTO_SOURCE = "auto"          # marks a row THIS code created (safe to auto-remove)
_OPEN_STATUS = "needed"        # an unbought/open shopping row


def _norm(s):
    return (s or "").strip().lower()


def sync_staple_shopping(gid, product_id=None):
    """Reconcile auto shopping rows against current stock for a group's staples.

    Scope to one product with `product_id` (cheap, called after a single stock change);
    omit it for a full-group sweep (the manual "sync now" path).
      * low staple with NO open row (product-linked OR a name match) → add one
        `source='auto'` row;
      * restocked staple → delete its open `source='auto'` row(s);
      * never touches a user-added or already-checked row.
    Converges to a single auto row per product: an open row a user typed by name (no
    productId) blocks the auto-add, and a duplicate auto row (e.g. a concurrent double
    sync) is collapsed to one on the next run. Returns {"added": n, "removed": n}.
    """
    from .reorder import reorder_state, _reserved_by_product

    q = db.session.query(Product).filter_by(group_id=gid, staple=True)
    if product_id is not None:
        q = q.filter(Product.id == product_id)
    staples = [p for p in q.all() if not p.do_not_suggest]
    if not staples:
        return {"added": 0, "removed": 0}

    reserved = _reserved_by_product(gid)
    pids = set(p.id for p in staples)
    name_to_pid = {_norm(p.name): p.id for p in staples}
    # Gather every open row for the group and attribute it to a staple by product_id,
    # else by name — so "Milk" typed onto the list still counts as tracking the Milk
    # staple even though it carries no productId (shopping.py allows name-only rows).
    open_by_pid = {}
    for it in (db.session.query(ShoppingItem)
               .filter(ShoppingItem.group_id == gid,
                       ShoppingItem.status == _OPEN_STATUS).all()):
        pid = it.product_id if it.product_id in pids else name_to_pid.get(_norm(it.name))
        if pid in pids:
            open_by_pid.setdefault(pid, []).append(it)

    added = removed = 0
    for p in staples:
        st = reorder_state(p, reserved.get(p.id, 0))
        if st is None:
            continue
        existing = open_by_pid.get(p.id, [])
        autos = [it for it in existing if it.source == _AUTO_SOURCE]
        if st["isLow"]:
            if not existing:
                # Nothing open tracks this product (any source) → add one auto row.
                db.session.add(ShoppingItem(
                    name=p.name, quantity=st["suggestedQuantity"] or 1,
                    unit=p.default_unit or "count", source=_AUTO_SOURCE,
                    product_id=p.id, group_id=gid))
                added += 1
            elif len(autos) > 1:
                # A concurrent double-add slipped through the check-then-insert window;
                # keep one, drop the rest so we converge to a single auto row.
                for it in autos[1:]:
                    db.session.delete(it)
                    removed += 1
        else:
            # Restocked above threshold → retract only what we auto-added.
            for it in autos:
                db.session.delete(it)
                removed += 1

    if added or removed:
        db.session.commit()
    return {"added": added, "removed": removed}


def retract_auto(gid, product_id):
    """Remove any open `source='auto'` rows for one product regardless of its stock or
    staple flag — used when a product is UN-marked as a staple, so its auto row doesn't
    linger on the list (sync_staple_shopping only looks at current staples)."""
    n = (db.session.query(ShoppingItem)
         .filter(ShoppingItem.group_id == gid, ShoppingItem.product_id == product_id,
                 ShoppingItem.status == _OPEN_STATUS,
                 ShoppingItem.source == _AUTO_SOURCE)
         .delete(synchronize_session=False))
    if n:
        db.session.commit()
    return {"added": 0, "removed": n}


def safe_sync(gid, product_id=None):
    """Best-effort wrapper: a shopping-list hiccup must never break the stock
    mutation that triggered it. Rolls back its own partial work on error."""
    try:
        return sync_staple_shopping(gid, product_id=product_id)
    except Exception:  # noqa: BLE001 - side effect; caller's commit already landed
        db.session.rollback()
        return {"added": 0, "removed": 0}
