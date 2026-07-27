from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    if not settings.encryption_key:
        raise ValueError("CELLIER_ENCRYPTION_KEY n’est pas configurée")
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(settings.encryption_key.encode()).digest()
    )
    return Fernet(derived)


def encrypt_config(value: dict[str, Any]) -> bytes:
    return _fernet().encrypt(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    )


def decrypt_config(value: bytes | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(_fernet().decrypt(value))
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}

