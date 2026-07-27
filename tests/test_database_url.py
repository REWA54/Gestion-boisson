from __future__ import annotations

from configparser import ConfigParser

from app.db.url import alembic_sync_url


def test_alembic_url_preserves_url_encoded_credentials() -> None:
    async_url = (
        "postgresql+asyncpg://cellier:"
        "secret%2Bwith%2Freserved%25characters@db:5432/cellier"
    )

    escaped_url = alembic_sync_url(async_url)

    parser = ConfigParser()
    parser.add_section("alembic")
    parser.set("alembic", "sqlalchemy.url", escaped_url)

    assert parser.get("alembic", "sqlalchemy.url") == (
        "postgresql+psycopg://cellier:"
        "secret%2Bwith%2Freserved%25characters@db:5432/cellier"
    )
