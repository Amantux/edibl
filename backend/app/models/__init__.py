"""Edibl data model.

Core shape:  Product (the "what") ──▶ StockLot (a real quantity of it, in a
Location, with a storage method + expiry).  Specialty attributes (wine vintage,
meat cut/weight, …) live in StockLot.attrs (JSON) so the schema stays flexible.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Float, Boolean, DateTime, ForeignKey, JSON
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


class User(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
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


class Product(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "products"
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(64), default="other")
    # Free-text display grouping, e.g. "Milk" for both Organic milk and Filtered
    # milk. Distinct products (own shelf-life + lots) that read as one group.
    family: Mapped[str] = mapped_column(String(255), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(64), default="", index=True)
    default_unit: Mapped[str] = mapped_column(String(32), default="count")
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

# How a lot (or part of one) left inventory. "Good" outcomes (eaten) teach us the
# item lasted at least that long; "loss" outcomes (spoiled/expired/discarded) are
# the signal that shortens the personalized shelf-life estimate for next time.
OUTCOMES = ("eaten", "spoiled", "expired", "discarded", "other")
GOOD_OUTCOMES = frozenset({"eaten", "used"})
LOSS_OUTCOMES = frozenset({"spoiled", "expired", "discarded"})


class StockLot(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "stock_lots"
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(32), default="count")
    storage_method: Mapped[str] = mapped_column(String(24), default="refrigerated")
    # Observed freshness / condition (free-form; FRESHNESS_LEVELS are suggestions);
    # "" when not tracked. Exposed as "freshness" in the API.
    state: Mapped[str] = mapped_column(String(32), default="")
    purchase_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    opened_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    expiry_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    # True when expiry was estimated from a shelf-life profile (vs printed date).
    expiry_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    cost: Mapped[float] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="")  # store/butcher/farm
    lot_code: Mapped[str] = mapped_column(String(64), default="")
    finished: Mapped[bool] = mapped_column(Boolean, default=False)  # fully consumed
    notes: Mapped[str] = mapped_column(Text, default="")
    # Specialty attributes: wine {vintage,varietal,region,producer,abv,volume_ml};
    # meat {cut,animal,weight_g,freeze_date,thaw_by,butcher_session}.
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
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


__all__ = [
    "db", "gen_uuid", "utcnow",
    "Group", "User", "ApiToken", "TOKEN_PREFIX", "generate_raw_token", "hash_token",
    "Location", "LOCATION_KINDS", "Product", "CATEGORIES", "UNITS",
    "StockLot", "STORAGE_METHODS", "FRESHNESS_LEVELS", "LIFECYCLE_STATES", "OUTCOMES",
    "GOOD_OUTCOMES", "LOSS_OUTCOMES",
    "ShelfLifeProfile", "ShoppingItem", "ConsumptionEvent", "PlannedItem",
]
