import pytest
from pydantic import ValidationError

from app.contracts.output_registry import OUTPUT_CONTRACTS, validate_output
from app.contracts.result import WorkerResult
from app.contracts.subagent import ResolvedSubAgentSpec
from app.policies.capability_registry import CAPABILITY_REGISTRY, allowed_tools_for
from app.policies.tool_policy import resolve_granted_tools
from app.tools.registry import TOOL_REGISTRY


def test_expected_registry_entries_exist() -> None:
    assert "web_research" in CAPABILITY_REGISTRY
    assert "web_search" in TOOL_REGISTRY
    assert "research_result_v1" in OUTPUT_CONTRACTS
    assert "exact workspace-relative" in TOOL_REGISTRY["document_reader"].description
    assert "path is unknown" in TOOL_REGISTRY["knowledge_search"].description


def test_capability_tool_mapping_is_bounded() -> None:
    assert allowed_tools_for(["web_research"]) == frozenset(
        {"web_search", "web_page_reader"}
    )


def test_tool_permission_resolution_is_an_intersection() -> None:
    granted = resolve_granted_tools(
        capabilities=["web_research"],
        requested_tools=["web_search", "web_page_reader"],
        user_allowed_tools=["web_search"],
        enabled_tools=["web_search", "web_page_reader"],
    )

    assert granted == ["web_search"]


def test_registered_output_contract_validates_payload() -> None:
    result = validate_output(
        "research_result_v1",
        {
            "summary": "Redis can provide shared, expiring memory storage.",
            "findings": ["TTL can bound temporary memory lifetime."],
            "sources": [],
        },
    )

    assert result.summary.startswith("Redis")


def test_unknown_output_contract_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown output contract"):
        validate_output("invented_contract", {})


def test_completed_worker_output_is_validated() -> None:
    with pytest.raises(ValidationError):
        WorkerResult(
            run_id="run-1",
            task_id="research_redis",
            agent_id="redis_researcher",
            status="completed",
            output_contract="research_result_v1",
            output={"summary": "Missing required findings"},
            execution_time_ms=10,
            token_usage=20,
        )


def test_resolved_worker_cannot_receive_unrequested_tool() -> None:
    with pytest.raises(ValidationError, match="were not requested"):
        ResolvedSubAgentSpec(
            agent_id="redis_researcher",
            task_id="research_redis",
            role="Redis researcher",
            objective="Research Redis memory",
            instructions=["Use official sources"],
            required_capabilities=["web_research"],
            requested_tools=["web_search"],
            knowledge_scope=["Redis"],
            output_contract="research_result_v1",
            granted_tools=["web_page_reader"],
        )
