from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings


engine_options: dict = {"pool_pre_ping": True}
if settings.environment == "test":
    engine_options["poolclass"] = NullPool
else:
    engine_options.update(pool_size=10, max_overflow=20)
engine = create_async_engine(settings.async_database_url, **engine_options)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
