"""Create configured chat models without exposing credentials to agents."""

from typing import Any

from app.config import Settings, get_settings


class MissingLLMConfigurationError(ValueError):
    """Raised before network access when required local LLM settings are absent."""


def create_chat_model(settings: Settings | None = None) -> Any:
    """Create an OpenAI-compatible LangChain chat model.

    Importing the provider lazily keeps configuration validation testable without
    starting a network request. Creating the object also performs no model call.
    """

    resolved = settings or get_settings()
    missing: list[str] = []
    if not resolved.llm_model:
        missing.append("TASKLOOM_LLM_MODEL")
    if resolved.llm_api_key is None:
        missing.append("TASKLOOM_LLM_API_KEY")
    if missing:
        raise MissingLLMConfigurationError(
            "Missing LLM configuration: " + ", ".join(missing)
        )

    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": resolved.llm_model,
        "api_key": resolved.llm_api_key.get_secret_value(),
        "temperature": resolved.llm_temperature,
        "timeout": resolved.llm_timeout_seconds,
        "max_retries": resolved.llm_max_retries,
        "max_tokens": resolved.llm_planner_max_output_tokens,
    }
    if resolved.llm_base_url:
        kwargs["base_url"] = resolved.llm_base_url
    return ChatOpenAI(**kwargs)
