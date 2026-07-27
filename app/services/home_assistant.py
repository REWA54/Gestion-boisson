from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


async def emit_event(event_type: str, data: dict[str, Any]) -> None:
    if not settings.home_assistant_url or not settings.home_assistant_token:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{settings.home_assistant_url.rstrip('/')}/api/events/cellier_{event_type}",
                headers={
                    "Authorization": f"Bearer {settings.home_assistant_token}",
                    "Content-Type": "application/json",
                },
                json={"event_data": data},
            )
    except httpx.HTTPError:
        # Stock operations must never fail because Home Assistant is unavailable.
        return

