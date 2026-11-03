"""Deterministic validation and resolution of worker tool requests."""

from collections.abc import Iterable

from app.policies.capability_registry import allowed_tools_for
from app.tools.registry import TOOL_REGISTRY


HIGH_RISK_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "shell",
        "file_writer",
        "database_writer",
        "message_sender",
        "external_action",
    }
)


def validate_tool_request(capabilities: list[str], requested_tools: list[str]) -> None:
    """Reject unknown tools and tools outside the worker capability template."""

    unknown = sorted(set(requested_tools) - set(TOOL_REGISTRY))
    if unknown:
        raise ValueError(f"Unknown requested tools: {', '.join(unknown)}")

    capability_tools = allowed_tools_for(capabilities)
    disallowed = sorted(set(requested_tools) - set(capability_tools))
    if disallowed:
        raise ValueError(
            "Requested tools are not allowed by the selected capabilities: "
            + ", ".join(disallowed)
        )


def resolve_granted_tools(
    capabilities: list[str],
    requested_tools: list[str],
    user_allowed_tools: Iterable[str],
    enabled_tools: Iterable[str],
) -> list[str]:
    """Apply the four-way permission intersection defined by the architecture."""

    validate_tool_request(capabilities, requested_tools)
    capability_tools = allowed_tools_for(capabilities)
    granted = (
        set(requested_tools)
        & set(capability_tools)
        & set(user_allowed_tools)
        & set(enabled_tools)
        & set(TOOL_REGISTRY)
    )
    return [tool for tool in requested_tools if tool in granted]
