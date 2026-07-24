from flask import Blueprint, request, jsonify, abort

from ..extensions import db, limiter
from ..models import (Product, StockLot, FoodConcept, ITEM_TYPES, CATEGORIES, UNITS,
                      FRESHNESS_LEVELS, STORAGE_METHODS)
from ..auth import login_required, owner_required, current_group
from ..schemas.serializers import product_out, concept_out
from ..services.estimation import product_insights

bp = Blueprint("products", __name__)


def _get(product_id):
    p = db.session.get(Product, product_id)
    if not p or p.group_id != current_group().id:
        abort(404)
    return p


def _apply(p, data):
    for k, attr in {"name": "name", "brand": "brand", "barcode": "barcode",
                    "notes": "notes"}.items():
        if k in data and data[k] is not None:
            setattr(p, attr, data[k])
    # Category and the display group are free-form / user-driven.
    if data.get("category"):
        p.category = str(data["category"]).strip()
    if "family" in data or "group" in data:
        p.family = str(data.get("family") or data.get("group") or "").strip()
    if "defaultUnit" in data and data["defaultUnit"]:
        p.default_unit = data["defaultUnit"]
    if "trackingMode" in data:
        from ..models import TRACKING_MODES
        tm = (data.get("trackingMode") or "").strip().lower()
        p.tracking_mode = tm if tm in TRACKING_MODES else ""
    if "itemType" in data:
        it = (data.get("itemType") or "food").strip().lower()
        p.item_type = it if it in ITEM_TYPES else "food"
    if "conceptId" in data:
        p.concept_id = data["conceptId"] or None
    for k, attr in {"minQuantity": "min_quantity", "targetQuantity": "target_quantity",
                    "reorderThreshold": "reorder_threshold"}.items():
        if k in data:
            setattr(p, attr, data[k] if data[k] is not None else None)
    if "staple" in data:
        p.staple = bool(data["staple"])
    if "doNotSuggest" in data:
        p.do_not_suggest = bool(data["doNotSuggest"])
    if "shelfLifeDays" in data:
        p.shelf_life_days = data["shelfLifeDays"] or None


@bp.get("/products")
@login_required
def list_products():
    q = db.session.query(Product).filter_by(group_id=current_group().id)
    search = request.args.get("q")
    if search:
        like = f"%{search}%"
        q = q.filter(db.or_(Product.name.ilike(like), Product.brand.ilike(like),
                            Product.barcode.ilike(like), Product.search_text.ilike(like)))
    if request.args.get("category"):
        q = q.filter(Product.category == request.args["category"])
    return jsonify([product_out(p) for p in q.order_by(Product.name.asc()).all()])


@bp.get("/products/suggestions")
@login_required
def suggestions():
    """Autocomplete fuel: seed suggestions merged with the values this household
    actually uses — for categories, units, groups (families), freshness, product
    names, and storage methods. Everything is free-form; these just help typing."""
    gid = current_group().id
    products = db.session.query(Product).filter_by(group_id=gid).all()
    lots = db.session.query(StockLot).filter_by(group_id=gid).all()

    def merged(seed, used):
        seen, out = set(), []
        for v in list(seed) + sorted(used):
            v = (v or "").strip()
            if v and v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)
        return out

    return jsonify({
        "categories": merged(CATEGORIES, {p.category for p in products}),
        "units": merged(UNITS, {p.default_unit for p in products} | {s.unit for s in lots}),
        "families": merged([], {p.family for p in products if p.family}),
        "freshness": merged(FRESHNESS_LEVELS, {s.state for s in lots if s.state}),
        "storageMethods": merged(STORAGE_METHODS, {s.storage_method for s in lots}),
        "names": sorted({p.name for p in products}),
    })


@bp.get("/products/<product_id>/insights")
@login_required
def insights(product_id):
    """Lifecycle insight for a product (waste rate, learned shelf life, suggestion)."""
    _get(product_id)
    return jsonify(product_insights(current_group().id, product_id))


@bp.get("/products/barcode/<code>")
@limiter.limit("60/minute")  # generous for fast scanning; bounds external lookups
@login_required
def by_barcode(code):
    """Resolve a scanned barcode. Known products return locally; unknown codes
    optionally fall back to the public Open Food Facts database (EDIBL_BARCODE_LOOKUP)
    so a scan can pre-fill name/brand/category for a new item."""
    p = (db.session.query(Product)
         .filter_by(group_id=current_group().id, barcode=code).first())
    if p:
        return jsonify({"found": True, "product": product_out(p)})
    from ..services.barcode import lookup_barcode
    hit = lookup_barcode(code)
    if hit:
        return jsonify({"found": False, "barcode": code, "suggestion": hit})
    return jsonify({"found": False, "barcode": code})


@bp.get("/products/autocomplete")
@login_required
def autocomplete():
    """Server-side item-name autocomplete for the add form. Ranked via the matching
    service (exact/alias/family/substring), newest-relevant first. ?q=&limit="""
    from ..services import matching
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"names": []})
    limit = min(int(request.args.get("limit", 8) or 8), 20)
    names = []
    for c in matching.match_products(current_group().id, q):
        if c.product.name not in names:
            names.append(c.product.name)
        if len(names) >= limit:
            break
    return jsonify({"names": names})


