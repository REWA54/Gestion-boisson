from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Location,
    OpenContainer,
    PurchaseLot,
    StockEvent,
    StockPosition,
    Variant,
)
from app.schemas import StockAddInput, StockMoveInput, StockWithdrawInput


def utcnow() -> datetime:
    return datetime.now(UTC)


def position_state(position: StockPosition) -> dict[str, Any]:
    return {
        "id": position.id,
        "variant_id": position.variant_id,
        "location_id": position.location_id,
        "quantity": position.quantity,
        "reserved_quantity": position.reserved_quantity,
        "packaging": position.packaging,
        "units_per_package": position.units_per_package,
        "closed_packages": position.closed_packages,
        "package_state": position.package_state,
    }


async def snapshot_locations(
    db: AsyncSession, location_ids: Iterable[int]
) -> list[dict[str, Any]]:
    ids = sorted(set(location_ids))
    if not ids:
        return []
    rows = (
        await db.scalars(
            select(StockPosition)
            .where(StockPosition.location_id.in_(ids))
            .order_by(StockPosition.location_id)
        )
    ).all()
    return [position_state(row) for row in rows]


async def existing_event(db: AsyncSession, operation_id: str) -> StockEvent | None:
    return await db.scalar(
        select(StockEvent).where(StockEvent.operation_id == operation_id)
    )


async def lock_operation(db: AsyncSession, operation_id: str) -> None:
    # The two-key advisory namespace isolates operation locks from other app locks.
    await db.execute(
        select(func.pg_advisory_xact_lock(0x43454C4C, func.hashtext(operation_id)))
    )


async def lock_variant(db: AsyncSession, variant_id: int) -> Variant:
    variant = await db.scalar(
        select(Variant)
        .options(selectinload(Variant.reference))
        .where(Variant.id == variant_id)
        .with_for_update()
    )
    if not variant:
        raise HTTPException(status_code=404, detail="Variante introuvable")
    return variant


def event_result(event: StockEvent, duplicate: bool = False) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "operation_id": event.operation_id,
        "event_type": event.event_type,
        "quantity": event.quantity,
        "state": event.state_after,
        "duplicate": duplicate,
        "sync_status": event.sync_status,
    }


async def lock_locations(db: AsyncSession, ids: Iterable[int]) -> dict[int, Location]:
    unique_ids = sorted(set(ids))
    locations = (
        await db.scalars(
            select(Location)
            .where(Location.id.in_(unique_ids))
            .order_by(Location.id)
            .with_for_update()
        )
    ).all()
    found = {item.id: item for item in locations}
    if len(found) != len(unique_ids):
        raise HTTPException(status_code=404, detail="Emplacement introuvable")
    return found


