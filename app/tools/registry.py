"""Metadata-only registry of tools known to TaskLoom."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Static tool metadata used during permission resolution."""

    name: str
    description: str
    risk: Literal["read", "compute"]


TOOL_REGISTRY: Mapping[str, ToolDefinition] = MappingProxyType(
    {
        "web_search": ToolDefinition(
            name="web_search",
            description="Search approved web sources.",
            risk="read",
        ),
        "web_page_reader": ToolDefinition(
            name="web_page_reader",
            description="Read a selected web page.",
            risk="read",
        ),
        "knowledge_search": ToolDefinition(
            name="knowledge_search",
            description="Search the configured knowledge base.",
            risk="read",
        ),
        "document_reader": ToolDefinition(
            name="document_reader",
            description="Read an approved local or uploaded document.",
            risk="read",
        ),
        "python_sandbox": ToolDefinition(
            name="python_sandbox",
            description="Run bounded Python analysis in an isolated sandbox.",
            risk="compute",
        ),
        "dataset_reader": ToolDefinition(
            name="dataset_reader",
            description="Read an approved dataset.",
            risk="read",
        ),
    }
)


def has_tool(name: str) -> bool:
    """Return whether a tool is registered."""

    return name in TOOL_REGISTRY


def get_tool(name: str) -> ToolDefinition:
    """Return registered metadata or raise a clear error."""

    try:
        return TOOL_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unknown tool: {name}") from exc
