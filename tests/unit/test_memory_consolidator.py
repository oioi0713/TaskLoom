from __future__ import annotations

from app.contracts.output_models import ResearchResult
from app.contracts.result import WorkerResult
from app.contracts.review import ReviewerDecision
from app.graph.builder import create_runtime_initial_state
from app.memory.consolidator import consolidate_state_memory


def test_consolidator_keeps_summary_not_raw_tool_history() -> None:
    state = create_runtime_initial_state("研究 LangGraph")
    state["plan_version"] = 1
    state["active_worker_results"] = [
        WorkerResult(
            run_id=state["run_id"],
            task_id="research",
            agent_id="researcher",
            status="completed",
            output_contract="research_result_v1",
            output=ResearchResult(
                summary="StateGraph 管理状态图。",
                findings=["节点通过边连接。"],
                sources=[],
            ).model_dump(mode="json"),
            execution_time_ms=10,
            token_usage=20,
            tool_calls=[],
        )
    ]

    memory = consolidate_state_memory(
        state,
        ReviewerDecision(action="finish", score=1.0),
    )

    assert len(memory) == 2
    result_memory = next(item for item in memory if item.source_task_id == "research")
    assert "StateGraph" in result_memory.content


def test_replan_constraints_and_failures_become_memory() -> None:
    state = create_runtime_initial_state("研究 LangGraph")
    state["plan_version"] = 1
    state["active_worker_results"] = [
        WorkerResult(
            run_id=state["run_id"],
            task_id="research",
            agent_id="researcher",
            status="failed",
            output_contract="research_result_v1",
            error="不要重复搜索同一关键词",
            execution_time_ms=10,
            token_usage=20,
        )
    ]
    decision = ReviewerDecision(
        action="replan",
        score=0.2,
        problems=["证据不足"],
        suggestions=["优先读取本地文件"],
        replan_constraints=["禁止重复搜索"],
    )

    memory = consolidate_state_memory(state, decision)

    assert {item.kind for item in memory} >= {"failure", "constraint", "decision"}
    assert any("禁止重复搜索" in item.content for item in memory)