async def add_stock(
    db: AsyncSession, data: StockAddInput, user_id: int
) -> dict[str, Any]:
    await lock_operation(db, data.operation_id)
    duplicate = await existing_event(db, data.operation_id)
    if duplicate:
        return event_result(duplicate, True)

    variant = await lock_variant(db, data.variant_id)
    locations = await lock_locations(db, [data.location_id])
    location = locations[data.location_id]
    if not location.is_terminal:
        raise HTTPException(
            status_code=409, detail="Choisissez un emplacement final"
        )
    if variant.reference.collection_id != location.collection_id:
        raise HTTPException(
            status_code=409,
            detail="La variante et l’emplacement appartiennent à des collections différentes",
        )

    current = await db.scalar(
        select(StockPosition)
        .where(StockPosition.location_id == data.location_id)
        .with_for_update()
    )
    before = [position_state(current)] if current else []
    if current and current.variant_id != data.variant_id:
        raise HTTPException(
            status_code=409,
            detail="Cet emplacement contient déjà une autre référence",
        )
    if current:
        current.quantity += data.quantity
        current.packaging = data.packaging
        current.units_per_package = data.units_per_package
        current.package_state = data.package_state
        if data.package_state == "closed":
            current.closed_packages += max(
                1, data.quantity // data.units_per_package
            )
    else:
        current = StockPosition(
            variant_id=data.variant_id,
            location_id=data.location_id,
            quantity=data.quantity,
            packaging=data.packaging,
            units_per_package=data.units_per_package,
            closed_packages=(
                max(1, data.quantity // data.units_per_package)
                if data.package_state == "closed"
                else 0
            ),
            package_state=data.package_state,
        )
        db.add(current)
        await db.flush()

    if data.purchase_date or data.seller or data.unit_price_cents is not None:
        db.add(
            PurchaseLot(
                variant_id=data.variant_id,
                purchased_at=data.purchase_date,
                seller=data.seller,
                quantity=data.quantity,
                packaging=data.packaging,
                unit_price_cents=data.unit_price_cents,
                total_price_cents=(
                    data.unit_price_cents * data.quantity
                    if data.unit_price_cents is not None
                    else None
                ),
                notes=data.notes,
            )
        )
    after = [position_state(current)]
    event = StockEvent(
        operation_id=data.operation_id,
        user_id=user_id,
        event_type="add",
        variant_id=data.variant_id,
        target_location_id=data.location_id,
        quantity=data.quantity,
        state_before={"positions": before},
        state_after={"positions": after},
        payload=data.model_dump(mode="json"),
        terminal=data.terminal,
        sync_status="synced",
        created_at=utcnow(),
    )
    db.add(event)
    await db.flush()
    return event_result(event)


async def withdraw_stock(
    db: AsyncSession, data: StockWithdrawInput, user_id: int
) -> dict[str, Any]:
    await lock_operation(db, data.operation_id)
    duplicate = await existing_event(db, data.operation_id)
    if duplicate:
        return event_result(duplicate, True)

    await lock_variant(db, data.variant_id)
    location_query = select(StockPosition.location_id).where(
        StockPosition.variant_id == data.variant_id,
        StockPosition.quantity > 0,
    )
    if data.location_id is not None:
        location_query = location_query.where(
            StockPosition.location_id == data.location_id
        )
    location_ids = list((await db.scalars(location_query)).all())
    await lock_locations(db, location_ids)
    query = (
        select(StockPosition)
        .where(
            StockPosition.variant_id == data.variant_id,
            StockPosition.quantity > 0,
        )
        .order_by(StockPosition.id)
        .with_for_update()
    )
    if data.location_id is not None:
        query = query.where(StockPosition.location_id == data.location_id)
    positions = list((await db.scalars(query)).all())
    available = sum(position.quantity for position in positions)
    if available < data.quantity:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "insufficient_stock",
                "message": "Stock insuffisant. Une autre personne a peut-être retiré la dernière unité.",
                "available": available,
            },
        )

    location_ids = [position.location_id for position in positions]
    before = [position_state(position) for position in positions]
    remaining = data.quantity
    opened_at_location: int | None = None
    for position in positions:
        if remaining == 0:
            break
        taken = min(remaining, position.quantity)
        position.quantity -= taken
        position.closed_packages = min(
            position.closed_packages,
            position.quantity // position.units_per_package,
        )
        if position.closed_packages == 0:
            position.package_state = "open"
        remaining -= taken
        opened_at_location = opened_at_location or position.location_id
        if position.quantity == 0:
            await db.delete(position)
    await db.flush()
    after = await snapshot_locations(db, location_ids)
    if data.open_container and opened_at_location:
        db.add(
            OpenContainer(
                variant_id=data.variant_id,
                location_id=opened_at_location,
                opened_at=utcnow(),
                remaining_level="almost_full",
                status="open",
            )
        )
    event = StockEvent(
        operation_id=data.operation_id,
        user_id=user_id,
        event_type="open" if data.open_container else "withdraw",
        variant_id=data.variant_id,
        source_location_id=data.location_id or opened_at_location,
        quantity=data.quantity,
        state_before={"positions": before},
        state_after={"positions": after},
        payload=data.model_dump(mode="json"),
        terminal=data.terminal,
        sync_status="synced",
        created_at=utcnow(),
    )
    db.add(event)
    await db.flush()
    return event_result(event)


