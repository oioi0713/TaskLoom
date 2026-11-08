from __future__ import annotations

from typing import Any

import pytest

from app.checkpoint.memory import (
    MemoryCheckpointSession,
    create_memory_checkpointer,
)
from app.contracts.budget import ExecutionBudget
from app.contracts.output_models import ReportResult, ResearchResult
from app.contracts.plan import ExecutionPlan
from app.contracts.review import ReviewerDecision
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.builder import build_runtime_graph, create_runtime_initial_state
from app.workers.factory import WorkerFactory


class TrackingPlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan_value = plan
        self.call_count = 0

    def plan(self, planner_input: Any) -> ExecutionPlan:
        self.call_count += 1
        return self.plan_value


class FinishReviewer:
    def review(self, review_input: Any) -> ReviewerDecision:
        return ReviewerDecision(action="finish", score=1.0)


class StructuredModel:
    def __init__(self, output_model: type) -> None:
        self.output_model = output_model

    def invoke(self, messages: list[Any], **kwargs: Any) -> Any:
        if self.output_model is ResearchResult:
            return ResearchResult(
                summary="Checkpoint research completed.",
                findings=["The first task should not be replayed."],
                sources=[],
            )
        if self.output_model is ReportResult:
            return ReportResult(
                title="Checkpoint report",
                executive_summary="The workflow resumed from saved state.",
                sections=[
                    {
                        "heading": "Result",
                        "content": "Completed nodes were not executed again.",
                    }
                ],
            )
        raise AssertionError(f"Unexpected output model: {self.output_model}")


class StructuredChatModel:
    def with_structured_output(
        self,
        model: type,
        *,
        method: str,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> StructuredModel:
        return StructuredModel(model)


def checkpoint_plan() -> ExecutionPlan:
    return ExecutionPlan(
        goal_summary="Verify checkpoint recovery",
        assumptions=[],
        tasks=[
            TaskSpec(
                task_id="research",
                title="Research checkpointing",
                description="Produce the dependency result",
                task_type="research",
                assigned_agent_id="researcher",
                output_contract="research_result_v1",
            ),
            TaskSpec(
                task_id="report",
                title="Write checkpoint report",
                description="Use the completed dependency",
                task_type="report",
                dependencies=["research"],
                assigned_agent_id="writer",
                output_contract="report_result_v1",
            ),
        ],
        agents=[
            SubAgentSpec(
                agent_id="researcher",
                task_id="research",
                role="Researcher",
                objective="Research checkpointing",
                instructions=["Return a structured result"],
                required_capabilities=["web_research"],
                requested_tools=[],
                knowledge_scope=["checkpointing"],
                output_contract="research_result_v1",
            ),
            SubAgentSpec(
                agent_id="writer",
                task_id="report",
                role="Writer",
                objective="Write the final report",
                instructions=["Use the dependency result"],
                required_capabilities=["report_writing"],
                requested_tools=[],
                knowledge_scope=["checkpointing"],
                output_contract="report_result_v1",
            ),
        ],
        expected_deliverable="A checkpoint report",
        budget=ExecutionBudget(max_concurrency=1),
    )


def test_memory_checkpoint_resumes_without_replaying_completed_task() -> None:
    planner = TrackingPlanner(checkpoint_plan())
    graph = build_runtime_graph(
        planner=planner,
        reviewer=FinishReviewer(),
        worker_factory=WorkerFactory(StructuredChatModel()),
        checkpointer=create_memory_checkpointer(),
        interrupt_after=["aggregate"],
    )
    initial_state = create_runtime_initial_state(
        "Verify checkpoint recovery",
        thread_id="checkpoint-test",
        enabled_tools=[],
    )
    session = MemoryCheckpointSession(graph, initial_state["thread_id"])

    first_pause = session.start(initial_state)

    assert session.is_paused is True
    assert session.snapshot().next == ("supervisor",)
    assert [(task.task_id, task.status) for task in first_pause["tasks"]] == [
        ("research", "completed"),
        ("report", "pending"),
    ]
    assert [result.task_id for result in first_pause["active_worker_results"]] == [
        "research"
    ]

    second_pause = session.resume()

    assert session.is_paused is True
    assert [result.task_id for result in second_pause["active_worker_results"]] == [
        "research",
        "report",
    ]

    final_state = session.resume()

    assert session.is_paused is False
    assert final_state["workflow_status"] == "completed"
    assert planner.call_count == 1
    assert [result.task_id for result in final_state["active_worker_results"]] == [
        "research",
        "report",
    ]
    assert len(session.history()) >= 3


def test_checkpoint_threads_are_unique_by_default() -> None:
    first = create_runtime_initial_state("first")
    second = create_runtime_initial_state("second")

    assert first["thread_id"] == first["run_id"]
    assert second["thread_id"] == second["run_id"]
    assert first["thread_id"] != second["thread_id"]


def test_checkpoint_session_rejects_mismatched_initial_state() -> None:
    graph = build_runtime_graph(
        planner=TrackingPlanner(checkpoint_plan()),
        reviewer=FinishReviewer(),
        worker_factory=WorkerFactory(StructuredChatModel()),
        checkpointer=create_memory_checkpointer(),
    )
    initial_state = create_runtime_initial_state(
        "Verify checkpoint recovery",
        thread_id="state-thread",
    )
    session = MemoryCheckpointSession(graph, "different-thread")

    with pytest.raises(ValueError, match="thread_id must match"):
        session.start(initial_state)
