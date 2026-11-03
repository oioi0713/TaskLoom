from pydantic import ValidationError
import pytest

from app.config import Settings


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "TaskLoom"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.max_workers == 3


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
    )

    assert settings.llm_model == ""
    assert settings.llm_api_key is None
    assert settings.llm_base_url is None
