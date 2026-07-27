from __future__ import annotations

import asyncio
import io
import uuid

from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw

from app.main import app


def operation_id(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4()}"


async def add_stock(
    client: AsyncClient, inventory: dict, quantity: int, op: str | None = None
):
    return await client.post(
        "/api/stock/add",
        json={
            "operation_id": op or operation_id("add"),
            "variant_id": inventory["variant_id"],
            "location_id": inventory["source_id"],
            "quantity": quantity,
            "packaging": "unit",
            "units_per_package": 1,
            "package_state": "open",
        },
    )


async def test_setup_login_and_persistent_session(client: AsyncClient, admin: dict):
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    second_setup = await client.post(
        "/api/auth/setup",
        json={
            "display_name": "Other",
            "username": "other",
            "password": "another-long-password",
            "collection_name": "Other",
        },
    )
    assert second_setup.status_code == 409

    login = await client.post(
        "/api/auth/login",
        json={"username": "LAURIS", "password": "mot-de-passe-tres-solide"},
    )
    assert login.status_code == 200
    assert login.json()["token"]


async def test_add_is_idempotent(client: AsyncClient, admin: dict, inventory: dict):
    op = operation_id("same")
    first = await add_stock(client, inventory, 3, op)
    second = await add_stock(client, inventory, 3, op)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True

    reference = await client.get(f"/api/references/{inventory['reference_id']}")
    assert reference.json()["quantity"] == 3


async def test_concurrent_duplicate_operation_is_applied_once(
    client: AsyncClient, admin: dict, inventory: dict
):
    token = client.headers["Authorization"]
    op = operation_id("same-concurrent")

    async def add_from_phone():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": token},
        ) as concurrent_client:
            return await add_stock(concurrent_client, inventory, 2, op)

    first, second = await asyncio.gather(add_from_phone(), add_from_phone())
    assert first.status_code == second.status_code == 200
    assert sorted([first.json()["duplicate"], second.json()["duplicate"]]) == [
        False,
        True,
    ]
    reference = await client.get(f"/api/references/{inventory['reference_id']}")
    assert reference.json()["quantity"] == 2


