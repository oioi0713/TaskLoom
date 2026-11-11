"""Allow-list of capabilities that a planner may assign to workers."""

from types import MappingProxyType
from typing import Mapping


CAPABILITY_REGISTRY: frozenset[str] = frozenset(
    {
        "direct_answer",
        "web_research",
        "document_analysis",
        "technical_analysis",
        "data_analysis",
        "comparison",
        "report_writing",
        "experiment_design",
    }
)

CAPABILITY_TOOL_MAP: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "direct_answer": frozenset(),
        "web_research": frozenset({"web_search", "web_page_reader"}),
        "document_analysis": frozenset({"knowledge_search", "document_reader"}),
        "technical_analysis": frozenset({"python_sandbox"}),
        "data_analysis": frozenset({"python_sandbox", "dataset_reader"}),
        "comparison": frozenset(),
        "report_writing": frozenset(),
        "experiment_design": frozenset({"python_sandbox", "dataset_reader"}),
    }
)


def has_capability(name: str) -> bool:
    """Return whether a capability is registered."""

    return name in CAPABILITY_REGISTRY


def allowed_tools_for(capabilities: list[str]) -> frozenset[str]:
    """Return the union of tools permitted by the supplied capabilities."""

    tools: set[str] = set()
    for capability in capabilities:
        tools.update(CAPABILITY_TOOL_MAP.get(capability, ()))
    return frozenset(tools)
