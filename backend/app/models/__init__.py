"""Edibl data model.

Core shape:  Product (the "what") ──▶ StockLot (a real quantity of it, in a
Location, with a storage method + expiry).  Specialty attributes (wine vintage,
meat cut/weight, …) live in StockLot.attrs (JSON) so the schema stays flexible.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import (String, Text, Float, Boolean, DateTime, ForeignKey, JSON,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def gen_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IDMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- #
# Tenancy (household) + integration tokens
# --------------------------------------------------------------------------- #
class Group(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "groups"
    name: Mapped[str] = mapped_column(String(255), default="Household")

    users = relationship("User", back_populates="group", cascade="all, delete-orphan")
    locations = relationship("Location", back_populates="group", cascade="all, delete-orphan")
    products = relationship("Product", back_populates="group", cascade="all, delete-orphan")
    stock = relationship("StockLot", back_populates="group", cascade="all, delete-orphan")
    shopping = relationship("ShoppingItem", back_populates="group", cascade="all, delete-orphan")
    consumption = relationship("ConsumptionEvent", back_populates="group", cascade="all, delete-orphan")
    planned = relationship("PlannedItem", back_populates="group", cascade="all, delete-orphan")
    tokens = relationship("ApiToken", back_populates="group", cascade="all, delete-orphan")
    settings = relationship("Setting", back_populates="group", cascade="all, delete-orphan")
    events = relationship("InventoryEvent", back_populates="group", cascade="all, delete-orphan")
    acquisition_lots = relationship("AcquisitionLot", cascade="all, delete-orphan")
    concepts = relationship("FoodConcept", cascade="all, delete-orphan")
    reservations = relationship("Reservation", cascade="all, delete-orphan")
    detections = relationship("Detection", cascade="all, delete-orphan")


class User(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    # HA user behind ingress (from X-Remote-User-Id); NULL for local/JWT users.
    ha_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # First user in a household is the owner; owners gate household config.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    group = relationship("Group", back_populates="users")
    api_tokens = relationship("ApiToken", back_populates="user", cascade="all, delete-orphan")


TOKEN_PREFIX = "edbl_"


def generate_raw_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApiToken(IDMixin, TimestampMixin, db.Model):
    """Long-lived API key for machine clients (myMeal, the MCP server, HA)."""
    __tablename__ = "api_tokens"
    name: Mapped[str] = mapped_column(String(255), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hint: Mapped[str] = mapped_column(String(16), default="")
    last_used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)
    user = relationship("User", back_populates="api_tokens")
    group = relationship("Group", back_populates="tokens")


# --------------------------------------------------------------------------- #
# Places to store food (nested, multi-site)
# --------------------------------------------------------------------------- #
LOCATION_KINDS = (
    "site", "room", "fridge", "freezer", "pantry", "wine_cellar", "cupboard", "other",
)


class Location(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "locations"
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(24), default="other")
    temp_c: Mapped[float] = mapped_column(Float, nullable=True)  # storage temperature
    notes: Mapped[str] = mapped_column(Text, default="")
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="locations")
    parent_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"), nullable=True)
    parent = relationship("Location", remote_side="Location.id", back_populates="children")
    children = relationship("Location", back_populates="parent", cascade="all, delete-orphan")
    stock = relationship("StockLot", back_populates="location")


# --------------------------------------------------------------------------- #
# Catalog: what a product IS
# --------------------------------------------------------------------------- #
# Categories and units are user-driven / free-form. These are only *suggestions*
# for autocomplete — any string is accepted (see /products/suggestions, which
# also merges in the values you actually use).
CATEGORIES = (
    "produce", "dairy", "meat", "seafood", "bakery", "frozen", "beverage",
    "wine", "spirits", "beer", "dry_goods", "condiment", "snack", "other",
)
UNITS = ("count", "g", "kg", "oz", "lb", "ml", "l", "pack", "bottle")
# How much detail a product is tracked with. Chosen per item (defaulted by
# category) so casual users can stay coarse and precise users can go fine-grained.
TRACKING_MODES = ("presence", "level", "count", "measure", "package", "portions")


# What kind of thing this is — gates recipe matching + food-expiry logic. Non-food
# consumables (foil, bags, dishwasher tablets) never match recipes.
ITEM_TYPES = ("food", "beverage", "consumable")


class FoodConcept(IDMixin, TimestampMixin, db.Model):
    """Canonical ingredient identity — the broad 'what' a recipe cares about ('milk',
    'green onion', 'chicken breast'), separate from a purchasable Product. Anchors
    recipe matching, aliases/regional names, substitutions, and allergen/dietary
    metadata. See docs/stock-redesign/DESIGN.md §4.4."""
    __tablename__ = "food_concepts"
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)          # ["scallion","green onion"]
    classification: Mapped[str] = mapped_column(String(64), default="")  # vegetable/dairy/…
    item_type: Mapped[str] = mapped_column(String(16), default="food")   # ITEM_TYPES
    allergens: Mapped[list] = mapped_column(JSON, default=list)         # ["dairy","nuts"]
    substitution_group: Mapped[str] = mapped_column(String(64), default="")
    default_tracking: Mapped[str] = mapped_column(String(16), default="")
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)
    products = relationship("Product", back_populates="concept")


class Product(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "products"
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(64), default="other")
    # Canonical identity (nullable; derived from `family` at migration) + what kind
    # of thing this is (non-food is excluded from recipe/expiry logic).
    concept_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("food_concepts.id"), nullable=True, index=True)
    concept = relationship("FoodConcept", back_populates="products")
    item_type: Mapped[str] = mapped_column(String(16), default="food")
    # Replenishment policy (all optional): desired minimum / target on hand, the
    # threshold that triggers a reorder suggestion, and staple / do-not-suggest flags.
    min_quantity: Mapped[float] = mapped_column(Float, nullable=True)
    target_quantity: Mapped[float] = mapped_column(Float, nullable=True)
    reorder_threshold: Mapped[float] = mapped_column(Float, nullable=True)
    staple: Mapped[bool] = mapped_column(Boolean, default=False)
    do_not_suggest: Mapped[bool] = mapped_column(Boolean, default=False)
    # Free-text display grouping, e.g. "Milk" for both Organic milk and Filtered
    # milk. Distinct products (own shelf-life + lots) that read as one group.
    family: Mapped[str] = mapped_column(String(255), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(64), default="", index=True)
    default_unit: Mapped[str] = mapped_column(String(32), default="count")
    # How this product is normally tracked (TRACKING_MODES). "" = derive from
    # category (see services.tracking.default_tracking_mode). Lets a spice track as
    # "level", eggs as "count", meat as "measure" — user-changeable.
    tracking_mode: Mapped[str] = mapped_column(String(16), default="")
    # Optional per-product override of the category shelf-life table (days).
    shelf_life_days: Mapped[int] = mapped_column(nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="products")
    stock = relationship("StockLot", back_populates="product", cascade="all, delete-orphan")


# --------------------------------------------------------------------------- #
# Inventory: an actual quantity in a place (the core entity)
# --------------------------------------------------------------------------- #
STORAGE_METHODS = (
    "fresh", "refrigerated", "frozen", "vacuum_sealed", "pantry", "opened",
)

# Observed freshness / condition of a lot right now. Free-form + user-driven;
# these are only suggestions. "" = not tracked. Feeds shelf-life learning.
FRESHNESS_LEVELS = (
    "fresh", "ripe", "unripe", "overripe", "opened", "spoiling", "use soon",
)
LIFECYCLE_STATES = FRESHNESS_LEVELS  # back-compat alias

# 1–5 condition scales for intake, keyed by domain (highest level = will-keep-
# longest / best condition). The stored `freshness`/`state` value is the `key`;
# `label` is what the UI shows. Produce uses ripeness language, bakery uses
# staleness, etc. Free-text freshness still works — this is the guided default.
FRESHNESS_SCALES = {
    "default": (
        {"level": 5, "key": "fresh", "label": "Fresh"},
        {"level": 4, "key": "good", "label": "Good"},
        {"level": 3, "key": "okay", "label": "Okay"},
        {"level": 2, "key": "use soon", "label": "Use soon"},
        {"level": 1, "key": "going off", "label": "Going off"},
    ),
    "produce": (
        {"level": 5, "key": "unripe", "label": "Unripe / firm"},
        {"level": 4, "key": "ripe", "label": "Ripe"},
        {"level": 3, "key": "use soon", "label": "Ripe — use soon"},
        {"level": 2, "key": "overripe", "label": "Very ripe"},
        {"level": 1, "key": "spoiling", "label": "Overripe / spoiling"},
    ),
    "bakery": (
        {"level": 5, "key": "fresh", "label": "Fresh-baked"},
        {"level": 4, "key": "good", "label": "Day-old"},
        {"level": 3, "key": "okay", "label": "Still good"},
        {"level": 2, "key": "stale", "label": "Going stale"},
        {"level": 1, "key": "hard", "label": "Stale / hard"},
    ),
    "meat": (
        {"level": 5, "key": "fresh", "label": "Fresh"},
        {"level": 4, "key": "good", "label": "Good"},
        {"level": 3, "key": "okay", "label": "Okay"},
        {"level": 2, "key": "use soon", "label": "Use soon"},
        {"level": 1, "key": "off", "label": "Turning / off"},
    ),
}
# Which categories share a scale (anything unlisted → "default").
_SCALE_BY_CATEGORY = {
    "produce": "produce", "bakery": "bakery",
    "meat": "meat", "seafood": "meat",
}
# Back-compat: the old flat name is the default scale.
FRESHNESS_SCALE = FRESHNESS_SCALES["default"]


def freshness_scale_for(category: str) -> tuple:
    """The 1–5 condition scale appropriate to a category (ripeness for produce,
    staleness for bakery, …), defaulting to the general fresh→going-off scale."""
    key = _SCALE_BY_CATEGORY.get((category or "").strip().lower(), "default")
    return FRESHNESS_SCALES[key]

# How a lot (or part of one) left inventory. "Good" outcomes (eaten) teach us the
# item lasted at least that long; "loss" outcomes (spoiled/expired/discarded) are
# the signal that shortens the personalized shelf-life estimate for next time.
OUTCOMES = ("eaten", "spoiled", "expired", "discarded", "other")
GOOD_OUTCOMES = frozenset({"eaten", "used"})
LOSS_OUTCOMES = frozenset({"spoiled", "expired", "discarded"})

# Package state is ORTHOGONAL to storage_method / freshness (a carton can be
# "frozen" AND "opened"). Kept separate so opening a package no longer overloads
# storage_method. "" = unknown/not tracked.
PACKAGE_STATES = ("sealed", "opened", "resealed", "damaged", "decanted")

# Quantity kinds mirror services.quantity — how sure we are of the amount. Unknown
# and presence carry NO number and must never be coerced to 0 or 1.
QUANTITY_KINDS = ("exact", "estimated", "approximate", "presence", "unknown")

# Inventory-event ledger types (the shared command vocabulary). The slice writes
# add/import/consume/open + reverse; the rest are reserved for later phases.
EVENT_TYPES = (
    "add", "import", "reconcile", "adjust", "consume", "waste", "expire",
    "donate", "return", "open", "reseal", "move", "split", "merge", "freeze",
    "thaw", "prepare", "transform", "decant", "archive", "reverse",
)


class AcquisitionLot(IDMixin, TimestampMixin, db.Model):
    """A batch acquired or produced together — one purchase, one cooking batch, one
    farm box, one butchering session. Separated from physical placement so a single
    acquisition can live as several positions (5 lb of chicken → 2 lb fridge + 3
    freezer portions) while keeping its own date/source/cost/lot facts.
    See docs/stock-redesign/DESIGN.md §4.5."""
    __tablename__ = "acquisition_lots"
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="")        # store/butcher/farm/cooked
    receipt_ref: Mapped[str] = mapped_column(String(128), default="")
    original_quantity: Mapped[float] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), default="count")
    cost: Mapped[float] = mapped_column(Float, nullable=True)          # money-as-float; Numeric TODO
    currency: Mapped[str] = mapped_column(String(8), default="")
    lot_code: Mapped[str] = mapped_column(String(64), default="")
    provenance: Mapped[str] = mapped_column(String(64), default="manual")
    # Lineage: the acquisition(s) this one was produced FROM (cooking/portioning).
    derived_from: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)
    positions = relationship("StockLot", back_populates="acquisition_lot")


class StockLot(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "stock_lots"
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    acquisition_lot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("acquisition_lots.id"), nullable=True, index=True)
    acquisition_lot = relationship("AcquisitionLot", back_populates="positions")
    location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(32), default="count")
    storage_method: Mapped[str] = mapped_column(String(24), default="refrigerated")
    # Package state (sealed/opened/…), ORTHOGONAL to storage_method + freshness.
    package_state: Mapped[str] = mapped_column(String(24), default="sealed")
    # Observed freshness / condition (free-form; FRESHNESS_LEVELS are suggestions);
    # "" when not tracked. Exposed as "freshness" in the API.
    state: Mapped[str] = mapped_column(String(32), default="")
    # How sure we are of `quantity` (QUANTITY_KINDS). "unknown"/"presence" mean the
    # number is not meaningful — the UI shows "some"/"unknown", never 0 or 1.
    quantity_kind: Mapped[str] = mapped_column(String(16), default="exact")
    # Where this amount came from + how confident (feeds review/agent-safety).
    provenance: Mapped[str] = mapped_column(String(64), default="manual")
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    purchase_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    opened_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    expiry_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # effective forecast
    # True when expiry was estimated from a shelf-life profile (vs printed date).
    expiry_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    # Raw date FACTS kept alongside the effective forecast (printed on the pack).
    best_by: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    use_by: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    # How the effective expiry_date was derived + how sure we are (0–1), so the UI
    # can explain it honestly. EXPIRY_BASES: use_by/best_by/user/estimated/frozen/thawed.
    expiry_basis: Mapped[str] = mapped_column(String(16), default="")
    expiry_confidence: Mapped[float] = mapped_column(Float, nullable=True)
    cost: Mapped[float] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="")  # store/butcher/farm
    lot_code: Mapped[str] = mapped_column(String(64), default="")
    finished: Mapped[bool] = mapped_column(Boolean, default=False)  # fully consumed
    notes: Mapped[str] = mapped_column(Text, default="")
    # Specialty attributes: wine {vintage,varietal,region,producer,abv,volume_ml};
    # meat {cut,animal,weight_g,freeze_date,thaw_by,butcher_session}.
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
    # Who added this lot (for multi-user households); NULL for legacy/imported lots.
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_by_user = relationship("User")
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="stock")
    product = relationship("Product", back_populates="stock")
    location = relationship("Location", back_populates="stock")


# --------------------------------------------------------------------------- #
# Shelf-life table (seeded defaults) — drives expiry estimation
# --------------------------------------------------------------------------- #
class ShelfLifeProfile(IDMixin, db.Model):
    __tablename__ = "shelf_life_profiles"
    category: Mapped[str] = mapped_column(String(24), index=True)
    storage_method: Mapped[str] = mapped_column(String(24), index=True)
    typical_days: Mapped[int] = mapped_column()


# --------------------------------------------------------------------------- #
# Shopping list + consumption history (runout prediction)
# --------------------------------------------------------------------------- #
class ShoppingItem(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "shopping_items"
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(16), default="count")
    note: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="needed")  # needed/purchased
    source: Mapped[str] = mapped_column(String(24), default="manual")  # manual/low_stock/expiring/recipe
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="shopping")


class Reservation(IDMixin, TimestampMixin, db.Model):
    """Stock earmarked for a planned meal, so it isn't double-allocated or suggested
    for reorder as if free. Points at a product (or just a name/concept) + amount."""
    __tablename__ = "reservations"
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=True)
    concept_id: Mapped[str] = mapped_column(String(36), ForeignKey("food_concepts.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(32), default="count")
    meal: Mapped[str] = mapped_column(String(255), default="")
    source_ref: Mapped[str] = mapped_column(String(128), default="")
    needed_by: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)


class PlannedItem(IDMixin, TimestampMixin, db.Model):
    """A required ingredient propagated from myMeal (a recipe / meal plan). Edibl
    matches these against on-hand stock to predict shortfalls and what to order."""
    __tablename__ = "planned_items"
    name: Mapped[str] = mapped_column(String(255), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(16), default="count")
    needed_by: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="mymeal")  # mymeal recipe/plan id
    source_ref: Mapped[str] = mapped_column(String(128), default="")   # external id for upsert
    meal: Mapped[str] = mapped_column(String(255), default="")         # recipe / meal name
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="planned")


class Detection(IDMixin, TimestampMixin, db.Model):
    """A low-confidence AI/vision detection staged for review before it touches
    inventory (ADR-0004). The user confirms (→ a stock lot) or dismisses; a match to
    an existing product is flagged so a re-detection isn't added twice."""
    __tablename__ = "detections"
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), default="count")
    category: Mapped[str] = mapped_column(String(64), default="")
    storage_method: Mapped[str] = mapped_column(String(24), default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="vision")   # vision/receipt/agent
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    matched_product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)


