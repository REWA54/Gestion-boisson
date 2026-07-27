from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault(
    "CELLIER_DATABASE_URL",
    "postgresql+asyncpg://cellier:cellier-test@127.0.0.1:55432/cellier_test",
)
os.environ.setdefault("CELLIER_MEDIA_DIR", "/tmp/cellier-test-media")
os.environ.setdefault("CELLIER_ENVIRONMENT", "test")

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
async def clean_database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


@pytest.fixture
async def admin(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/auth/setup",
        json={
            "display_name": "Lauris",
            "username": "lauris",
            "password": "mot-de-passe-tres-solide",
            "collection_name": "Cave principale",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    client.headers["Authorization"] = f"Bearer {body['token']}"
    return body


@pytest.fixture
async def inventory(client: AsyncClient, admin: dict) -> dict:
    collections = (await client.get("/api/collections")).json()
    collection_id = collections[0]["id"]
    source = (
        await client.post(
            "/api/locations",
            json={
                "collection_id": collection_id,
                "name": "Emplacement A1",
                "kind": "location",
                "is_terminal": True,
            },
        )
    ).json()
    target = (
        await client.post(
            "/api/locations",
            json={
                "collection_id": collection_id,
                "name": "Emplacement A2",
                "kind": "location",
                "is_terminal": True,
            },
        )
    ).json()
    reference = (
        await client.post(
            "/api/references",
            json={
                "collection_id": collection_id,
                "name": "Cuvée des tests",
                "producer": "Domaine CI",
                "category": "wine",
                "region": "Bourgogne",
                "tags": ["garde"],
                "variant": {"vintage": "2022", "volume_ml": 750},
            },
        )
    ).json()
    return {
        "collection_id": collection_id,
        "source_id": source["id"],
        "target_id": target["id"],
        "reference_id": reference["id"],
        "variant_id": reference["variants"][0]["id"],
    }