async def test_concurrent_last_unit_never_goes_negative(
    client: AsyncClient, admin: dict, inventory: dict
):
    assert (await add_stock(client, inventory, 1)).status_code == 200
    token = client.headers["Authorization"]

    async def withdraw(op: str):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": token},
        ) as concurrent_client:
            return await concurrent_client.post(
                "/api/stock/withdraw",
                json={
                    "operation_id": op,
                    "variant_id": inventory["variant_id"],
                    "location_id": inventory["source_id"],
                    "quantity": 1,
                },
            )

    responses = await asyncio.gather(
        withdraw(operation_id("phone-a")),
        withdraw(operation_id("phone-b")),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    reference = await client.get(f"/api/references/{inventory['reference_id']}")
    assert reference.json()["quantity"] == 0
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["detail"]["code"] == "insufficient_stock"


async def test_move_and_occupancy_invariant(
    client: AsyncClient, admin: dict, inventory: dict
):
    await add_stock(client, inventory, 4)
    moved = await client.post(
        "/api/stock/move",
        json={
            "operation_id": operation_id("move"),
            "variant_id": inventory["variant_id"],
            "source_location_id": inventory["source_id"],
            "target_location_id": inventory["target_id"],
            "quantity": 2,
        },
    )
    assert moved.status_code == 200, moved.text
    state = (await client.get(f"/api/references/{inventory['reference_id']}")).json()
    quantities = {
        position["location_id"]: position["quantity"]
        for position in state["variants"][0]["positions"]
    }
    assert quantities == {
        inventory["source_id"]: 2,
        inventory["target_id"]: 2,
    }

    other = (
        await client.post(
            "/api/references",
            json={
                "collection_id": inventory["collection_id"],
                "name": "Whisky concurrent",
                "category": "spirit",
                "variant": {"volume_ml": 700},
            },
        )
    ).json()
    collision = await client.post(
        "/api/stock/add",
        json={
            "operation_id": operation_id("occupied"),
            "variant_id": other["variants"][0]["id"],
            "location_id": inventory["target_id"],
            "quantity": 1,
        },
    )
    assert collision.status_code == 409


async def test_undo_refuses_to_overwrite_later_changes(
    client: AsyncClient, admin: dict, inventory: dict
):
    added = await add_stock(client, inventory, 2)
    event_id = added.json()["event_id"]
    await add_stock(client, inventory, 1)

    stale_undo = await client.post(f"/api/events/{event_id}/undo")
    assert stale_undo.status_code == 409
    assert "stock a évolué" in stale_undo.json()["detail"]


async def test_offline_sync_is_ordered_and_reports_conflict(
    client: AsyncClient, admin: dict, inventory: dict
):
    result = await client.post(
        "/api/sync",
        json={
            "operations": [
                {
                    "operation_id": operation_id("offline-add"),
                    "action": "add",
                    "payload": {
                        "variant_id": inventory["variant_id"],
                        "location_id": inventory["source_id"],
                        "quantity": 1,
                    },
                },
                {
                    "operation_id": operation_id("offline-withdraw"),
                    "action": "withdraw",
                    "payload": {
                        "variant_id": inventory["variant_id"],
                        "location_id": inventory["source_id"],
                        "quantity": 2,
                    },
                },
            ]
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["applied"] == 1
    assert result.json()["rejected"] == 1
    state = (await client.get(f"/api/references/{inventory['reference_id']}")).json()
    assert state["quantity"] == 1


async def test_offline_reference_creation_is_atomic_and_idempotent(
    client: AsyncClient, admin: dict, inventory: dict
):
    op = operation_id("offline-reference")
    payload = {
        "operation_id": op,
        "reference": {
            "collection_id": inventory["collection_id"],
            "name": "Bière hors ligne",
            "producer": "Brasserie locale",
            "category": "beer",
            "variant": {"volume_ml": 330, "format": "bottle"},
        },
        "stock": {
            "location_id": inventory["source_id"],
            "quantity": 6,
            "packaging": "pack",
            "units_per_package": 6,
            "package_state": "closed",
        },
    }
    first = await client.post("/api/offline/reference-create", json=payload)
    second = await client.post("/api/offline/reference-create", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["reference_id"] == second.json()["reference_id"]
    assert second.json()["duplicate"] is True
    item = (
        await client.get(f"/api/references/{first.json()['reference_id']}")
    ).json()
    assert item["quantity"] == 6


async def test_offline_reservation_and_tasting_are_idempotent(
    client: AsyncClient, admin: dict, inventory: dict
):
    await add_stock(client, inventory, 3)
    reserve_op = operation_id("reserve")
    taste_op = operation_id("taste")
    batch = {
        "operations": [
            {
                "operation_id": reserve_op,
                "action": "reserve",
                "payload": {
                    "variant_id": inventory["variant_id"],
                    "quantity": 1,
                    "occasion": "Dîner",
                },
            },
            {
                "operation_id": taste_op,
                "action": "taste",
                "payload": {
                    "variant_id": inventory["variant_id"],
                    "sentiment": "liked",
                    "comment": "Très bon",
                },
            },
        ]
    }
    assert (await client.post("/api/sync", json=batch)).json()["applied"] == 2
    replay = await client.post("/api/sync", json=batch)
    assert replay.json()["applied"] == 2
    assert all(item["duplicate"] for item in replay.json()["results"])
    assert len((await client.get("/api/reservations")).json()) == 1
    assert len((await client.get("/api/tastings")).json()) == 1


async def test_recognition_photo_is_cached_locally(
    client: AsyncClient, admin: dict, inventory: dict
):
    image = Image.new("RGB", (1000, 500), "white")
    ImageDraw.Draw(image).text((80, 180), "CUVEE DES TESTS 2022", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    files = {"file": ("label.jpg", buffer.getvalue(), "image/jpeg")}

    first = await client.post(
        f"/api/recognition/photo?collection_id={inventory['collection_id']}",
        files=files,
    )
    second = await client.post(
        f"/api/recognition/photo?collection_id={inventory['collection_id']}",
        files=files,
    )
    assert first.status_code == 200, first.text
    assert first.json()["requires_confirmation"] is True
    assert second.json()["source"] == "cache"


async def test_provider_credentials_are_never_returned(
    client: AsyncClient, admin: dict
):
    created = await client.post(
        "/api/providers",
        json={
            "name": "Reconnaissance privée",
            "provider_type": "generic_http",
            "enabled": False,
            "priority": 10,
            "config": {
                "endpoint": "https://recognition.example.test/v1/labels",
                "api_key": "secret-test-key",
            },
        },
    )
    assert created.status_code == 201, created.text
    listed = (await client.get("/api/providers")).json()
    assert listed[0]["has_configuration"] is True
    assert "secret-test-key" not in str(listed)
    assert "config" not in listed[0]


async def test_party_mode_can_always_be_disabled(
    client: AsyncClient, admin: dict
):
    enabled = await client.put(
        "/api/settings/party-mode",
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json() == {"enabled": True}
    assert (await client.get("/api/dashboard")).json()["party_mode"] is True

    disabled = await client.put(
        "/api/settings/party-mode",
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json() == {"enabled": False}
    assert (await client.get("/api/dashboard")).json()["party_mode"] is False
