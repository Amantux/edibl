"""Inventory commands — the only code that mutates stock + writes the ledger.

Slice scope (Phase 1): add / open / consume / reverse (+ the migration's `import`).
Later phases add waste/adjust/move/split/merge/freeze/thaw/transform on the same
spine. Each command:
  * is transactional (one commit),
  * is idempotency-keyed (a retried command returns the same event, never doubles) —
    enforced at the DB (unique constraints) AND replayed on a concurrent collision,
  * appends an immutable `InventoryEvent`,
  * returns a `CommandResult` with an event id, a plain-language summary, and an
    `undo` descriptor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.exc import IntegrityError

from ...extensions import db
from ...models import (StockLot, ConsumptionEvent, InventoryEvent, utcnow,
                       OUTCOMES, LOSS_OUTCOMES)

# Event types a reversal knows how to compensate. Reversing anything else (add,
# import, or a reverse itself) is rejected rather than silently no-op'd.
REVERSIBLE_TYPES = frozenset({"consume", "open", "adjust", "move", "split", "merge"})
_NUMERIC_KINDS = frozenset({"exact", "estimated", "approximate"})


@dataclass
class CommandResult:
    lot: Optional[StockLot]
    event: Optional[InventoryEvent]
    summary: str
    extra: dict          # command-specific payload (consumptionId, consumedAmount, insight, …)
    idempotent_replay: bool = False

    @property
    def undo(self) -> Optional[dict]:
        """Undo descriptor: reverse THIS event (a new compensating event)."""
        if not self.event:
            return None
        return {"op": "reverse_event", "eventId": self.event.id}


class UnsupportedReversal(ValueError):
    """Raised when asked to reverse an event type the ledger can't compensate."""


def _existing_event(group_id: str, key: Optional[str]) -> Optional[InventoryEvent]:
    if not key:
        return None
    return (db.session.query(InventoryEvent)
            .filter_by(group_id=group_id, idempotency_key=key).first())


def _reversal_of(event_id: str) -> Optional[InventoryEvent]:
    return db.session.query(InventoryEvent).filter_by(reversal_of=event_id).first()


def _emit(group_id, type_, *, actor_user_id=None, source_app="web", summary="",
          src=None, dst=None, delta_value=None, delta_unit="", state_changes=None,
          provenance="manual", confidence=None, reason="", reversal_of=None,
          idempotency_key=None) -> InventoryEvent:
    ev = InventoryEvent(
        type=type_, group_id=group_id, actor_user_id=actor_user_id,
        source_app=source_app, summary=summary,
        src_position_id=src, dst_position_id=dst,
        delta_value=(None if delta_value is None else str(delta_value)),
        delta_unit=delta_unit, state_changes=state_changes or {},
        provenance=provenance, confidence=confidence, reason=reason,
        reversal_of=reversal_of, idempotency_key=idempotency_key)
    db.session.add(ev)
    db.session.flush()
    return ev


def _commit_or_replay(replay):
    """Commit; on a unique-constraint race (a concurrent duplicate landed first),
    roll back this attempt and return the winner's result via `replay()`. Returns
    None on a clean commit, or the replay CommandResult on collision."""
    try:
        db.session.commit()
        return None
    except IntegrityError:
        db.session.rollback()
        result = replay()
        if result is not None:
            return result
        raise


def _days_kept(purchase) -> Optional[int]:
    if not purchase:
        return None
    p = purchase.replace(tzinfo=None) if purchase.tzinfo else purchase
    return max((datetime.utcnow() - p).days, 0)


def _label(lot: StockLot) -> str:
    name = lot.product.name if lot.product else "item"
    return f"{lot.quantity} {lot.unit} of {name}"


def _numeric(lot: StockLot) -> bool:
    return (getattr(lot, "quantity_kind", "exact") or "exact") in _NUMERIC_KINDS


