from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="CELLIER_", case_sensitive=False, extra="ignore"
    )

    app_name: str = "Cellier"
    environment: str = "production"
    database_url: str = ""
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "cellier"
    db_user: str = "cellier"
    db_password: str = "cellier"
    media_dir: Path = Path("./data/media")
    session_days: int = Field(default=90, ge=1, le=365)
    max_upload_mb: int = Field(default=15, ge=1, le=100)
    allowed_origins: str = ""
    home_assistant_url: str = ""
    home_assistant_token: str = ""
    home_assistant_webhook_secret: str = ""
    encryption_key: str = ""
    public_url: str = "http://localhost:8080"
    log_level: str = "INFO"

    @property
    def origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]

    @property
    def async_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            "postgresql+asyncpg://"
            f"{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{quote_plus(self.db_name)}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
