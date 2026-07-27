from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import token_digest
from app.db.session import get_db
from app.models import Session, User


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "manager": {
        "reference:add", "reference:edit", "stock:add", "stock:withdraw",
        "stock:move", "stock:correct", "location:manage", "finance:view_purchase",
        "finance:view_value", "finance:edit", "document:view", "data:export",
        "data:import", "reservation:manage", "tasting:add",
    },
    "member": {
        "stock:withdraw", "stock:move", "reservation:create", "tasting:add"
    },
    "guest": {"stock:view"},
}


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
    cellier_session: Annotated[str | None, Cookie()] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    raw_token = (
        authorization[7:]
        if authorization and authorization.startswith("Bearer ")
        else cellier_session
    )
    if not raw_token:
        raise HTTPException(status_code=401, detail="Session requise")
    query = (
        select(User)
        .join(Session, Session.user_id == User.id)
        .where(
            Session.token_hash == token_digest(raw_token),
            Session.expires_at > datetime.now(UTC),
            User.active.is_(True),
        )
    )
    user = await db.scalar(query)
    if not user:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée")
    return user


def has_permission(user: User, permission: str) -> bool:
    base = ROLE_PERMISSIONS.get(user.role, set())
    if "*" in base:
        return True
    if permission in user.permissions:
        return bool(user.permissions[permission])
    return permission in base


def require(permission: str):
    async def dependency(user: User = Depends(current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n’avez pas l’autorisation nécessaire",
            )
        return user

    return dependency
