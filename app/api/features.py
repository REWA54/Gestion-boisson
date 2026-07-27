from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import secrets
import subprocess
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, has_permission, require
from app.api.inventory import allowed_collection_ids
from app.core.config import settings
from app.core.crypto import decrypt_config, encrypt_config
from app.db.session import get_db
from app.models import (
    BeverageReference,
    Collection,
    Location,
    OpenContainer,
    PurchaseLot,
    RecognitionCache,
    RecognitionProvider,
    Reservation,
    Setting,
    StockEvent,
    StockPosition,
    Tasting,
    User,
    Variant,
)
from app.schemas import (
    PartyModeInput,
    ProviderInput,
    ReservationInput,
    StockWithdrawInput,
    TastingInput,
)
from app.services.stock import withdraw_stock


router = APIRouter(prefix="/api", tags=["features"])


def validate_image(content: bytes) -> tuple[str, str]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
            image_format = (image.format or "").lower()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="Image invalide") from exc
    suffixes = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}
    if image_format not in suffixes:
        raise HTTPException(status_code=415, detail="Format d’image non pris en charge")
    return suffixes[image_format], hashlib.sha256(content).hexdigest()


def run_tesseract(path: str) -> str:
    try:
        def execute(language: str):
            return subprocess.run(
                ["tesseract", path, "stdout", "-l", language, "--psm", "11"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )

        result = execute("fra+eng")
        if result.returncode and not result.stdout.strip():
            result = execute("eng")
        return result.stdout.strip()[:5000]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def reference_match(item: BeverageReference, text: str) -> float:
    candidate = " ".join(
        [
            item.name,
            item.producer,
            item.region,
            item.country,
            " ".join(item.tags),
        ]
    )
    return max(
        fuzz.token_set_ratio(text, candidate),
        fuzz.partial_ratio(text, candidate),
    ) / 100


@router.get("/dashboard")
async def dashboard(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    total = await db.scalar(
        select(func.coalesce(func.sum(StockPosition.quantity), 0))
    )
    references = await db.scalar(
        select(func.count(func.distinct(Variant.reference_id)))
        .join(StockPosition, StockPosition.variant_id == Variant.id)
        .where(StockPosition.quantity > 0)
    )
    opened = await db.scalar(
        select(func.count(OpenContainer.id)).where(OpenContainer.status == "open")
    )
    reservations = await db.scalar(
        select(func.count(Reservation.id)).where(Reservation.status == "active")
    )
    party = await db.get(Setting, "party_mode")
    recent = (
        await db.scalars(
            select(StockEvent).order_by(StockEvent.created_at.desc()).limit(5)
        )
    ).all()
    return {
        "greeting_name": user.display_name,
        "total_quantity": int(total or 0),
        "reference_count": int(references or 0),
        "open_containers": int(opened or 0),
        "reservations": int(reservations or 0),
        "party_mode": bool(party and party.value.get("enabled")),
        "pending_sync": 0,
        "recent_events": [
            {
                "id": item.id,
                "event_type": item.event_type,
                "quantity": item.quantity,
                "variant_id": item.variant_id,
                "created_at": item.created_at,
            }
            for item in recent
        ],
    }


@router.put("/settings/party-mode")
async def party_mode(
    data: PartyModeInput,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    setting = await db.get(Setting, "party_mode")
    if setting:
        setting.value = {"enabled": data.enabled, "changed_by": user.id}
        setting.updated_at = datetime.now(UTC)
    else:
        setting = Setting(
            key="party_mode",
            value={"enabled": data.enabled, "changed_by": user.id},
            updated_at=datetime.now(UTC),
        )
        db.add(setting)
    await db.commit()
    return {"enabled": data.enabled}


@router.get("/reservations")
async def reservations(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        await db.execute(
            select(
                Reservation,
                BeverageReference.name,
                Variant.vintage,
                User.display_name,
            )
            .join(Variant, Variant.id == Reservation.variant_id)
            .join(BeverageReference, BeverageReference.id == Variant.reference_id)
            .join(User, User.id == Reservation.user_id)
            .where(Reservation.status == "active")
            .order_by(Reservation.planned_for.nulls_last(), Reservation.created_at.desc())
        )
    ).all()
    return [
        {
            "id": item.id,
            "variant_id": item.variant_id,
            "quantity": item.quantity,
            "planned_for": item.planned_for,
            "occasion": item.occasion,
            "comment": item.comment,
            "user_id": item.user_id,
            "user_name": user_name,
            "reference_name": name,
            "vintage": vintage,
            "mine": item.user_id == user.id,
        }
        for item, name, vintage, user_name in rows
    ]


@router.post("/reservations", status_code=201)
async def create_reservation(
    data: ReservationInput,
    user: User = Depends(require("reservation:create")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    available = await db.scalar(
        select(func.coalesce(func.sum(StockPosition.quantity), 0)).where(
            StockPosition.variant_id == data.variant_id
        )
    )
    active = await db.scalar(
        select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
            Reservation.variant_id == data.variant_id,
            Reservation.status == "active",
        )
    )
    if int(available or 0) - int(active or 0) < data.quantity:
        raise HTTPException(status_code=409, detail="Quantité disponible insuffisante")
    item = Reservation(user_id=user.id, **data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id, **data.model_dump(mode="json")}


@router.delete("/reservations/{reservation_id}")
async def cancel_reservation(
    reservation_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    item = await db.get(Reservation, reservation_id)
    if not item:
        raise HTTPException(status_code=404, detail="Réservation introuvable")
    if item.user_id != user.id and not has_permission(user, "reservation:manage"):
        raise HTTPException(status_code=403, detail="Action interdite")
    item.status = "cancelled"
    item.cancelled_at = datetime.now(UTC)
    await db.commit()
    return {"ok": True}


@router.get("/tastings")
async def tastings(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        await db.execute(
            select(Tasting, BeverageReference.name, Variant.vintage, User.display_name)
            .join(Variant, Variant.id == Tasting.variant_id)
            .join(BeverageReference, BeverageReference.id == Variant.reference_id)
            .join(User, User.id == Tasting.user_id)
            .where(
                (Tasting.user_id == user.id)
                | (Tasting.visibility.in_(["family", "selected"]))
            )
            .order_by(Tasting.created_at.desc())
            .limit(200)
        )
    ).all()
    return [
        {
            "id": item.id,
            "variant_id": item.variant_id,
            "sentiment": item.sentiment,
            "comment": item.comment,
            "meal": item.meal,
            "occasion": item.occasion,
            "visibility": item.visibility,
            "created_at": item.created_at,
            "reference_name": name,
            "vintage": vintage,
            "user_name": user_name,
            "mine": item.user_id == user.id,
        }
        for item, name, vintage, user_name in rows
    ]


@router.post("/tastings", status_code=201)
async def create_tasting(
    data: TastingInput,
    user: User = Depends(require("tasting:add")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not await db.get(Variant, data.variant_id):
        raise HTTPException(status_code=404, detail="Variante introuvable")
    item = Tasting(user_id=user.id, **data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id, **data.model_dump()}


@router.get("/finance/summary")
async def finance_summary(
    _: User = Depends(require("finance:view_value")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    total = await db.scalar(
        select(
            func.coalesce(
                func.sum(PurchaseLot.quantity * PurchaseLot.unit_price_cents), 0
            )
        )
    )
    by_category = (
        await db.execute(
            select(
                BeverageReference.category,
                func.coalesce(
                    func.sum(PurchaseLot.quantity * PurchaseLot.unit_price_cents), 0
                ),
            )
            .join(Variant, Variant.reference_id == BeverageReference.id)
            .join(PurchaseLot, PurchaseLot.variant_id == Variant.id)
            .group_by(BeverageReference.category)
        )
    ).all()
    return {
        "currency": "EUR",
        "purchase_value_cents": int(total or 0),
        "by_category": [
            {"category": category, "value_cents": int(value)}
            for category, value in by_category
        ],
        "estimation": None,
    }


@router.get("/recommendations")
async def recommendations(
    context: str = "",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(
                Variant,
                BeverageReference,
                func.sum(StockPosition.quantity).label("quantity"),
            )
            .join(BeverageReference, BeverageReference.id == Variant.reference_id)
            .join(StockPosition, StockPosition.variant_id == Variant.id)
            .where(StockPosition.quantity > 0)
            .group_by(Variant.id, BeverageReference.id)
            .limit(300)
        )
    ).all()
    liked = set(
        (
            await db.scalars(
                select(BeverageReference.category)
                .join(Variant, Variant.reference_id == BeverageReference.id)
                .join(Tasting, Tasting.variant_id == Variant.id)
                .where(Tasting.user_id == user.id, Tasting.sentiment == "liked")
            )
        ).all()
    )
    opened = set(
        (
            await db.scalars(
                select(OpenContainer.variant_id).where(OpenContainer.status == "open")
            )
        ).all()
    )
    reserved = dict(
        (
            await db.execute(
                select(Reservation.variant_id, func.sum(Reservation.quantity))
                .where(Reservation.status == "active")
                .group_by(Reservation.variant_id)
            )
        ).all()
    )
    context_words = {
        value
        for value in context.lower().replace(",", " ").split()
        if len(value) >= 3
    }
    scored: list[tuple[float, dict[str, Any]]] = []
    for variant, reference, quantity in rows:
        reasons: list[str] = []
        score = 0.0
        if variant.id in opened:
            score += 3
            reasons.append("un contenant est déjà ouvert")
        if reference.category in liked:
            score += 2
            reasons.append("vous avez aimé des boissons de cette catégorie")
        matching_tags = context_words.intersection(
            {tag.lower() for tag in reference.tags}
        )
        if matching_tags:
            score += 4
            reasons.append(f"ses tags correspondent à {', '.join(sorted(matching_tags))}")
        available = int(quantity) - int(reserved.get(variant.id, 0))
        if available >= 3:
            score += 1
            reasons.append(f"{available} unités non réservées sont disponibles")
        if not reasons:
            reasons.append("elle est disponible dans votre stock")
        scored.append(
            (
                score,
                {
                    "reference_id": reference.id,
                    "variant_id": variant.id,
                    "name": reference.name,
                    "producer": reference.producer,
                    "category": reference.category,
                    "quantity": int(quantity),
                    "reasons": reasons,
                    "score": score,
                },
            )
        )
    scored.sort(key=lambda value: (value[0], value[1]["quantity"]), reverse=True)
    return {
        "context": context,
        "explainable": True,
        "results": [item for _, item in scored[:5]],
    }


@router.post("/media")
async def upload_media(
    file: UploadFile = File(...),
    _: User = Depends(require("reference:add")),
) -> dict:
    allowed = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }
    suffix = allowed.get(file.content_type or "")
    if not suffix:
        raise HTTPException(status_code=415, detail="Format de fichier non pris en charge")
    content = await file.read((settings.max_upload_mb * 1024 * 1024) + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux")
    if (file.content_type or "").startswith("image/"):
        validate_image(content)
    elif not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Document PDF invalide")
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_hex(20)}{suffix}"
    path = settings.media_dir / filename
    path.write_bytes(content)
    return {"path": f"/media/{filename}", "size": len(content)}


@router.get("/providers")
async def list_providers(
    _: User = Depends(require("provider:manage")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(RecognitionProvider).order_by(
                RecognitionProvider.priority, RecognitionProvider.name
            )
        )
    ).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "provider_type": item.provider_type,
            "enabled": item.enabled,
            "priority": item.priority,
            "supported_categories": item.supported_categories,
            "has_configuration": bool(item.config_encrypted),
            "last_used_at": item.last_used_at,
        }
        for item in rows
    ]


@router.post("/providers", status_code=201)
async def create_provider(
    data: ProviderInput,
    _: User = Depends(require("provider:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        encrypted = encrypt_config(data.config) if data.config else None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    item = RecognitionProvider(
        name=data.name,
        provider_type=data.provider_type,
        enabled=data.enabled,
        priority=data.priority,
        supported_categories=data.supported_categories,
        config_encrypted=encrypted,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id, "name": item.name, "enabled": item.enabled}


@router.put("/providers/{provider_id}")
async def update_provider(
    provider_id: int,
    data: ProviderInput,
    _: User = Depends(require("provider:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await db.get(RecognitionProvider, provider_id)
    if not item:
        raise HTTPException(status_code=404, detail="Fournisseur introuvable")
    try:
        encrypted = encrypt_config(data.config) if data.config else item.config_encrypted
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    item.name = data.name
    item.provider_type = data.provider_type
    item.enabled = data.enabled
    item.priority = data.priority
    item.supported_categories = data.supported_categories
    item.config_encrypted = encrypted
    await db.commit()
    return {"id": item.id, "name": item.name, "enabled": item.enabled}


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: int,
    _: User = Depends(require("provider:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    item = await db.get(RecognitionProvider, provider_id)
    if not item:
        raise HTTPException(status_code=404, detail="Fournisseur introuvable")
    await db.delete(item)
    await db.commit()
    return {"ok": True}


async def query_external_provider(
    provider: RecognitionProvider,
    *,
    query: str = "",
    image_content: bytes | None = None,
    image_type: str = "image/jpeg",
) -> list[dict[str, Any]]:
    config = decrypt_config(provider.config_encrypted)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            if provider.provider_type == "openfoodfacts" and query.isdigit():
                base = config.get(
                    "base_url", "https://world.openfoodfacts.org/api/v2/product"
                ).rstrip("/")
                response = await client.get(f"{base}/{query}.json")
                response.raise_for_status()
                product = response.json().get("product") or {}
                if not product:
                    return []
                return [
                    {
                        "name": product.get("product_name") or product.get("generic_name"),
                        "producer": product.get("brands", ""),
                        "category": "non_alcoholic",
                        "photo_url": product.get("image_front_url"),
                        "barcode": query,
                        "confidence": 0.82,
                        "source": provider.name,
                        "external_data": {
                            "quantity": product.get("quantity"),
                            "countries": product.get("countries"),
                        },
                    }
                ]
            if provider.provider_type == "generic_http":
                endpoint = str(config.get("endpoint", ""))
                if not endpoint.startswith(("https://", "http://")):
                    return []
                headers = {"Accept": "application/json"}
                if config.get("api_key"):
                    headers["Authorization"] = f"Bearer {config['api_key']}"
                if image_content is not None:
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        data={"query": query},
                        files={"image": ("label.jpg", image_content, image_type)},
                    )
                else:
                    response = await client.post(
                        endpoint, headers=headers, json={"query": query}
                    )
                response.raise_for_status()
                body = response.json()
                return list(body.get("results", []))[:10]
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    return []


@router.get("/recognition")
async def recognize(
    q: str,
    collection_id: int | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    allowed = await allowed_collection_ids(db, user)
    if collection_id and collection_id not in allowed:
        raise HTTPException(status_code=404, detail="Collection introuvable")
    query = select(BeverageReference).where(
        BeverageReference.collection_id.in_(allowed)
    )
    if collection_id:
        query = query.where(BeverageReference.collection_id == collection_id)
    term = f"%{q.strip()}%"
    query = query.where(
        (BeverageReference.barcode == q.strip())
        | BeverageReference.name.ilike(term)
        | BeverageReference.producer.ilike(term)
    ).limit(8)
    rows = (await db.scalars(query)).all()
    results = [
        {
            "reference_id": item.id,
            "name": item.name,
            "producer": item.producer,
            "category": item.category,
            "photo_path": item.photo_path,
            "confidence": 1.0 if item.barcode == q.strip() else 0.72,
            "source": "local",
        }
        for item in rows
    ]
    if not results:
        providers = (
            await db.scalars(
                select(RecognitionProvider)
                .where(RecognitionProvider.enabled.is_(True))
                .order_by(RecognitionProvider.priority)
            )
        ).all()
        for provider in providers:
            external = await query_external_provider(provider, query=q.strip())
            if external:
                results.extend(external)
                provider.last_used_at = datetime.now(UTC)
                await db.commit()
                break
    return {
        "query": q,
        "source": "local" if rows else ("external" if results else "none"),
        "requires_confirmation": True,
        "results": results,
    }


@router.post("/recognition/photo")
async def recognize_photo(
    file: UploadFile = File(...),
    collection_id: int | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    content = await file.read((settings.max_upload_mb * 1024 * 1024) + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image trop volumineuse")
    suffix, digest = validate_image(content)
    allowed = await allowed_collection_ids(db, user)
    if collection_id and collection_id not in allowed:
        raise HTTPException(status_code=404, detail="Collection introuvable")
    scoped_digest = hashlib.sha256(
        f"{digest}:{collection_id or '*'}:{','.join(map(str, sorted(allowed)))}".encode()
    ).hexdigest()
    cached = await db.scalar(
        select(RecognitionCache).where(RecognitionCache.cache_key == scoped_digest)
    )
    if cached:
        return {
            "source": "cache",
            "ocr_text": cached.query_text,
            "requires_confirmation": True,
            "results": cached.results,
        }

    settings.media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"scan-{digest}{suffix}"
    path = settings.media_dir / filename
    path.write_bytes(content)
    ocr_text = await asyncio.to_thread(run_tesseract, str(path))
    query = select(BeverageReference).where(
        BeverageReference.collection_id.in_(allowed)
    )
    if collection_id:
        query = query.where(BeverageReference.collection_id == collection_id)
    references = list((await db.scalars(query)).all())
    local_results = []
    if ocr_text:
        scored = sorted(
            (
                (reference_match(item, ocr_text), item)
                for item in references
            ),
            key=lambda value: value[0],
            reverse=True,
        )
        local_results = [
            {
                "reference_id": item.id,
                "name": item.name,
                "producer": item.producer,
                "category": item.category,
                "photo_path": item.photo_path,
                "confidence": round(score, 3),
                "source": "ocr_local",
            }
            for score, item in scored[:8]
            if score >= 0.38
        ]

    results = local_results
    provider_name = "local"
    if not results:
        providers = (
            await db.scalars(
                select(RecognitionProvider)
                .where(RecognitionProvider.enabled.is_(True))
                .order_by(RecognitionProvider.priority)
            )
        ).all()
        for provider in providers:
            external = await query_external_provider(
                provider,
                query=ocr_text,
                image_content=content,
                image_type=file.content_type or "image/jpeg",
            )
            if external:
                results = external[:10]
                provider_name = provider.name
                provider.last_used_at = datetime.now(UTC)
                break
    db.add(
        RecognitionCache(
            cache_key=scoped_digest,
            query_text=ocr_text,
            provider=provider_name,
            image_path=f"/media/{filename}",
            results=results,
        )
    )
    await db.commit()
    return {
        "source": "local" if local_results else provider_name,
        "ocr_text": ocr_text,
        "photo_path": f"/media/{filename}",
        "requires_confirmation": True,
        "needs_back_label": not bool(results),
        "results": results,
    }


@router.delete("/recognition/cache")
async def clear_recognition_cache(
    _: User = Depends(require("provider:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    rows = list((await db.scalars(select(RecognitionCache))).all())
    removed = 0
    for item in rows:
        if item.image_path:
            path = settings.media_dir / item.image_path.rsplit("/", 1)[-1]
            if path.exists() and path.name.startswith("scan-"):
                path.unlink()
        await db.delete(item)
        removed += 1
    await db.commit()
    return {"removed": removed}


@router.get("/home-assistant/state")
async def home_assistant_state(
    _: User = Depends(require("home_assistant:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    total = await db.scalar(select(func.coalesce(func.sum(StockPosition.quantity), 0)))
    opened = await db.scalar(
        select(func.count(OpenContainer.id)).where(OpenContainer.status == "open")
    )
    reservations_count = await db.scalar(
        select(func.count(Reservation.id)).where(Reservation.status == "active")
    )
    return {
        "total_quantity": int(total or 0),
        "open_containers": int(opened or 0),
        "reservations": int(reservations_count or 0),
    }


async def ha_state_payload(db: AsyncSession) -> dict[str, int]:
    total = await db.scalar(select(func.coalesce(func.sum(StockPosition.quantity), 0)))
    opened = await db.scalar(
        select(func.count(OpenContainer.id)).where(OpenContainer.status == "open")
    )
    reservations_count = await db.scalar(
        select(func.count(Reservation.id)).where(Reservation.status == "active")
    )
    return {
        "total_quantity": int(total or 0),
        "open_containers": int(opened or 0),
        "reservations": int(reservations_count or 0),
    }


def verify_ha_secret(value: str | None) -> None:
    expected = settings.home_assistant_webhook_secret
    if not expected:
        raise HTTPException(
            status_code=503, detail="Webhook Home Assistant non configuré"
        )
    if not value or not hmac.compare_digest(value, expected):
        raise HTTPException(status_code=401, detail="Secret webhook invalide")


@router.get("/home-assistant/webhook/state")
async def home_assistant_webhook_state(
    x_cellier_secret: str | None = Header(default=None, alias="X-Cellier-Secret"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    verify_ha_secret(x_cellier_secret)
    return await ha_state_payload(db)


@router.post("/home-assistant/webhook/command")
async def home_assistant_command(
    command: dict[str, Any],
    x_cellier_secret: str | None = Header(default=None, alias="X-Cellier-Secret"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    verify_ha_secret(x_cellier_secret)
    action = command.get("action")
    if action in {"refresh", "sync"}:
        return {"ok": True, "state": await ha_state_payload(db)}
    if action == "party_mode":
        enabled = bool(command.get("enabled"))
        setting = await db.get(Setting, "party_mode")
        if setting:
            setting.value = {"enabled": enabled, "changed_by": "home_assistant"}
            setting.updated_at = datetime.now(UTC)
        else:
            db.add(
                Setting(
                    key="party_mode",
                    value={"enabled": enabled, "changed_by": "home_assistant"},
                    updated_at=datetime.now(UTC),
                )
            )
        await db.commit()
        return {"ok": True, "enabled": enabled}
    actor = await db.scalar(
        select(User).where(User.active.is_(True)).order_by(
            (User.role == "admin").desc(), User.id
        )
    )
    if not actor:
        raise HTTPException(status_code=409, detail="Aucun utilisateur actif")
    if action == "withdraw":
        payload = dict(command.get("payload") or {})
        payload.setdefault("operation_id", f"ha-{secrets.token_urlsafe(24)}")
        payload.setdefault("quantity", 1)
        try:
            result = await withdraw_stock(
                db, StockWithdrawInput.model_validate(payload), actor.id
            )
            await db.commit()
            return {"ok": True, "result": result}
        except Exception:
            await db.rollback()
            raise
    if action == "reserve":
        data = ReservationInput.model_validate(command.get("payload") or {})
        item = Reservation(user_id=actor.id, **data.model_dump())
        db.add(item)
        await db.commit()
        return {"ok": True, "reservation_id": item.id}
    if action == "inventory":
        return {"ok": True, "requested": "inventory"}
    raise HTTPException(status_code=422, detail="Commande non prise en charge")


@router.post("/home-assistant/test")
async def test_home_assistant(
    _: User = Depends(require("home_assistant:manage")),
) -> dict:
    if not settings.home_assistant_url or not settings.home_assistant_token:
        raise HTTPException(status_code=409, detail="Home Assistant n’est pas configuré")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{settings.home_assistant_url.rstrip('/')}/api/",
            headers={"Authorization": f"Bearer {settings.home_assistant_token}"},
        )
    return {"ok": response.is_success, "status_code": response.status_code}


@router.get("/export")
async def export_data(
    _: User = Depends(require("data:export")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    collections = (await db.scalars(select(Collection))).all()
    locations = (await db.scalars(select(Location))).all()
    references = (await db.scalars(select(BeverageReference))).all()
    variants = (await db.scalars(select(Variant))).all()
    positions = (await db.scalars(select(StockPosition))).all()
    return {
        "format": "cellier-export",
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "collections": [
            {"id": x.id, "name": x.name, "description": x.description, "icon": x.icon}
            for x in collections
        ],
        "locations": [
            {
                "id": x.id, "collection_id": x.collection_id, "parent_id": x.parent_id,
                "name": x.name, "kind": x.kind, "qr_code": x.qr_code,
                "is_terminal": x.is_terminal,
            }
            for x in locations
        ],
        "references": [
            {
                "id": x.id, "collection_id": x.collection_id, "name": x.name,
                "producer": x.producer, "category": x.category, "tags": x.tags,
                "barcode": x.barcode,
            }
            for x in references
        ],
        "variants": [
            {
                "id": x.id, "reference_id": x.reference_id, "vintage": x.vintage,
                "volume_ml": x.volume_ml, "batch": x.batch, "format": x.format,
                "edition": x.edition,
            }
            for x in variants
        ],
        "stock_positions": [
            {
                "variant_id": x.variant_id, "location_id": x.location_id,
                "quantity": x.quantity, "reserved_quantity": x.reserved_quantity,
                "packaging": x.packaging, "units_per_package": x.units_per_package,
                "package_state": x.package_state,
            }
            for x in positions
        ],
    }
