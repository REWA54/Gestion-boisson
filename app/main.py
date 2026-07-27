from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import auth, features, inventory, operations
from app.api.deps import current_user
from app.core.config import settings
from app.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    yield
    await engine.dispose()


app = FastAPI(
    title="Cellier API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
if settings.origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(auth.users_router)
app.include_router(inventory.router)
app.include_router(operations.router)
app.include_router(features.router)


@app.get("/api/health")
async def health() -> dict:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ok", "service": "cellier"}


settings.media_dir.mkdir(parents=True, exist_ok=True)


@app.get("/media/{filename}", include_in_schema=False)
async def protected_media(
    filename: str, _: object = Depends(current_user)
) -> FileResponse:
    if filename != Path(filename).name:
        raise HTTPException(status_code=404)
    path = settings.media_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(STATIC_DIR / "manifest.webmanifest")

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "sw.js", media_type="application/javascript"
        )

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        requested = (STATIC_DIR / path).resolve()
        if requested.is_relative_to(STATIC_DIR.resolve()) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(STATIC_DIR / "index.html")
