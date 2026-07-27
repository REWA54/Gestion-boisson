from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import UTC, datetime

from app.api.deps import current_user, has_permission, require
from app.db.session import get_db
from app.models import (
    BeverageReference,
    Reservation,
    StockEvent,
    StockPosition,
    Tasting,
    User,
    Variant,
)
from app.schemas import (
    ReferenceInput,
    PackageOpenInput,
    ReservationInput,
    StockAddInput,
    StockMoveInput,
    StockWithdrawInput,
    SyncInput,
    TastingInput,
)
from app.services.stock import (
    add_stock,
    event_result,
    existing_event,
    lock_operation,
    lock_variant,
    move_stock,
    position_state,
    restore_event_state,
    withdraw_stock,
)
from app.services.home_assistant import emit_event


router = APIRouter(prefix="/api", tags=["stock"])


async def create_reference_operation(
    db: AsyncSession, payload: dict, operation_id: str, user_id: int
) -> dict:
    await lock_operation(db, operation_id)
    duplicate = await existing_event(db, operation_id)
    if duplicate:
        reference_id = None
        if duplicate.variant_id:
            variant = await db.get(Variant, duplicate.variant_id)
            reference_id = variant.reference_id if variant else None
        return {
            **event_result(duplicate, True),
            "reference_id": reference_id,
        }
    reference_data = ReferenceInput.model_validate(payload["reference"])
    reference = BeverageReference(
        **reference_data.model_dump(exclude={"variant"}),
        data_sources={"name": "user"},
        photo_path=payload.get("photo_path"),
    )
    db.add(reference)
    await db.flush()
    variant = Variant(
        reference_id=reference.id, **reference_data.variant.model_dump()
    )
    db.add(variant)
    await db.flush()
    stock_payload = payload.get("stock")
    if stock_payload and int(stock_payload.get("quantity", 0)) > 0:
        result = await add_stock(
            db,
            StockAddInput.model_validate(
                {
                    **stock_payload,
                    "operation_id": operation_id,
                    "variant_id": variant.id,
                }
            ),
            user_id,
        )
        event = await existing_event(db, operation_id)
        assert event is not None
        event.event_type = "reference_create"
        event.payload = payload
        return {**result, "reference_id": reference.id}
    event = StockEvent(
        operation_id=operation_id,
        user_id=user_id,
        event_type="reference_create",
        variant_id=variant.id,
        quantity=0,
        state_before={"positions": []},
        state_after={"positions": []},
        payload=payload,
        terminal=str(payload.get("terminal", ""))[:120],
        sync_status="synced",
        created_at=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    return {**event_result(event), "reference_id": reference.id}


async def create_reservation_operation(
    db: AsyncSession, payload: dict, operation_id: str, user_id: int
) -> dict:
    await lock_operation(db, operation_id)
    duplicate = await existing_event(db, operation_id)
    if duplicate:
        return {
            **event_result(duplicate, True),
            "reservation_id": duplicate.state_after.get("reservation_id"),
        }
    model = ReservationInput.model_validate(payload)
    available = await db.scalar(
        select(func.coalesce(func.sum(StockPosition.quantity), 0)).where(
            StockPosition.variant_id == model.variant_id
        )
    )
    active = await db.scalar(
        select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
            Reservation.variant_id == model.variant_id,
            Reservation.status == "active",
        )
    )
    if int(available or 0) - int(active or 0) < model.quantity:
        raise HTTPException(status_code=409, detail="Quantité disponible insuffisante")
    item = Reservation(user_id=user_id, **model.model_dump())
    db.add(item)
    await db.flush()
    event = StockEvent(
        operation_id=operation_id,
        user_id=user_id,
        event_type="reservation_create",
        variant_id=model.variant_id,
        quantity=model.quantity,
        state_before={},
        state_after={"reservation_id": item.id},
        payload=model.model_dump(mode="json"),
        terminal="",
        sync_status="synced",
        created_at=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    return {**event_result(event), "reservation_id": item.id}


async def create_tasting_operation(
    db: AsyncSession, payload: dict, operation_id: str, user_id: int
) -> dict:
    await lock_operation(db, operation_id)
    duplicate = await existing_event(db, operation_id)
    if duplicate:
        return {
            **event_result(duplicate, True),
            "tasting_id": duplicate.state_after.get("tasting_id"),
        }
    model = TastingInput.model_validate(payload)
    if not await db.get(Variant, model.variant_id):
        raise HTTPException(status_code=404, detail="Variante introuvable")
    item = Tasting(user_id=user_id, **model.model_dump())
    db.add(item)
    await db.flush()
    event = StockEvent(
        operation_id=operation_id,
        user_id=user_id,
        event_type="tasting_create",
        variant_id=model.variant_id,
        quantity=0,
        state_before={},
        state_after={"tasting_id": item.id},
        payload=model.model_dump(mode="json"),
        terminal="",
        sync_status="synced",
        created_at=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    return {**event_result(event), "tasting_id": item.id}


@router.post("/offline/reference-create")
async def create_reference_idempotent(
    payload: dict,
    user: User = Depends(require("reference:add")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    operation_id = str(payload.get("operation_id", ""))
    if len(operation_id) < 8:
        raise HTTPException(status_code=422, detail="Identifiant d’opération invalide")
    try:
        result = await create_reference_operation(
            db, payload, operation_id, user.id
        )
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/offline/reserve")
async def create_reservation_idempotent(
    payload: dict,
    user: User = Depends(require("reservation:create")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    operation_id = str(payload.get("operation_id", ""))
    if len(operation_id) < 8:
        raise HTTPException(status_code=422, detail="Identifiant d’opération invalide")
    try:
        result = await create_reservation_operation(
            db, payload, operation_id, user.id
        )
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/offline/taste")
async def create_tasting_idempotent(
    payload: dict,
    user: User = Depends(require("tasting:add")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    operation_id = str(payload.get("operation_id", ""))
    if len(operation_id) < 8:
        raise HTTPException(status_code=422, detail="Identifiant d’opération invalide")
    try:
        result = await create_tasting_operation(db, payload, operation_id, user.id)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/stock/add")
async def add(
    data: StockAddInput,
    background_tasks: BackgroundTasks,
    user: User = Depends(require("stock:add")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await add_stock(db, data, user.id)
        await db.commit()
        background_tasks.add_task(emit_event, "reference_added", result)
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/stock/withdraw")
async def withdraw(
    data: StockWithdrawInput,
    background_tasks: BackgroundTasks,
    user: User = Depends(require("stock:withdraw")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await withdraw_stock(db, data, user.id)
        await db.commit()
        background_tasks.add_task(
            emit_event,
            "container_opened" if data.open_container else "quantity_withdrawn",
            result,
        )
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/stock/move")
async def move(
    data: StockMoveInput,
    background_tasks: BackgroundTasks,
    user: User = Depends(require("stock:move")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await move_stock(db, data, user.id)
        await db.commit()
        background_tasks.add_task(emit_event, "reference_moved", result)
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/stock/open-package")
async def open_package(
    data: PackageOpenInput,
    user: User = Depends(require("stock:add")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await lock_operation(db, data.operation_id)
        duplicate = await existing_event(db, data.operation_id)
        if duplicate:
            await db.commit()
            return event_result(duplicate, True)
        initial = await db.get(StockPosition, data.stock_position_id)
        if not initial:
            raise HTTPException(status_code=404, detail="Position de stock introuvable")
        await lock_variant(db, initial.variant_id)
        position = await db.scalar(
            select(StockPosition)
            .where(StockPosition.id == data.stock_position_id)
            .with_for_update()
        )
        if not position or position.closed_packages < data.packages:
            raise HTTPException(
                status_code=409, detail="Nombre de conditionnements fermés insuffisant"
            )
        before = position_state(position)
        position.closed_packages -= data.packages
        if position.closed_packages == 0:
            position.package_state = "open"
        after = position_state(position)
        event = StockEvent(
            operation_id=data.operation_id,
            user_id=user.id,
            event_type="packaging_opened",
            variant_id=position.variant_id,
            source_location_id=position.location_id,
            quantity=data.packages * position.units_per_package,
            state_before={"positions": [before]},
            state_after={"positions": [after]},
            payload=data.model_dump(mode="json"),
            terminal=data.terminal,
            sync_status="synced",
            created_at=datetime.now(UTC),
        )
        db.add(event)
        await db.flush()
        await db.commit()
        return event_result(event)
    except Exception:
        await db.rollback()
        raise


@router.get("/events")
async def events(
    limit: int = 60,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = (
        await db.scalars(
            select(StockEvent)
            .order_by(StockEvent.created_at.desc())
            .limit(min(max(limit, 1), 200))
        )
    ).all()
    return [
        {
            "id": item.id,
            "operation_id": item.operation_id,
            "event_type": item.event_type,
            "variant_id": item.variant_id,
            "source_location_id": item.source_location_id,
            "target_location_id": item.target_location_id,
            "quantity": item.quantity,
            "created_at": item.created_at,
            "undone": item.undone_by is not None,
            "can_undo": item.event_type
            in {"add", "withdraw", "open", "move", "packaging_opened"}
            and item.undone_by is None,
            "user_id": item.user_id,
        }
        for item in rows
    ]


@router.post("/events/{event_id}/undo")
async def undo(
    event_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require("stock:correct")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        event = await db.scalar(
            select(StockEvent).where(StockEvent.id == event_id).with_for_update()
        )
        if not event:
            raise HTTPException(status_code=404, detail="Événement introuvable")
        if event.undone_by:
            raise HTTPException(status_code=409, detail="Action déjà annulée")
        if event.event_type not in {
            "add", "withdraw", "open", "move", "packaging_opened"
        }:
            raise HTTPException(status_code=409, detail="Action non annulable")
        result = await restore_event_state(
            db, event, user.id, f"undo-{event.id}-{uuid.uuid4()}"
        )
        await db.commit()
        background_tasks.add_task(emit_event, "action_undone", result)
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/events/{event_id}/redo")
async def redo(
    event_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require("stock:correct")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        event = await db.scalar(
            select(StockEvent).where(StockEvent.id == event_id).with_for_update()
        )
        if not event or not event.undone_by:
            raise HTTPException(status_code=409, detail="Action non rétablissable")
        undo_event = await db.get(StockEvent, event.undone_by)
        if not undo_event:
            raise HTTPException(status_code=409, detail="Annulation introuvable")
        result = await restore_event_state(
            db, event, user.id, f"redo-{event.id}-{uuid.uuid4()}", redo=True
        )
        await db.commit()
        background_tasks.add_task(emit_event, "action_redone", result)
        return result
    except Exception:
        await db.rollback()
        raise


@router.post("/sync")
async def sync(
    data: SyncInput,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    results: list[dict] = []
    permissions = {
        "add": "stock:add",
        "withdraw": "stock:withdraw",
        "move": "stock:move",
        "reference_create": "reference:add",
        "reserve": "reservation:create",
        "taste": "tasting:add",
    }
    for operation in data.operations:
        try:
            permission = permissions[operation.action]
            if not has_permission(user, permission):
                raise HTTPException(
                    status_code=403, detail="Autorisation insuffisante"
                )
            await lock_operation(db, operation.operation_id)
            existing = await existing_event(db, operation.operation_id)
            if existing:
                results.append(
                    {
                        "operation_id": operation.operation_id,
                        "status": "applied",
                        "duplicate": True,
                        "result": event_result(existing, True),
                    }
                )
                await db.commit()
                continue
            payload = {**operation.payload, "operation_id": operation.operation_id}
            async with db.begin_nested():
                if operation.action == "add":
                    model = StockAddInput.model_validate(payload)
                    result = await add_stock(db, model, user.id)
                elif operation.action == "withdraw":
                    model = StockWithdrawInput.model_validate(payload)
                    result = await withdraw_stock(db, model, user.id)
                elif operation.action == "move":
                    model = StockMoveInput.model_validate(payload)
                    result = await move_stock(db, model, user.id)
                elif operation.action == "reference_create":
                    result = await create_reference_operation(
                        db, payload, operation.operation_id, user.id
                    )
                elif operation.action == "reserve":
                    result = await create_reservation_operation(
                        db, payload, operation.operation_id, user.id
                    )
                else:
                    result = await create_tasting_operation(
                        db, payload, operation.operation_id, user.id
                    )
            await db.commit()
            results.append(
                {
                    "operation_id": operation.operation_id,
                    "status": "applied",
                    "result": result,
                }
            )
        except (HTTPException, ValidationError) as exc:
            await db.rollback()
            detail = exc.detail if isinstance(exc, HTTPException) else exc.errors()
            results.append(
                {
                    "operation_id": operation.operation_id,
                    "status": "rejected",
                    "detail": detail,
                }
            )
    return {
        "results": results,
        "applied": sum(item["status"] == "applied" for item in results),
        "rejected": sum(item["status"] == "rejected" for item in results),
    }