class Setting(IDMixin, TimestampMixin, db.Model):
    """Per-household runtime settings (e.g. the chat LLM provider) editable from
    the UI. These override the add-on / env defaults, so a provider can be set up
    in Home Assistant *or* in Edibl and is remembered across restarts."""
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("group_id", "key", name="uq_setting_group_key"),)
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)
    group = relationship("Group", back_populates="settings")


class ConsumptionEvent(IDMixin, db.Model):
    __tablename__ = "consumption_events"
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(32), default="count")
    reason: Mapped[str] = mapped_column(String(24), default="used")  # legacy: used/expired/discarded
    # Richer lifecycle label (OUTCOMES) + how long the lot was kept before this
    # event + its freshness at the time. These drive personalized shelf-life
    # learning and per-item suggestions.
    outcome: Mapped[str] = mapped_column(String(16), default="eaten")
    days_kept: Mapped[int] = mapped_column(nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="consumption")


class InventoryEvent(IDMixin, db.Model):
    """Append-only ledger of stock changes (the audit + reversal + explanation
    source). Every inventory command writes one of these atomically with the
    projection update; nothing here is mutated after commit — a reversal appends a
    NEW compensating event pointing back via `reversal_of`. See
    docs/stock-redesign/adr/0001-event-logged-not-event-sourced.md.
    """
    __tablename__ = "inventory_events"
    # (group, idempotency_key) is unique so a retried command is a no-op, not a
    # double-apply. NULL keys are allowed to repeat (SQLite permits multiple NULLs).
    __table_args__ = (
        UniqueConstraint("group_id", "idempotency_key", name="uq_event_group_idem"),
        # A forward event can be reversed at most once — the DB rejects a second
        # concurrent reverse (NULLs, i.e. forward events, are allowed to repeat).
        UniqueConstraint("reversal_of", name="uq_event_reversal_of"),
    )
    type: Mapped[str] = mapped_column(String(24), index=True)  # EVENT_TYPES
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True)
    source_app: Mapped[str] = mapped_column(String(32), default="web")  # web/mcp/assistant/ha
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), default="")
    # The physical positions (StockLots) this event touched.
    src_position_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dst_position_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Signed quantity delta as a decimal STRING (exact; no binary-float rounding).
    delta_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delta_unit: Mapped[str] = mapped_column(String(32), default="")
    # Orthogonal state facet changes, e.g. {"package_state": ["sealed","opened"]}.
    state_changes: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    provenance: Mapped[str] = mapped_column(String(64), default="manual")
    # The event this one reverses (self-FK); NULL for forward events.
    reversal_of: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inventory_events.id"), nullable=True)
    summary: Mapped[str] = mapped_column(String(255), default="")
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)
    group = relationship("Group", back_populates="events")


__all__ = [
    "db", "gen_uuid", "utcnow",
    "Group", "User", "ApiToken", "TOKEN_PREFIX", "generate_raw_token", "hash_token",
    "Location", "LOCATION_KINDS", "Product", "CATEGORIES", "UNITS", "TRACKING_MODES",
    "StockLot", "STORAGE_METHODS", "FRESHNESS_LEVELS", "FRESHNESS_SCALE",
    "FRESHNESS_SCALES", "freshness_scale_for", "LIFECYCLE_STATES", "OUTCOMES",
    "GOOD_OUTCOMES", "LOSS_OUTCOMES", "PACKAGE_STATES", "QUANTITY_KINDS", "EVENT_TYPES",
    "ShelfLifeProfile", "ShoppingItem", "ConsumptionEvent", "PlannedItem", "Setting",
    "InventoryEvent", "AcquisitionLot", "FoodConcept", "ITEM_TYPES", "Reservation",
    "Detection",
]
