"""Application configuration loaded from environment variables and ``.env``."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    llm_provider: Literal["openai_compatible"] = "openai_compatible"
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: int = Field(default=60, ge=1, le=300)
    llm_structured_output_method: Literal["function_calling", "json_schema"] = (
        "function_calling"
    )

    @field_validator("llm_model", mode="before")
    @classmethod
    def normalize_model_name(cls, value: object) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("llm_api_key", "llm_base_url", mode="before")
    @classmethod
    def blank_optional_llm_values_are_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance per process."""

    return Settings()
