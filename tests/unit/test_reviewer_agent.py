from __future__ import annotations

from typing import Any

import pytest

from app.agents.reviewer import (
    ReviewerAgent,
    build_reviewer_messages,
    build_runtime_review_update,
    enforce_reviewer_decision,
)
from app.contracts.budget import ExecutionBudget
from app.contracts.output_models import ResearchResult
from app.contracts.plan import ExecutionPlan
from app.contracts.result import WorkerResult
from app.contracts.review import ReviewerDecision, ReviewerInput
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.builder import create_runtime_initial_state


class SequentialReviewerModel:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(list(messages))
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


def completed_task(*, retry_count: int = 0) -> TaskSpec:
    return TaskSpec(
        task_id="research",
        title="Research",
        description="Research LangGraph",
        task_type="research",
        assigned_agent_id="researcher",
        output_contract="research_result_v1",
        status="completed",
        retry_count=retry_count,
    )


def completed_result() -> WorkerResult:
    output = ResearchResult(
        summary="LangGraph uses stateful graphs.",
        findings=["StateGraph connects nodes and edges."],
        sources=[],
    ).model_dump(mode="json")
    return WorkerResult(
        run_id="run-review",
        task_id="research",
        agent_id="researcher",
        status="completed",
        output_contract="research_result_v1",
        output=output,
        execution_time_ms=1,
        token_usage=10,
    )


def reviewer_input() -> ReviewerInput:
    return ReviewerInput(
        original_goal="Research LangGraph",
        plan_version=1,
        expected_deliverable="A research result",
        tasks=[completed_task()],
        latest_results=[completed_result()],
        retry_count=0,
        replan_count=0,
        total_steps=2,
        max_retries_per_task=2,
        max_replans=2,
        max_total_steps=30,
    )


def runtime_state(*, task: TaskSpec | None = None) -> dict:
    selected_task = task or completed_task()
    plan = ExecutionPlan(
        goal_summary="Research LangGraph",
        tasks=[selected_task],
        agents=[
            SubAgentSpec(
                agent_id="researcher",
                task_id="research",
                role="Researcher",
                objective="Research LangGraph",
                instructions=["Return evidence"],
                required_capabilities=["technical_analysis"],
                output_contract="research_result_v1",
            )
        ],
        expected_deliverable="A research result",
        budget=ExecutionBudget(),
    )
    return {
        **create_runtime_initial_state("Research LangGraph"),
        "plan_version": 1,
        "execution_plan": plan,
        "tasks": [selected_task],
        "agent_specs": plan.agents,
        "worker_results": [completed_result()],
        "workflow_status": "running",
    }


def test_reviewer_prompt_contains_only_bounded_evidence() -> None:
    messages = build_reviewer_messages(reviewer_input())
    combined = "\n".join(str(message.content) for message in messages)

    assert "Research LangGraph" in combined
    assert "StateGraph connects nodes" in combined
    assert "retry_tasks" in combined
    assert "API Key" not in combined


def test_reviewer_retries_invalid_structure_once() -> None:
    model = SequentialReviewerModel(
        [
            {"action": "retry_tasks", "score": 0.4},
            ReviewerDecision(
                action="finish",
                score=0.9,
                problems=[],
                suggestions=[],
            ),
        ]
    )

    decision = ReviewerAgent(model).review(reviewer_input())

    assert decision.action == "finish"
    assert len(model.calls) == 2
    assert "requires target_task_ids" in str(model.calls[1][-1].content)


def test_finish_is_rejected_when_a_task_is_incomplete() -> None:
    state = runtime_state(
        task=completed_task().model_copy(update={"status": "failed"})
    )
    decision = enforce_reviewer_decision(
        state,
        ReviewerDecision(action="finish", score=1.0),
    )

    assert decision.action == "fail"
    assert "incomplete tasks" in " ".join(decision.problems)


def test_retry_is_rejected_for_unknown_or_exhausted_tasks() -> None:
    state = runtime_state(task=completed_task(retry_count=2))
    decision = enforce_reviewer_decision(
        state,
        ReviewerDecision(
            action="retry_tasks",
            score=0.2,
            target_task_ids=["research", "unknown"],
        ),
    )

    assert decision.action == "fail"
    problems = " ".join(decision.problems)
    assert "unknown tasks" in problems
    assert "exhausted retries" in problems


def test_replan_is_rejected_when_budget_is_exhausted() -> None:
    state = runtime_state()
    state["replan_count"] = state["budget"].max_replans

    decision = enforce_reviewer_decision(
        state,
        ReviewerDecision(
            action="replan",
            score=0.2,
            replan_constraints=["Add another task"],
        ),
    )

    assert decision.action == "fail"
    assert "replan budget" in " ".join(decision.problems)


def test_finish_builds_terminal_output() -> None:
    update = build_runtime_review_update(
        runtime_state(),
        ReviewerDecision(action="finish", score=0.95),
    )

    assert update["workflow_status"] == "completed"
    assert '"research"' in update["final_output"]
    assert update["reviewer_decision"].action == "finish"


@pytest.mark.parametrize("action", ["retry_tasks", "replan"])
def test_non_terminal_review_keeps_workflow_open(action: str) -> None:
    decision = (
        ReviewerDecision(
            action="retry_tasks",
            score=0.5,
            target_task_ids=["research"],
        )
        if action == "retry_tasks"
        else ReviewerDecision(
            action="replan",
            score=0.5,
            replan_constraints=["Add a missing comparison task"],
        )
    )

    update = build_runtime_review_update(runtime_state(), decision)

    assert update["reviewer_decision"].action == action
    assert update["workflow_status"] == "running"
    assert update["final_output"] is None
