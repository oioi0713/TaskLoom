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
            description=(
                "Search public web indexes when the target URL is unknown. Use it to "
                "discover a small number of candidate pages, then switch to an "
                "authorized page-reading tool. Do not repeat equivalent queries or use it when an "
                "exact URL is already available."
            ),
            risk="read",
        ),
        "web_page_reader": ToolDefinition(
            name="web_page_reader",
            description=(
                "Read one exact public http/https URL and return page content plus "
                "source metadata. Use it only after a URL is known. A successful read "
                "is sufficient unless the content is empty, redirect-only, or the "
                "task explicitly needs another distinct source."
            ),
            risk="read",
        ),
        "knowledge_search": ToolDefinition(
            name="knowledge_search",
            description=(
                "Search workspace text/code by keywords and return matching file "
                "paths with excerpts, not complete documents. Use only when the path "
                "is unknown or repository-wide symbol/call-site discovery is required. "
                "When an exact path is known, use an authorized exact-file reader instead. After a "
                "useful path is found, stop searching and read that file."
            ),
            risk="read",
        ),
        "document_reader": ToolDefinition(
            name="document_reader",
            description=(
                "Read one exact workspace-relative text file path. Prefer this when "
                "the task already names a file. A repeated call with the same path and "
                "max_chars returns the same content and must not be used to verify or "
                "continue a successful read. If information is absent, report it as "
                "not specified instead of searching for invented fields."
            ),
            risk="read",
        ),
        "python_sandbox": ToolDefinition(
            name="python_sandbox",
            description=(
                "Run bounded Python analysis only when computation is required; do not "
                "use it for ordinary reading, summarization, or comparison."
            ),
            risk="compute",
        ),
        "dataset_reader": ToolDefinition(
            name="dataset_reader",
            description=(
                "Read an approved dataset when the task explicitly requires tabular "
                "or structured data; do not use it for source-code or document tasks."
            ),
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