# --------------------------------------------------------------------------- #
# add
# --------------------------------------------------------------------------- #
def add_lot(lot: StockLot, *, actor_user_id=None, source_app="web",
            provenance="manual", confidence=None, idempotency_key=None,
            reason="") -> CommandResult:
    """Persist a freshly-built (not-yet-added) StockLot and log an `add` event."""
    gid = lot.group_id

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is None:
            return None
        existing = db.session.get(StockLot, prior.dst_position_id) if prior.dst_position_id else None
        return CommandResult(existing, prior, prior.summary,
                             {"lotId": prior.dst_position_id}, idempotent_replay=True)

    replayed = replay()
    if replayed is not None:
        return replayed

    lot.provenance = provenance
    lot.confidence = confidence
    db.session.add(lot)
    db.session.flush()
    summary = f"Added {_label(lot)}"
    # A non-numeric (presence/unknown) add carries NO quantity delta — never a fake 1.
    delta = lot.quantity if _numeric(lot) else None
    ev = _emit(gid, "add", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, dst=lot.id, delta_value=delta,
               delta_unit=lot.unit, provenance=provenance, confidence=confidence,
               reason=reason, idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision
    return CommandResult(lot, ev, summary, {"lotId": lot.id})


# --------------------------------------------------------------------------- #
# open
# --------------------------------------------------------------------------- #
def open_lot(lot: StockLot, *, actor_user_id=None, source_app="web",
             idempotency_key=None) -> CommandResult:
    """Mark a package opened (orthogonal facet). No-op if already open."""
    gid = lot.group_id

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is None:
            return None
        return CommandResult(lot, prior, prior.summary, {}, idempotent_replay=True)

    replayed = replay()
    if replayed is not None:
        return replayed

    if lot.package_state == "opened":
        return CommandResult(lot, None, f"{_label(lot)} is already open", {})
    was = lot.package_state or "sealed"
    lot.package_state = "opened"
    if not lot.opened_date:
        lot.opened_date = utcnow()
    name = lot.product.name if lot.product else "item"
    summary = f"Opened {name}"
    ev = _emit(gid, "open", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, src=lot.id,
               state_changes={"package_state": [was, "opened"]},
               idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision
    return CommandResult(lot, ev, summary, {})


# --------------------------------------------------------------------------- #
# consume
# --------------------------------------------------------------------------- #
def consume_lot(lot: StockLot, *, amount=None, outcome="eaten", freshness=None,
                actor_user_id=None, source_app="web", idempotency_key=None,
                reason=None) -> CommandResult:
    """Resolve some/all of a lot with an outcome. Canonical semantics (shared by
    REST + assistant): **amount omitted ⇒ the whole lot**. Writes BOTH a
    ConsumptionEvent (shelf-life learning + legacy undo) and an InventoryEvent."""
    from ...services.estimation import product_insights

    gid = lot.group_id

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is None:
            return None
        return CommandResult(
            lot, prior, prior.summary,
            {"consumptionId": (prior.state_changes or {}).get("consumption_event_id"),
             "consumedAmount": abs(float(prior.delta_value or 0))},
            idempotent_replay=True)

    replayed = replay()
    if replayed is not None:
        return replayed

    o = (outcome or "eaten").strip().lower()
    if o not in OUTCOMES:
        legacy = (reason or "used").strip().lower()
        o = {"used": "eaten", "expired": "expired", "discarded": "discarded"}.get(legacy, "eaten")
    amt = float(amount) if amount is not None else (lot.quantity or 0)
    amt = min(amt, lot.quantity or 0)
    if amt <= 0:
        return CommandResult(lot, None, "Nothing to consume", {"consumedAmount": 0})
    lot.quantity = round((lot.quantity or 0) - amt, 4)
    state = (freshness or lot.state or "").strip()
    days_kept = _days_kept(lot.purchase_date)
    if lot.quantity <= 0:
        lot.finished = True
        lot.quantity = 0

    ce = ConsumptionEvent(
        product_id=lot.product_id, quantity=amt, unit=lot.unit,
        reason="used" if o == "eaten" else o, outcome=o,
        days_kept=days_kept, state=state, group_id=gid)
    db.session.add(ce)
    db.session.flush()

    name = lot.product.name if lot.product else "item"
    verb = {"eaten": "used", "spoiled": "tossed (spoiled)",
            "expired": "tossed (expired)", "discarded": "discarded"}.get(o, "used")
    summary = f"Recorded {amt} {lot.unit} of {name} {verb}"
    ev = _emit(gid, "consume", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, src=lot.id, delta_value=-Decimal(str(amt)),
               delta_unit=lot.unit, reason=o,
               state_changes={"consumption_event_id": ce.id},
               idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision

    insight = None
    if o in LOSS_OUTCOMES:
        insight = product_insights(gid, lot.product_id).get("suggestion") or None
    return CommandResult(lot, ev, summary,
                         {"consumptionId": ce.id, "consumedAmount": amt, "insight": insight})


# --------------------------------------------------------------------------- #
# reverse
# --------------------------------------------------------------------------- #
def reverse_event(event: InventoryEvent, *, actor_user_id=None, source_app="web",
                  idempotency_key=None) -> CommandResult:
    """Reverse a prior forward event by APPENDING a compensating `reverse` event
    (history is never rewritten). Idempotent: a second reverse of the same event is
    a no-op (guarded by the app-level check AND the uq_event_reversal_of DB
    constraint, which turns a concurrent double-undo into a replay). Raises
    UnsupportedReversal for event types the ledger can't compensate."""
    gid = event.group_id

    if event.type not in REVERSIBLE_TYPES:
        raise UnsupportedReversal(f"cannot reverse a {event.type} event")

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is not None:
            lot0 = db.session.get(StockLot, prior.src_position_id) if prior.src_position_id else None
            return CommandResult(lot0, prior, prior.summary, {}, idempotent_replay=True)
        done = _reversal_of(event.id)
        if done is not None:
            lot0 = db.session.get(StockLot, done.src_position_id) if done.src_position_id else None
            return CommandResult(lot0, done, done.summary, {}, idempotent_replay=True)
        return None

    replayed = replay()
    if replayed is not None:
        return replayed

    lot = db.session.get(StockLot, event.src_position_id) if event.src_position_id else None
    changes: dict = {}

    if event.type == "consume":
        amount = abs(float(event.delta_value or 0))
        if lot is not None and amount:
            lot.quantity = round((lot.quantity or 0) + amount, 4)
            if lot.quantity > 0:
                lot.finished = False
        ce_id = (event.state_changes or {}).get("consumption_event_id")
        if ce_id:
            ce = db.session.get(ConsumptionEvent, ce_id)
            if ce and ce.group_id == gid:
                db.session.delete(ce)  # remove the learning signal for the undone consume
        name = lot.product.name if (lot and lot.product) else "item"
        summary = f"Restored {amount} {event.delta_unit} of {name}"
    else:  # open
        if lot is not None:
            prev = (event.state_changes or {}).get("package_state", ["sealed", "opened"])[0]
            lot.package_state = prev or "sealed"
            lot.opened_date = None
            changes = {"package_state": ["opened", lot.package_state]}
        summary = "Re-sealed package"

    if event.type == "adjust":
        prev, _new = (event.state_changes or {}).get("quantity", [None, None])
        if lot is not None and prev is not None:
            lot.quantity = round(float(prev), 4)
            lot.finished = lot.quantity <= 0
        summary = "Reverted the correction"
    elif event.type == "move":
        prev, _new = (event.state_changes or {}).get("location_id", [None, None])
        if lot is not None:
            lot.location_id = prev
        summary = "Moved it back"
    elif event.type == "split":
        # Undo a split = pour the split-off position back into the source.
        new_lot = db.session.get(StockLot, event.dst_position_id) if event.dst_position_id else None
        if lot is not None and new_lot is not None:
            lot.quantity = round((lot.quantity or 0) + (new_lot.quantity or 0), 4)
            lot.finished = False
            new_lot.quantity = 0
            new_lot.finished = True
        summary = "Undid the split"
    elif event.type == "merge":
        # Undo a merge = pull the merged amount back out of the destination.
        moved = float((event.state_changes or {}).get("moved", 0) or 0)
        src = db.session.get(StockLot, event.src_position_id) if event.src_position_id else None
        dst = db.session.get(StockLot, event.dst_position_id) if event.dst_position_id else None
        if src is not None and dst is not None and moved:
            dst.quantity = round((dst.quantity or 0) - moved, 4)
            dst.finished = dst.quantity <= 0
            src.quantity = round((src.quantity or 0) + moved, 4)
            src.finished = False
        lot = src
        summary = "Undid the merge"

    ev = _emit(gid, "reverse", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, src=(lot.id if lot else None),
               reversal_of=event.id, state_changes=changes,
               idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision
    return CommandResult(lot, ev, summary, {})


# --------------------------------------------------------------------------- #
# adjust — correct a lot's quantity to a measured/observed value
# --------------------------------------------------------------------------- #
def adjust_lot(lot: StockLot, *, new_quantity, quantity_kind="exact", reason="",
               actor_user_id=None, source_app="web", idempotency_key=None) -> CommandResult:
    """Correct a lot to a known amount (e.g. estimated 2 kg → measured 1.6 kg). An
    exact correction supersedes an estimate. Logs the before/after for reversal."""
    gid = lot.group_id

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is None:
            return None
        return CommandResult(lot, prior, prior.summary, {}, idempotent_replay=True)

    replayed = replay()
    if replayed is not None:
        return replayed

    old = lot.quantity or 0
    new = round(float(new_quantity), 4)
    if new < 0:
        new = 0.0
    lot.quantity = new
    if quantity_kind:
        lot.quantity_kind = quantity_kind
    lot.finished = new <= 0
    name = lot.product.name if lot.product else "item"
    summary = f"Corrected {name} to {new} {lot.unit}"
    ev = _emit(gid, "adjust", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, src=lot.id, delta_value=Decimal(str(new)) - Decimal(str(old)),
               delta_unit=lot.unit, reason=reason,
               state_changes={"quantity": [old, new]}, idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision
    return CommandResult(lot, ev, summary, {})


# --------------------------------------------------------------------------- #
# move — relocate a whole position
# --------------------------------------------------------------------------- #
def move_lot(lot: StockLot, *, location_id, actor_user_id=None, source_app="web",
             idempotency_key=None) -> CommandResult:
    """Move a whole position to another location (e.g. thawing portion → fridge)."""
    gid = lot.group_id

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is None:
            return None
        return CommandResult(lot, prior, prior.summary, {}, idempotent_replay=True)

    replayed = replay()
    if replayed is not None:
        return replayed

    old = lot.location_id
    lot.location_id = location_id or None
    name = lot.product.name if lot.product else "item"
    dest = lot.location.name if lot.location else "unassigned"
    summary = f"Moved {name} to {dest}"
    ev = _emit(gid, "move", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, src=lot.id,
               state_changes={"location_id": [old, lot.location_id]},
               idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision
    return CommandResult(lot, ev, summary, {})


# --------------------------------------------------------------------------- #
# split — divide a position into two (conserves total)
# --------------------------------------------------------------------------- #
def split_lot(lot: StockLot, *, amount, location_id=None, package_state=None,
              actor_user_id=None, source_app="web", idempotency_key=None) -> CommandResult:
    """Split `amount` off a lot into a NEW position (optionally in another location
    / package state). Conserves the total. Extra returns the new position's id."""
    gid = lot.group_id

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is None:
            return None
        new = db.session.get(StockLot, prior.dst_position_id) if prior.dst_position_id else None
        return CommandResult(lot, prior, prior.summary,
                             {"newLotId": prior.dst_position_id, "newLot": new},
                             idempotent_replay=True)

    replayed = replay()
    if replayed is not None:
        return replayed

    amt = round(float(amount), 4)
    if amt <= 0 or amt >= (lot.quantity or 0):
        raise ValueError("split amount must be > 0 and < the lot's quantity")
    lot.quantity = round((lot.quantity or 0) - amt, 4)
    new_lot = StockLot(
        product_id=lot.product_id, location_id=(location_id or lot.location_id),
        quantity=amt, unit=lot.unit, storage_method=lot.storage_method,
        package_state=(package_state or lot.package_state), quantity_kind=lot.quantity_kind,
        state=lot.state, purchase_date=lot.purchase_date, opened_date=lot.opened_date,
        expiry_date=lot.expiry_date, expiry_estimated=lot.expiry_estimated,
        source=lot.source, provenance="split", attrs=dict(lot.attrs or {}), group_id=gid,
        created_by=lot.created_by)
    db.session.add(new_lot)
    db.session.flush()
    name = lot.product.name if lot.product else "item"
    summary = f"Split off {amt} {lot.unit} of {name}"
    ev = _emit(gid, "split", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, src=lot.id, dst=new_lot.id,
               delta_value=-Decimal(str(amt)), delta_unit=lot.unit,
               idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision
    return CommandResult(lot, ev, summary, {"newLotId": new_lot.id, "newLot": new_lot})


# --------------------------------------------------------------------------- #
# merge — combine two compatible positions into one (conserves total)
# --------------------------------------------------------------------------- #
def merge_lots(src: StockLot, dst: StockLot, *, actor_user_id=None, source_app="web",
               idempotency_key=None) -> CommandResult:
    """Pour `src` into `dst` (same product + unit), emptying src. Conserves total.
    Used e.g. to combine flour from two purchases in one bin."""
    gid = src.group_id

    def replay():
        prior = _existing_event(gid, idempotency_key)
        if prior is None:
            return None
        return CommandResult(dst, prior, prior.summary, {}, idempotent_replay=True)

    replayed = replay()
    if replayed is not None:
        return replayed

    if src.id == dst.id:
        raise ValueError("cannot merge a lot into itself")
    if src.product_id != dst.product_id or src.unit != dst.unit:
        raise ValueError("can only merge lots of the same product and unit")
    moved = round(src.quantity or 0, 4)
    dst.quantity = round((dst.quantity or 0) + moved, 4)
    src.quantity = 0
    src.finished = True
    name = dst.product.name if dst.product else "item"
    summary = f"Merged {moved} {dst.unit} of {name} together"
    ev = _emit(gid, "merge", actor_user_id=actor_user_id, source_app=source_app,
               summary=summary, src=src.id, dst=dst.id, delta_value=Decimal(str(moved)),
               delta_unit=dst.unit, state_changes={"moved": moved},
               idempotency_key=idempotency_key)
    collision = _commit_or_replay(replay)
    if collision is not None:
        return collision
    return CommandResult(dst, ev, summary, {"mergedFrom": src.id})
