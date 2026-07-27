from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, has_permission, require
from app.db.session import get_db
from app.models import (
    BeverageReference,
    Collection,
    CollectionMember,
    Location,
    OpenContainer,
    Reservation,
    StockPosition,
    User,
    Variant,
)
from app.schemas import CollectionInput, LocationInput, ReferenceInput, ReferencePatch


router = APIRouter(prefix="/api", tags=["inventory"])


async def allowed_collection_ids(db: AsyncSession, user: User) -> list[int]:
    if user.role == "admin":
        return list((await db.scalars(select(Collection.id))).all())
    return list(
        (
            await db.scalars(
                select(CollectionMember.collection_id).where(
                    CollectionMember.user_id == user.id,
                    CollectionMember.can_view.is_(True),
                )
            )
        ).all()
    )


@router.get("/collections")
async def list_collections(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    ids = await allowed_collection_ids(db, user)
    if not ids:
        return []
    rows = (
        await db.execute(
            select(
                Collection,
                func.coalesce(func.sum(StockPosition.quantity), 0).label("quantity"),
                func.count(func.distinct(BeverageReference.id)).label("references"),
            )
            .outerjoin(
                BeverageReference, BeverageReference.collection_id == Collection.id
            )
            .outerjoin(Variant, Variant.reference_id == BeverageReference.id)
            .outerjoin(StockPosition, StockPosition.variant_id == Variant.id)
            .where(Collection.id.in_(ids))
            .group_by(Collection.id)
            .order_by(Collection.name)
        )
    ).all()
    return [
        {
            "id": collection.id,
            "name": collection.name,
            "description": collection.description,
            "icon": collection.icon,
            "notifications_enabled": collection.notifications_enabled,
            "quantity": int(quantity),
            "references": references,
        }
        for collection, quantity, references in rows
    ]


@router.post("/collections", status_code=201)
async def create_collection(
    data: CollectionInput,
    user: User = Depends(require("reference:add")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    collection = Collection(**data.model_dump())
    db.add(collection)
    await db.flush()
    db.add(CollectionMember(collection_id=collection.id, user_id=user.id))
    await db.commit()
    return {"id": collection.id, **data.model_dump()}


def location_path(location: Location, by_id: dict[int, Location]) -> str:
    parts = [location.name]
    seen = {location.id}
    parent_id = location.parent_id
    while parent_id and parent_id not in seen and parent_id in by_id:
        parent = by_id[parent_id]
        seen.add(parent.id)
        parts.append(parent.name)
        parent_id = parent.parent_id
    return " → ".join(reversed(parts))


@router.get("/locations")
async def list_locations(
    collection_id: int | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    allowed = await allowed_collection_ids(db, user)
    query = select(Location).where(Location.collection_id.in_(allowed))
    if collection_id:
        query = query.where(Location.collection_id == collection_id)
    locations = list(
        (await db.scalars(query.order_by(Location.sort_order, Location.name))).all()
    )
    by_id = {location.id: location for location in locations}
    occupied = set(
        (
            await db.scalars(
                select(StockPosition.location_id).where(
                    StockPosition.location_id.in_(by_id)
                )
            )
        ).all()
    )
    return [
        {
            "id": item.id,
            "collection_id": item.collection_id,
            "parent_id": item.parent_id,
            "name": item.name,
            "kind": item.kind,
            "qr_code": item.qr_code,
            "is_terminal": item.is_terminal,
            "sort_order": item.sort_order,
            "path": location_path(item, by_id),
            "occupied": item.id in occupied,
        }
        for item in locations
    ]


@router.post("/locations", status_code=201)
async def create_location(
    data: LocationInput,
    _: User = Depends(require("location:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if data.parent_id:
        parent = await db.get(Location, data.parent_id)
        if not parent or parent.collection_id != data.collection_id:
            raise HTTPException(status_code=409, detail="Parent invalide")
        parent.is_terminal = False
    location = Location(**data.model_dump())
    db.add(location)
    await db.commit()
    await db.refresh(location)
    return {"id": location.id, **data.model_dump()}


@router.get("/locations/by-qr/{qr_code}")
async def location_by_qr(
    qr_code: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    allowed = await allowed_collection_ids(db, user)
    location = await db.scalar(
        select(Location).where(
            Location.qr_code == qr_code, Location.collection_id.in_(allowed)
        )
    )
    if not location:
        raise HTTPException(status_code=404, detail="QR code inconnu")
    return {"id": location.id, "name": location.name, "collection_id": location.collection_id}


async def serialize_references(
    db: AsyncSession, references: list[BeverageReference], financial: bool
) -> list[dict[str, Any]]:
    if not references:
        return []
    ref_ids = [item.id for item in references]
    variants = list(
        (
            await db.scalars(
                select(Variant).where(Variant.reference_id.in_(ref_ids)).order_by(Variant.id)
            )
        ).all()
    )
    variant_ids = [item.id for item in variants]
    positions = []
    reservations: dict[int, int] = {}
    opened: dict[int, int] = {}
    if variant_ids:
        positions = list(
            (
                await db.scalars(
                    select(StockPosition).where(
                        StockPosition.variant_id.in_(variant_ids),
                        StockPosition.quantity > 0,
                    )
                )
            ).all()
        )
        reservations = dict(
            (
                await db.execute(
                    select(Reservation.variant_id, func.sum(Reservation.quantity))
                    .where(
                        Reservation.variant_id.in_(variant_ids),
                        Reservation.status == "active",
                    )
                    .group_by(Reservation.variant_id)
                )
            ).all()
        )
        opened = dict(
            (
                await db.execute(
                    select(OpenContainer.variant_id, func.count(OpenContainer.id))
                    .where(
                        OpenContainer.variant_id.in_(variant_ids),
                        OpenContainer.status == "open",
                    )
                    .group_by(OpenContainer.variant_id)
                )
            ).all()
        )
    location_ids = {position.location_id for position in positions}
    locations = list(
        (
            await db.scalars(select(Location).where(Location.id.in_(location_ids)))
        ).all()
    ) if location_ids else []
    by_location = {item.id: item for item in locations}
    all_locations = list(
        (
            await db.scalars(
                select(Location).where(
                    Location.collection_id.in_({r.collection_id for r in references})
                )
            )
        ).all()
    )
    all_by_location = {item.id: item for item in all_locations}
    positions_by_variant: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in positions:
        loc = by_location[item.location_id]
        positions_by_variant[item.variant_id].append(
            {
                "id": item.id,
                "location_id": item.location_id,
                "location_name": loc.name,
                "location_path": location_path(loc, all_by_location),
                "quantity": item.quantity,
                "reserved_quantity": item.reserved_quantity,
                "packaging": item.packaging,
                "units_per_package": item.units_per_package,
                "closed_packages": item.closed_packages,
                "package_state": item.package_state,
            }
        )
    variants_by_ref: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in variants:
        item_positions = positions_by_variant[item.id]
        variants_by_ref[item.reference_id].append(
            {
                "id": item.id,
                "vintage": item.vintage,
                "volume_ml": item.volume_ml,
                "batch": item.batch,
                "format": item.format,
                "edition": item.edition,
                "alcohol_percent": item.alcohol_percent,
                "quantity": sum(value["quantity"] for value in item_positions),
                "reserved_quantity": int(reservations.get(item.id, 0)),
                "open_containers": int(opened.get(item.id, 0)),
                "positions": item_positions,
            }
        )
    return [
        {
            "id": ref.id,
            "collection_id": ref.collection_id,
            "name": ref.name,
            "producer": ref.producer,
            "category": ref.category,
            "subcategory": ref.subcategory,
            "country": ref.country,
            "region": ref.region,
            "description": ref.description,
            "photo_path": ref.photo_path,
            "barcode": ref.barcode,
            "alcohol_percent": ref.alcohol_percent,
            "tags": ref.tags,
            "data_sources": ref.data_sources,
            "variants": variants_by_ref[ref.id],
            "quantity": sum(v["quantity"] for v in variants_by_ref[ref.id]),
            "financial_visible": financial,
        }
        for ref in references
    ]


@router.get("/references")
async def list_references(
    q: str = Query(default="", max_length=180),
    collection_id: int | None = None,
    in_stock: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    allowed = await allowed_collection_ids(db, user)
    query = select(BeverageReference).where(
        BeverageReference.collection_id.in_(allowed)
    )
    if collection_id:
        query = query.where(BeverageReference.collection_id == collection_id)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                BeverageReference.name.ilike(term),
                BeverageReference.producer.ilike(term),
                BeverageReference.region.ilike(term),
                BeverageReference.country.ilike(term),
                BeverageReference.barcode == q.strip(),
                cast(BeverageReference.tags, Text).ilike(term),
            )
        )
    if in_stock:
        query = (
            query.join(Variant)
            .join(StockPosition)
            .where(StockPosition.quantity > 0)
            .distinct()
        )
    refs = list(
        (await db.scalars(query.order_by(BeverageReference.name).limit(limit))).all()
    )
    return await serialize_references(
        db, refs, has_permission(user, "finance:view_purchase")
    )


@router.get("/references/{reference_id}")
async def get_reference(
    reference_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    allowed = await allowed_collection_ids(db, user)
    ref = await db.scalar(
        select(BeverageReference).where(
            BeverageReference.id == reference_id,
            BeverageReference.collection_id.in_(allowed),
        )
    )
    if not ref:
        raise HTTPException(status_code=404, detail="Référence introuvable")
    return (await serialize_references(
        db, [ref], has_permission(user, "finance:view_purchase")
    ))[0]


@router.post("/references", status_code=status.HTTP_201_CREATED)
async def create_reference(
    data: ReferenceInput,
    _: User = Depends(require("reference:add")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    collection = await db.get(Collection, data.collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection introuvable")
    values = data.model_dump(exclude={"variant"})
    ref = BeverageReference(**values, data_sources={"name": "user"})
    db.add(ref)
    await db.flush()
    variant = Variant(reference_id=ref.id, **data.variant.model_dump())
    db.add(variant)
    await db.commit()
    await db.refresh(variant)
    result = (await serialize_references(db, [ref], True))[0]
    return result


@router.patch("/references/{reference_id}")
async def update_reference(
    reference_id: int,
    data: ReferencePatch,
    _: User = Depends(require("reference:edit")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ref = await db.get(BeverageReference, reference_id)
    if not ref:
        raise HTTPException(status_code=404, detail="Référence introuvable")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(ref, key, value)
        ref.data_sources[key] = "user"
    await db.commit()
    return (await serialize_references(db, [ref], True))[0]
