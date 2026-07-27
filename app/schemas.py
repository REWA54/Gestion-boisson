from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SetupInput(Schema):
    display_name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=200)
    collection_name: str = Field(default="Ma cave", min_length=1, max_length=100)


class LoginInput(Schema):
    username: str
    password: str


class CollectionInput(Schema):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    icon: str = Field(default="bottle", max_length=40)
    notifications_enabled: bool = True


class LocationInput(Schema):
    collection_id: int
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    kind: Literal[
        "place", "zone", "furniture", "rack", "shelf", "location", "box", "other"
    ] = "location"
    qr_code: str | None = Field(default=None, max_length=200)
    is_terminal: bool = True
    sort_order: int = 0


class VariantInput(Schema):
    vintage: str = Field(default="", max_length=30)
    volume_ml: int | None = Field(default=750, ge=1, le=100_000)
    batch: str = Field(default="", max_length=100)
    format: str = Field(default="bottle", max_length=60)
    edition: str = Field(default="", max_length=100)
    alcohol_percent: float | None = Field(default=None, ge=0, le=100)
    sku: str | None = Field(default=None, max_length=100)


class ReferenceInput(Schema):
    collection_id: int
    name: str = Field(min_length=1, max_length=180)
    producer: str = Field(default="", max_length=180)
    category: Literal[
        "wine", "sparkling", "beer", "cider", "spirit", "liqueur",
        "non_alcoholic", "other",
    ] = "other"
    subcategory: str = Field(default="", max_length=100)
    country: str = Field(default="", max_length=100)
    region: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    barcode: str | None = Field(default=None, max_length=100)
    alcohol_percent: float | None = Field(default=None, ge=0, le=100)
    tags: list[str] = Field(default_factory=list)
    variant: VariantInput = Field(default_factory=VariantInput)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))[:30]


class ReferencePatch(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    producer: str | None = Field(default=None, max_length=180)
    category: str | None = None
    subcategory: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    barcode: str | None = Field(default=None, max_length=100)
    alcohol_percent: float | None = Field(default=None, ge=0, le=100)
    tags: list[str] | None = None
    photo_path: str | None = None


class StockAddInput(Schema):
    operation_id: str = Field(min_length=8, max_length=100)
    variant_id: int
    location_id: int
    quantity: int = Field(ge=1, le=100_000)
    packaging: str = Field(default="unit", max_length=50)
    units_per_package: int = Field(default=1, ge=1, le=1000)
    package_state: Literal["open", "closed"] = "open"
    terminal: str = Field(default="", max_length=120)
    purchase_date: datetime | None = None
    seller: str = Field(default="", max_length=180)
    unit_price_cents: int | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=1000)


class StockWithdrawInput(Schema):
    operation_id: str = Field(min_length=8, max_length=100)
    variant_id: int
    location_id: int | None = None
    quantity: int = Field(default=1, ge=1, le=100_000)
    open_container: bool = False
    terminal: str = Field(default="", max_length=120)


class StockMoveInput(Schema):
    operation_id: str = Field(min_length=8, max_length=100)
    variant_id: int
    source_location_id: int
    target_location_id: int
    quantity: int = Field(ge=1, le=100_000)
    collision_strategy: Literal["reject", "swap"] = "reject"
    terminal: str = Field(default="", max_length=120)


class PackageOpenInput(Schema):
    operation_id: str = Field(min_length=8, max_length=100)
    stock_position_id: int
    packages: int = Field(default=1, ge=1, le=1000)
    terminal: str = Field(default="", max_length=120)


class ReservationInput(Schema):
    variant_id: int
    quantity: int = Field(ge=1, le=100_000)
    planned_for: datetime | None = None
    occasion: str = Field(default="", max_length=180)
    comment: str = Field(default="", max_length=1000)


class TastingInput(Schema):
    variant_id: int
    sentiment: Literal["liked", "neutral", "disliked"]
    comment: str = Field(default="", max_length=3000)
    meal: str = Field(default="", max_length=300)
    occasion: str = Field(default="", max_length=300)
    people: str = Field(default="", max_length=300)
    visibility: Literal["private", "family", "selected"] = "private"


class SyncOperation(Schema):
    operation_id: str
    action: Literal[
        "add", "withdraw", "move", "reference_create", "reserve", "taste"
    ]
    payload: dict[str, Any]
    created_at: datetime | None = None


class SyncInput(Schema):
    operations: list[SyncOperation] = Field(max_length=200)


class UserInput(Schema):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["admin", "manager", "member", "guest"] = "member"
    permissions: dict[str, bool] = Field(default_factory=dict)


class PartyModeInput(Schema):
    enabled: bool


class ProviderInput(Schema):
    name: str = Field(min_length=1, max_length=100)
    provider_type: Literal["generic_http", "openfoodfacts"] = "generic_http"
    enabled: bool = False
    priority: int = Field(default=100, ge=1, le=1000)
    supported_categories: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
