from __future__ import annotations


def alembic_sync_url(async_url: str) -> str:
    """Return a synchronous URL safe for Alembic's ConfigParser.

    Alembic stores runtime options in a ConfigParser, where percent signs have
    interpolation semantics. URL-encoded credentials therefore need percent
    signs doubled before being assigned with ``set_main_option``. ConfigParser
    restores the original value when Alembic reads it.
    """

    sync_url = async_url.replace("+asyncpg", "+psycopg")
    return sync_url.replace("%", "%%")
