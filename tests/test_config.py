from pydantic import ValidationError
import pytest

from app.config import Settings


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "TaskLoom"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.max_workers == 3
    assert settings.checkpoint_backend == "memory"
    assert settings.redis_url is None
    assert settings.checkpoint_ttl_minutes == 1440
    assert settings.session_memory_ttl_hours == 72
    assert settings.session_memory_max_items == 50
    assert settings.llm_context_window_tokens == 32768


def test_settings_read_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKLOOM_ENVIRONMENT", "test")
    monkeypatch.setenv("TASKLOOM_DEBUG", "true")
    monkeypatch.setenv("TASKLOOM_MAX_WORKERS", "5")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.debug is True
    assert settings.max_workers == 5


def test_invalid_worker_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_workers=0)


def test_blank_llm_values_are_normalized() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="  ",
        llm_api_key="  ",
        llm_base_url="",
        redis_url="  ",
    )

    assert settings.llm_model == ""
    assert settings.llm_api_key is None
    assert settings.llm_base_url is None
    assert settings.redis_url is None


def test_redis_checkpoint_settings_are_loaded_as_secret() -> None:
    settings = Settings(
        _env_file=None,
        checkpoint_backend="redis",
        redis_url="redis://:password@127.0.0.1:6379/0",
        checkpoint_ttl_minutes=720,
    )

    assert settings.checkpoint_backend == "redis"
    assert settings.redis_url is not None
    assert settings.redis_url.get_secret_value().startswith("redis://")
    assert settings.checkpoint_ttl_minutes == 720
