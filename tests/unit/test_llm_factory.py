import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm.factory import MissingLLMConfigurationError, create_chat_model


def test_missing_llm_configuration_fails_before_provider_creation() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="",
        llm_api_key=None,
    )

    with pytest.raises(
        MissingLLMConfigurationError,
        match="TASKLOOM_LLM_MODEL.*TASKLOOM_LLM_API_KEY",
    ):
        create_chat_model(settings)


def test_llm_api_key_is_secret_in_settings_representation() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="test-model",
        llm_api_key=SecretStr("super-secret-value"),
    )

    assert "super-secret-value" not in repr(settings)
    assert settings.llm_api_key.get_secret_value() == "super-secret-value"


def test_configured_factory_creates_provider_without_api_request() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="test-model",
        llm_api_key=SecretStr("test-key"),
        llm_base_url="https://example.invalid/v1",
    )

    model = create_chat_model(settings)

    assert model.model_name == "test-model"
    assert str(model.openai_api_base).rstrip("/") == "https://example.invalid/v1"
    assert model.max_retries == 0
    assert model.max_tokens == 4096
