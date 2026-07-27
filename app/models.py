from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


JSONType = JSONB


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="member")
    permissions: Mapped[dict[str, bool]] = mapped_column(
        MutableDict.as_mutable(JSONType), default=dict
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship()


class Collection(Base, TimestampMixin):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(40), default="bottle")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CollectionMember(Base):
    __tablename__ = "collection_members"
    __table_args__ = (UniqueConstraint("collection_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    can_view: Mapped[bool] = mapped_column(Boolean, default=True)


class Location(Base, TimestampMixin):
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("collection_id", "parent_id", "name"),
        CheckConstraint("id IS DISTINCT FROM parent_id", name="location_not_self_parent"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30), default="location")
    qr_code: Mapped[str | None] = mapped_column(String(200), unique=True)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class BeverageReference(Base, TimestampMixin):
    __tablename__ = "beverage_references"
    __table_args__ = (
        Index("ix_reference_search", "collection_id", "name", "producer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(180))
    producer: Mapped[str] = mapped_column(String(180), default="")
    category: Mapped[str] = mapped_column(String(40), default="other", index=True)
    subcategory: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(100), default="")
    region: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    photo_path: Mapped[str | None] = mapped_column(Text)
    barcode: Mapped[str | None] = mapped_column(String(100), index=True)
    alcohol_percent: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSONType), default=list)
    external_data: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONType), default=dict
    )
    data_sources: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONType), default=dict
    )
    personal_data: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONType), default=dict
    )
    variants: Mapped[list["Variant"]] = relationship(
        back_populates="reference", cascade="all, delete-orphan"
    )


class Variant(Base, TimestampMixin):
    __tablename__ = "variants"
    __table_args__ = (
        UniqueConstraint(
            "reference_id", "vintage", "volume_ml", "batch", "format", "edition"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("beverage_references.id", ondelete="CASCADE"), index=True
    )
    vintage: Mapped[str] = mapped_column(String(30), default="")
    volume_ml: Mapped[int | None] = mapped_column(Integer)
    batch: Mapped[str] = mapped_column(String(100), default="")
    format: Mapped[str] = mapped_column(String(60), default="bottle")
    edition: Mapped[str] = mapped_column(String(100), default="")
    alcohol_percent: Mapped[float | None] = mapped_column(Float)
    sku: Mapped[str | None] = mapped_column(String(100), unique=True)
    reference: Mapped[BeverageReference] = relationship(back_populates="variants")


class PurchaseLot(Base, TimestampMixin):
    __tablename__ = "purchase_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seller: Mapped[str] = mapped_column(String(180), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    packaging: Mapped[str] = mapped_column(String(50), default="unit")
    unit_price_cents: Mapped[int | None] = mapped_column(Integer)
    total_price_cents: Mapped[int | None] = mapped_column(Integer)
    document_path: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")


class StockPosition(Base, TimestampMixin):
    __tablename__ = "stock_positions"
    __table_args__ = (
        UniqueConstraint("location_id"),
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("reserved_quantity >= 0", name="reserved_nonnegative"),
        CheckConstraint("reserved_quantity <= quantity", name="reserved_lte_quantity"),
        CheckConstraint("units_per_package > 0", name="units_per_package_positive"),
        CheckConstraint("closed_packages >= 0", name="closed_packages_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[int] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    packaging: Mapped[str] = mapped_column(String(50), default="unit")
    units_per_package: Mapped[int] = mapped_column(Integer, default=1)
    closed_packages: Mapped[int] = mapped_column(Integer, default=0)
    package_state: Mapped[str] = mapped_column(String(20), default="open")


class OpenContainer(Base, TimestampMixin):
    __tablename__ = "open_containers"

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[int] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT")
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    remaining_level: Mapped[str] = mapped_column(String(30), default="almost_full")
    consume_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reservation(Base, TimestampMixin):
    __tablename__ = "reservations"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    planned_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occasion: Mapped[str] = mapped_column(String(180), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Tasting(Base, TimestampMixin):
    __tablename__ = "tastings"

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    sentiment: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    meal: Mapped[str] = mapped_column(String(300), default="")
    occasion: Mapped[str] = mapped_column(String(300), default="")
    people: Mapped[str] = mapped_column(String(300), default="")
    photo_path: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(20), default="private")


class StockEvent(Base):
    __tablename__ = "stock_events"
    __table_args__ = (
        Index("ix_stock_events_created_desc", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("variants.id", ondelete="SET NULL"), index=True
    )
    source_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL")
    )
    target_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL")
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    state_before: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    state_after: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    terminal: Mapped[str] = mapped_column(String(120), default="")
    sync_status: Mapped[str] = mapped_column(String(30), default="synced")
    undone_by: Mapped[int | None] = mapped_column(
        ForeignKey("stock_events.id", ondelete="SET NULL")
    )
    undo_of: Mapped[int | None] = mapped_column(
        ForeignKey("stock_events.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RecognitionProvider(Base, TimestampMixin):
    __tablename__ = "recognition_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    provider_type: Mapped[str] = mapped_column(String(60), default="generic")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    supported_categories: Mapped[list[str]] = mapped_column(JSONType, default=list)
    config_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecognitionCache(Base, TimestampMixin):
    __tablename__ = "recognition_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    query_text: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(100), default="local")
    image_path: Mapped[str | None] = mapped_column(Text)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