async def move_stock(
    db: AsyncSession, data: StockMoveInput, user_id: int
) -> dict[str, Any]:
    await lock_operation(db, data.operation_id)
    duplicate = await existing_event(db, data.operation_id)
    if duplicate:
        return event_result(duplicate, True)
    if data.source_location_id == data.target_location_id:
        raise HTTPException(status_code=409, detail="La destination est identique")

    await lock_variant(db, data.variant_id)
    locations = await lock_locations(
        db, [data.source_location_id, data.target_location_id]
    )
    target_location = locations[data.target_location_id]
    if not target_location.is_terminal:
        raise HTTPException(
            status_code=409, detail="Choisissez un emplacement final"
        )
    positions = (
        await db.scalars(
            select(StockPosition)
            .where(
                StockPosition.location_id.in_(
                    [data.source_location_id, data.target_location_id]
                )
            )
            .order_by(StockPosition.location_id)
            .with_for_update()
        )
    ).all()
    source = next(
        (p for p in positions if p.location_id == data.source_location_id), None
    )
    target = next(
        (p for p in positions if p.location_id == data.target_location_id), None
    )
    if not source or source.variant_id != data.variant_id:
        raise HTTPException(status_code=404, detail="Stock source introuvable")
    if source.quantity < data.quantity:
        raise HTTPException(status_code=409, detail="Quantité source insuffisante")
    before = [position_state(position) for position in positions]

    if target and target.variant_id != data.variant_id:
        if data.collision_strategy != "swap":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "location_occupied",
                    "message": "La destination contient une autre référence",
                    "variant_id": target.variant_id,
                },
            )
        if data.quantity != source.quantity:
            raise HTTPException(
                status_code=409,
                detail="Un échange nécessite de déplacer tout le contenu source",
            )
        source_state = position_state(source)
        target_state = position_state(target)
        await db.delete(source)
        await db.delete(target)
        await db.flush()
        db.add(
            StockPosition(
                variant_id=source_state["variant_id"],
                location_id=data.target_location_id,
                quantity=source_state["quantity"],
                reserved_quantity=source_state["reserved_quantity"],
                packaging=source_state["packaging"],
                units_per_package=source_state["units_per_package"],
                closed_packages=source_state["closed_packages"],
                package_state=source_state["package_state"],
            )
        )
        db.add(
            StockPosition(
                variant_id=target_state["variant_id"],
                location_id=data.source_location_id,
                quantity=target_state["quantity"],
                reserved_quantity=target_state["reserved_quantity"],
                packaging=target_state["packaging"],
                units_per_package=target_state["units_per_package"],
                closed_packages=target_state["closed_packages"],
                package_state=target_state["package_state"],
            )
        )
    else:
        moved_closed = min(
            source.closed_packages,
            data.quantity // source.units_per_package,
        )
        source.quantity -= data.quantity
        source.closed_packages -= moved_closed
        if source.closed_packages == 0:
            source.package_state = "open"
        if target:
            target.quantity += data.quantity
            target.closed_packages += moved_closed
            if target.closed_packages:
                target.package_state = "closed"
        else:
            target = StockPosition(
                variant_id=data.variant_id,
                location_id=data.target_location_id,
                quantity=data.quantity,
                reserved_quantity=0,
                packaging=source.packaging,
                units_per_package=source.units_per_package,
                closed_packages=moved_closed,
                package_state="closed" if moved_closed else "open",
            )
            db.add(target)
        if source.quantity == 0:
            await db.delete(source)
    await db.flush()
    after = await snapshot_locations(
        db, [data.source_location_id, data.target_location_id]
    )
    event = StockEvent(
        operation_id=data.operation_id,
        user_id=user_id,
        event_type="move",
        variant_id=data.variant_id,
        source_location_id=data.source_location_id,
        target_location_id=data.target_location_id,
        quantity=data.quantity,
        state_before={"positions": before},
        state_after={"positions": after},
        payload=data.model_dump(mode="json"),
        terminal=data.terminal,
        sync_status="synced",
        created_at=utcnow(),
    )
    db.add(event)
    await db.flush()
    return event_result(event)


def normalize_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "variant_id", "location_id", "quantity", "reserved_quantity",
        "packaging", "units_per_package", "package_state",
        "closed_packages",
    )
    return sorted(
        [{key: item.get(key) for key in keys} for item in positions],
        key=lambda value: (value["location_id"], value["variant_id"]),
    )


async def restore_event_state(
    db: AsyncSession,
    event: StockEvent,
    user_id: int,
    operation_id: str,
    redo: bool = False,
) -> dict[str, Any]:
    expected = event.state_before if redo else event.state_after
    desired = event.state_after if redo else event.state_before
    all_states = expected.get("positions", []) + desired.get("positions", [])
    location_ids = {item["location_id"] for item in all_states}
    await lock_locations(db, location_ids)
    current = await snapshot_locations(db, location_ids)
    if normalize_positions(current) != normalize_positions(expected.get("positions", [])):
        raise HTTPException(
            status_code=409,
            detail="Cette action ne peut plus être modifiée car le stock a évolué depuis",
        )
    await db.execute(
        delete(StockPosition).where(StockPosition.location_id.in_(location_ids))
    )
    for item in desired.get("positions", []):
        clean = {key: value for key, value in item.items() if key != "id"}
        db.add(StockPosition(**clean))
    await db.flush()
    inverse = StockEvent(
        operation_id=operation_id,
        user_id=user_id,
        event_type="redo" if redo else "undo",
        variant_id=event.variant_id,
        source_location_id=event.source_location_id,
        target_location_id=event.target_location_id,
        quantity=event.quantity,
        state_before=expected,
        state_after=desired,
        payload={"target_event_id": event.id},
        terminal="",
        sync_status="synced",
        undo_of=event.id if not redo else None,
        created_at=utcnow(),
    )
    db.add(inverse)
    await db.flush()
    if not redo:
        event.undone_by = inverse.id
    return event_result(inverse)
