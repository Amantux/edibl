"""Portable export / import of a household's inventory.

Home Assistant already snapshots the add-on's /data (the whole SQLite DB), so this
is for *portability*: a human-readable JSON snapshot you can keep or move between
instances, plus a stock CSV. Import is **additive** (creates what's missing by
name) — it never deletes, so it can't clobber existing data.
"""
import csv
import io
from datetime import datetime

from flask import Blueprint, request, jsonify, Response

from ..extensions import db
from ..models import Product, Location, StockLot, ShoppingItem, utcnow
from ..auth import login_required, owner_required, current_group
from ..schemas.serializers import (product_out, location_out, stock_out,
                                    shopping_out, iso)

bp = Blueprint("data", __name__)


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "").replace("+00:00", ""))
    except ValueError:
        return None


@bp.get("/export")
@login_required
def export():
    """Full JSON snapshot of this household's inventory."""
    gid = current_group().id
    q = lambda m: db.session.query(m).filter_by(group_id=gid)  # noqa: E731
    return jsonify({
        "edibl": "1",
        "exportedAt": iso(utcnow()),
        "group": current_group().name,
        "locations": [location_out(loc, with_counts=False) for loc in q(Location).all()],
        "products": [product_out(p) for p in q(Product).all()],
        "stock": [stock_out(s) for s in q(StockLot).filter_by(finished=False).all()],
        "shopping": [shopping_out(i) for i in q(ShoppingItem).all()],
    })


@bp.get("/export/backup.db")
@owner_required
def backup_db():
    """A consistent, whole-database SQLite backup file (all households) — a real
    point-in-time copy taken with SQLite's online backup API, safe even while the
    app is writing (unlike copying the file). Owner-only. Non-SQLite → 400."""
    import sqlite3
    import tempfile
    engine = db.engine
    if engine.dialect.name != "sqlite":
        return jsonify({"error": "backup.db is only available on SQLite"}), 400
    src_path = engine.url.database
    if not src_path:
        return jsonify({"error": "no database file"}), 400
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(tmp.name)
    try:
        with dst:
            src.backup(dst)   # online backup — consistent snapshot
    finally:
        src.close()
        dst.close()
    import os
    with open(tmp.name, "rb") as f:
        data = f.read()
    os.unlink(tmp.name)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    return Response(data, mimetype="application/x-sqlite3", headers={
        "Content-Disposition": f'attachment; filename="edibl-backup-{stamp}.db"'})


@bp.get("/export/stock.csv")
@login_required
def export_csv():
    """Active stock as CSV (spreadsheet-friendly)."""
    gid = current_group().id
    lots = db.session.query(StockLot).filter_by(group_id=gid, finished=False).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["product", "group", "category", "quantity", "unit", "storage",
                "freshness", "location", "purchased", "expires", "source"])
    for s in lots:
        p = s.product
        w.writerow([
            p.name if p else "", (p.family if p else "") or "",
            p.category if p else "", s.quantity, s.unit, s.storage_method,
            s.state or "", s.location.name if s.location else "",
            iso(s.purchase_date) or "", iso(s.expiry_date) or "", s.source or "",
        ])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=edibl-stock.csv"})


@bp.post("/import")
@owner_required
def data_import():
    """Additive restore from an /export JSON snapshot. Creates locations, products,
    stock lots, and shopping items that don't already exist (matched by name).
    Never deletes — safe to run against a populated instance."""
    gid = current_group().id
    data = request.get_json(force=True) or {}
    counts = {"locations": 0, "products": 0, "stock": 0, "shopping": 0}

    loc_by_name = {loc.name: loc for loc in
                   db.session.query(Location).filter_by(group_id=gid).all()}
    for lo in data.get("locations", []):
        name = (lo.get("name") or "").strip()
        if not name or name in loc_by_name:
            continue
        loc = Location(name=name, kind=lo.get("kind") or "other",
                       notes=lo.get("notes") or "", group_id=gid)
        db.session.add(loc)
        db.session.flush()
        loc_by_name[name] = loc
        counts["locations"] += 1

    prod_by_name = {p.name: p for p in
                    db.session.query(Product).filter_by(group_id=gid).all()}

    def _product(name, src):
        name = (name or "").strip()
        if not name:
            return None
        p = prod_by_name.get(name)
        if not p:
            p = Product(name=name, category=(src.get("category") or "other"),
                        family=src.get("family") or "", brand=src.get("brand") or "",
                        barcode=src.get("barcode") or "",
                        default_unit=src.get("defaultUnit") or "count",
                        shelf_life_days=src.get("shelfLifeDays"),
                        notes=src.get("notes") or "", group_id=gid)
            db.session.add(p)
            db.session.flush()
            prod_by_name[name] = p
            counts["products"] += 1
        return p

    for pr in data.get("products", []):
        _product(pr.get("name"), pr)

    for st in data.get("stock", []):
        prod = st.get("product") or {}
        p = _product(prod.get("name"), prod)
        if not p:
            continue
        loc = st.get("location") or {}
        loc_id = loc_by_name[loc["name"]].id if loc.get("name") in loc_by_name else None
        db.session.add(StockLot(
            product_id=p.id, location_id=loc_id,
            quantity=float(st.get("quantity") or 1), unit=st.get("unit") or "count",
            storage_method=st.get("storageMethod") or "refrigerated",
            state=st.get("freshness") or st.get("state") or "",
            purchase_date=_parse_dt(st.get("purchaseDate")),
            expiry_date=_parse_dt(st.get("expiryDate")),
            expiry_estimated=bool(st.get("expiryEstimated")),
            cost=st.get("cost"), source=st.get("source") or "",
            notes=st.get("notes") or "", attrs=st.get("attrs") or {}, group_id=gid))
        counts["stock"] += 1

    existing_shop = {(i.name, i.status) for i in
                     db.session.query(ShoppingItem).filter_by(group_id=gid).all()}
    for it in data.get("shopping", []):
        key = (it.get("name"), it.get("status", "needed"))
        if not it.get("name") or key in existing_shop:
            continue
        db.session.add(ShoppingItem(
            name=it["name"], quantity=float(it.get("quantity") or 1),
            unit=it.get("unit") or "count", note=it.get("note") or "",
            status=it.get("status") or "needed", source=it.get("source") or "manual",
            group_id=gid))
        counts["shopping"] += 1

    db.session.commit()
    return jsonify({"imported": counts})
