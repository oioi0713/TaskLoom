from __future__ import annotations

from typing import Any

from app.contracts.budget import ExecutionBudget
from app.contracts.output_models import ReportResult, ResearchResult
from app.contracts.plan import ExecutionPlan
from app.contracts.review import ReviewerDecision
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.builder import (
    build_runtime_graph,
    create_runtime_initial_state,
)
from app.workers.factory import WorkerFactory


class FixedPlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan_value = plan
        self.inputs = []

    def plan(self, planner_input):
        self.inputs.append(planner_input)
        return self.plan_value


class FixedReviewer:
    def review(self, review_input):
        return ReviewerDecision(
            action="finish",
            score=1.0,
            problems=[],
            suggestions=[],
        )


class SequentialReviewer:
    def __init__(self, decisions: list[ReviewerDecision]) -> None:
        self.decisions = list(decisions)
        self.inputs = []

    def review(self, review_input):
        self.inputs.append(review_input)
        return self.decisions.pop(0)


class ContractStructuredModel:
    def __init__(self, output_model: type) -> None:
        self.output_model = output_model

    def invoke(self, messages: list[Any], **kwargs: Any):
        if self.output_model is ResearchResult:
            return ResearchResult(
                summary="LangGraph is a stateful graph orchestration framework.",
                findings=["StateGraph connects typed state, nodes, and edges."],
                sources=[],
            )
        if self.output_model is ReportResult:
            return ReportResult(
                title="LangGraph learning report",
                executive_summary="Learn state, nodes, edges, and routing first.",
                sections=[
                    {
                        "heading": "Learning order",
                        "content": "Start with StateGraph, then conditional routing.",
                    }
                ],
            )
        raise AssertionError(f"Unexpected output model: {self.output_model}")


class ContractAwareChatModel:
    def with_structured_output(
        self,
        model: type,
        *,
        method: str,
        include_raw: bool = False,
        **kwargs,
    ):
        assert method == "function_calling"
        return ContractStructuredModel(model)


def dynamic_plan() -> ExecutionPlan:
    return ExecutionPlan(
        goal_summary="Research LangGraph and produce a report",
        assumptions=[],
        tasks=[
            TaskSpec(
                task_id="research",
                title="Research LangGraph",
                description="Collect core LangGraph concepts",
                task_type="research",
                priority=10,
                assigned_agent_id="researcher",
                output_contract="research_result_v1",
            ),
            TaskSpec(
                task_id="report",
                title="Write report",
                description="Write a report from research",
                task_type="report",
                dependencies=["research"],
                priority=5,
                assigned_agent_id="writer",
                output_contract="report_result_v1",
            ),
        ],
        agents=[
            SubAgentSpec(
                agent_id="researcher",
                task_id="research",
                role="Researcher",
                objective="Research LangGraph",
                instructions=["Return structured findings"],
                required_capabilities=["web_research"],
                requested_tools=["web_search"],
                knowledge_scope=["LangGraph"],
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
                knowledge_scope=["LangGraph"],
                output_contract="report_result_v1",
            ),
        ],
        expected_deliverable="A structured LangGraph report",
        budget=ExecutionBudget(max_concurrency=2),
    )


def test_real_runtime_graph_connects_plan_to_dynamic_workers() -> None:
    planner = FixedPlanner(dynamic_plan())
    progress_messages: list[str] = []
    graph = build_runtime_graph(
        planner=planner,
        reviewer=FixedReviewer(),
        worker_factory=WorkerFactory(ContractAwareChatModel()),
        progress=progress_messages.append,
    )
    initial_state = create_runtime_initial_state(
        "Research LangGraph and produce a report",
        enabled_tools=[],
    )

    state = graph.invoke(initial_state)

    assert state["workflow_status"] == "completed"
    assert state["plan_version"] == 1
    assert [(task.task_id, task.status) for task in state["tasks"]] == [
        ("research", "completed"),
        ("report", "completed"),
    ]
    assert [result.task_id for result in state["worker_results"]] == [
        "research",
        "report",
    ]
    assert state["worker_results"][1].output["title"] == "LangGraph learning report"
    assert state["reviewer_decision"].action == "finish"
    assert state["errors"] == []
    assert planner.inputs[0].user_goal == initial_state["original_goal"]
    event_types = [event.event_type for event in state["events"]]
    assert "plan.created" in event_types
    assert "plan.validated" in event_types
    assert event_types.count("worker.created") == 2
    assert event_types.count("worker.completed") == 2
    assert any("Planner 完成" in message for message in progress_messages)
    assert any("Supervisor 选中任务" in message for message in progress_messages)
    assert any("Worker research" in message for message in progress_messages)
    assert any("Reviewer 结束：finish" in message for message in progress_messages)


def test_reviewer_targeted_retry_reruns_task_and_descendants() -> None:
    reviewer = SequentialReviewer(
        [
            ReviewerDecision(
                action="retry_tasks",
                score=0.5,
                problems=["Research needs stronger detail"],
                suggestions=["Expand the core concepts"],
                target_task_ids=["research"],
            ),
            ReviewerDecision(action="finish", score=0.95),
        ]
    )
    graph = build_runtime_graph(
        planner=FixedPlanner(dynamic_plan()),
        reviewer=reviewer,
        worker_factory=WorkerFactory(ContractAwareChatModel()),
    )

    state = graph.invoke(
        create_runtime_initial_state("Research LangGraph and produce a report")
    )

    assert state["workflow_status"] == "completed"
    assert [result.task_id for result in state["worker_results"]] == [
        "research",
        "report",
        "research",
        "report",
    ]
    assert state["tasks"][0].retry_count == 1
    assert state["retry_count"] == 1
    assert len(reviewer.inputs) == 2
    event_types = [event.event_type for event in state["events"]]
    assert event_types.count("review.completed") == 2
    assert "task.retry_scheduled" not in event_types


def test_reviewer_replan_returns_constraints_to_planner() -> None:
    planner = FixedPlanner(dynamic_plan())
    reviewer = SequentialReviewer(
        [
            ReviewerDecision(
                action="replan",
                score=0.4,
                problems=["The plan needs an explicit comparison"],
                replan_constraints=["Add an explicit comparison before reporting"],
            ),
            ReviewerDecision(action="finish", score=0.95),
        ]
    )
    graph = build_runtime_graph(
        planner=planner,
        reviewer=reviewer,
        worker_factory=WorkerFactory(ContractAwareChatModel()),
    )

    state = graph.invoke(
        create_runtime_initial_state("Research LangGraph and produce a report")
    )

    assert state["workflow_status"] == "completed"
    assert state["plan_version"] == 2
    assert state["replan_count"] == 1
    assert len(planner.inputs) == 2
    assert planner.inputs[1].replan_constraints == [
        "Add an explicit comparison before reporting"
    ]
    assert [result.task_id for result in state["worker_results"]] == [
        "research",
        "report",
        "research",
        "report",
    ]
