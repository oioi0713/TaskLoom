"""Application configuration loaded from environment variables and ``.env``."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated TaskLoom runtime settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TASKLOOM_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "TaskLoom"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    debug: bool = False
    max_workers: int = Field(default=3, ge=1, le=32)


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance per process."""

    return Settings()
