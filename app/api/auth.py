from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_PERMISSIONS, current_user, require
from app.core.config import settings
from app.core.security import (
    hash_password,
    make_session,
    token_digest,
    verify_password,
)
from app.db.session import get_db
from app.models import Collection, CollectionMember, Session, User
from app.schemas import LoginInput, SetupInput, UserInput


router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


def public_user(user: User) -> dict:
    base = ROLE_PERMISSIONS.get(user.role, set())
    permissions = {name: True for name in base if name != "*"} | user.permissions
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "permissions": permissions,
        "is_admin": "*" in base,
        "active": user.active,
    }


@router.get("/setup-status")
async def setup_status(db: AsyncSession = Depends(get_db)) -> dict:
    count = await db.scalar(select(func.count(User.id)))
    return {"needs_setup": count == 0}


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    data: SetupInput, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    async with db.begin():
        # A transaction-scoped advisory lock makes first-run setup single-writer.
        await db.execute(select(func.pg_advisory_xact_lock(0x43454C4C)))
        count = await db.scalar(select(func.count(User.id)))
        if count:
            raise HTTPException(status_code=409, detail="Installation déjà configurée")
        user = User(
            username=data.username.lower(),
            display_name=data.display_name,
            password_hash=hash_password(data.password),
            role="admin",
            permissions={},
        )
        collection = Collection(name=data.collection_name, icon="wine")
        db.add_all([user, collection])
        await db.flush()
        db.add(CollectionMember(collection_id=collection.id, user_id=user.id))
        token, digest, expires = make_session()
        db.add(
            Session(
                user_id=user.id,
                token_hash=digest,
                expires_at=expires,
                created_at=datetime.now(UTC),
            )
        )
    response.set_cookie(
        "cellier_session",
        token,
        httponly=True,
        secure=settings.public_url.startswith("https://"),
        samesite="strict",
        expires=expires,
    )
    return {"token": token, "expires_at": expires, "user": public_user(user)}


@router.post("/login")
async def login(
    data: LoginInput, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    user = await db.scalar(
        select(User).where(
            func.lower(User.username) == data.username.lower(), User.active.is_(True)
        )
    )
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Identifiants incorrects")
    token, digest, expires = make_session()
    db.add(
        Session(
            user_id=user.id,
            token_hash=digest,
            expires_at=expires,
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()
    response.set_cookie(
        "cellier_session",
        token,
        httponly=True,
        secure=settings.public_url.startswith("https://"),
        samesite="strict",
        expires=expires,
    )
    return {"token": token, "expires_at": expires, "user": public_user(user)}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return public_user(user)


@router.post("/logout")
async def logout(
    response: Response,
    authorization: str | None = Header(default=None),
    cellier_session: str | None = Cookie(default=None),
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    raw_token = authorization[7:] if authorization and authorization.startswith("Bearer ") else cellier_session
    if raw_token:
        await db.execute(
            delete(Session).where(Session.token_hash == token_digest(raw_token))
        )
        await db.commit()
    response.delete_cookie("cellier_session", samesite="strict")
    return {"ok": True}


@users_router.get("")
async def list_users(
    _: User = Depends(require("user:manage")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    users = (await db.scalars(select(User).order_by(User.display_name))).all()
    return [public_user(user) for user in users]


@users_router.post("", status_code=201)
async def create_user(
    data: UserInput,
    _: User = Depends(require("user:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = await db.scalar(
        select(User).where(func.lower(User.username) == data.username.lower())
    )
    if existing:
        raise HTTPException(status_code=409, detail="Ce nom d’utilisateur existe déjà")
    user = User(
        username=data.username.lower(),
        display_name=data.display_name,
        password_hash=hash_password(data.password),
        role=data.role,
        permissions=data.permissions,
    )
    db.add(user)
    await db.flush()
    collection_ids = (await db.scalars(select(Collection.id))).all()
    db.add_all(
        [CollectionMember(collection_id=value, user_id=user.id) for value in collection_ids]
    )
    await db.commit()
    return public_user(user)