@bp.post("/products")
@login_required
def create():
    data = request.get_json(force=True) or {}
    p = Product(name=data.get("name", ""), group_id=current_group().id)
    _apply(p, data)
    db.session.add(p)
    db.session.commit()
    return jsonify(product_out(p)), 201


@bp.put("/products/<product_id>")
@login_required
def update(product_id):
    p = _get(product_id)
    _apply(p, request.get_json(force=True) or {})
    db.session.commit()
    return jsonify(product_out(p))


@bp.delete("/products/<product_id>")
@login_required
def delete(product_id):
    db.session.delete(_get(product_id))
    db.session.commit()
    return "", 204


# --------------------------------------------------------------------------- #
# Food concepts — canonical ingredient identity (aliases, item_type, allergens)
# --------------------------------------------------------------------------- #
def _get_concept(concept_id):
    c = db.session.get(FoodConcept, concept_id)
    if not c or c.group_id != current_group().id:
        abort(404)
    return c


def _apply_concept(c, data):
    if data.get("canonicalName"):
        c.canonical_name = str(data["canonicalName"]).strip()
    if "aliases" in data and isinstance(data["aliases"], list):
        c.aliases = [str(a).strip() for a in data["aliases"] if str(a).strip()]
    if "allergens" in data and isinstance(data["allergens"], list):
        c.allergens = [str(a).strip().lower() for a in data["allergens"] if str(a).strip()]
    if "classification" in data:
        c.classification = str(data.get("classification") or "").strip()
    if "substitutionGroup" in data:
        c.substitution_group = str(data.get("substitutionGroup") or "").strip()
    if "itemType" in data:
        it = (data.get("itemType") or "food").strip().lower()
        c.item_type = it if it in ITEM_TYPES else "food"


@bp.get("/concepts")
@login_required
def list_concepts():
    concepts = (db.session.query(FoodConcept)
                .filter_by(group_id=current_group().id)
                .order_by(FoodConcept.canonical_name).all())
    return jsonify({"items": [concept_out(c) for c in concepts], "total": len(concepts)})


@bp.post("/concepts")
@login_required
def create_concept():
    data = request.get_json(force=True) or {}
    if not (data.get("canonicalName") or "").strip():
        return jsonify({"error": "canonicalName required"}), 422
    c = FoodConcept(canonical_name="", group_id=current_group().id)
    _apply_concept(c, data)
    db.session.add(c)
    db.session.commit()
    return jsonify(concept_out(c)), 201


@bp.put("/concepts/<concept_id>")
@login_required
def update_concept(concept_id):
    c = _get_concept(concept_id)
    _apply_concept(c, request.get_json(force=True) or {})
    db.session.commit()
    return jsonify(concept_out(c))


def _describe_fields(p):
    return {"name": p.name, "brand": p.brand, "category": p.category}


_SEARCH_TEXT_MAX = 600  # keep search_text bounded (LLM/web output is untrusted)
_BATCH_FETCH = 10
_BATCH_BUDGET_S = 60    # stay well under the gunicorn worker timeout (120s)


def _apply_description(p, result):
    text = result["description"]
    if result.get("keywords"):
        text = f"{text} {' '.join(result['keywords'])}"
    p.search_text = text.strip()[:_SEARCH_TEXT_MAX]


@bp.post("/products/<product_id>/describe")
@limiter.limit("20/minute")
@login_required
def describe_product(product_id):
    """Look the product up online (Ollama web search) and store a short searchable
    description. Returns it, or 4xx when unavailable."""
    from ..services import enrich

    p = _get(product_id)
    if not enrich.enabled():
        return jsonify({"error": "Web search isn't configured. Set an Ollama search "
                                 "key (EDIBL_OLLAMA_SEARCH_KEY)."}), 409
    result = enrich.describe(_describe_fields(p))
    if not result:
        return jsonify({"error": "Couldn't find a description for this product online."}), 422
    _apply_description(p, result)
    db.session.commit()
    return jsonify({"searchText": p.search_text, "description": result["description"],
                    "keywords": result.get("keywords", []),
                    "sources": result.get("sources", [])})


@bp.post("/products/describe-missing")
@limiter.limit("6/hour")
@owner_required
def describe_missing():
    """Batch-enrich products with no search_text yet. Owner-only (bulk external,
    paid calls). Commits per item so a worker-kill keeps completed work, and stops
    before the worker timeout; returns how many remain so the UI can resume."""
    import time

    from ..services import enrich

    if not enrich.enabled():
        return jsonify({"error": "Web search isn't configured."}), 409
    missing = (db.session.query(Product)
               .filter(Product.group_id == current_group().id,
                       db.or_(Product.search_text.is_(None), Product.search_text == "")))
    products = missing.limit(_BATCH_FETCH).all()
    deadline = time.monotonic() + _BATCH_BUDGET_S
    done = 0
    for p in products:
        if time.monotonic() > deadline:
            break
        result = enrich.describe(_describe_fields(p))
        if result:
            _apply_description(p, result)
            db.session.commit()  # per item — partial progress survives a timeout
            done += 1
    return jsonify({"described": done, "scanned": len(products), "remaining": missing.count()})
