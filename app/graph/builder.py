"""Build and run the no-LLM stage-three LangGraph workflow."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.agents.planner import mock_planner_node
from app.agents.reviewer import reviewer_node
from app.agents.supervisor import supervisor_node
from app.contracts.budget import ExecutionBudget
from app.graph.nodes import aggregate_node, validate_plan_node
from app.graph.routes import route_after_validation, route_supervisor
from app.graph.state import Stage3State
from app.workers.runtime import ExecutionTracker, MockWorkerRuntime


def build_mock_graph(
    *,
    runtime: MockWorkerRuntime | None = None,
) -> tuple[Any, ExecutionTracker]:
    """Compile the fixed stage-three graph and expose its execution tracker."""

    worker_runtime = runtime or MockWorkerRuntime()
    builder = StateGraph(Stage3State)
    builder.add_node("mock_planner", mock_planner_node)
    builder.add_node("validate_plan", validate_plan_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("worker", worker_runtime.execute)
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("reviewer", reviewer_node)

    builder.add_edge(START, "mock_planner")
    builder.add_edge("mock_planner", "validate_plan")
    builder.add_conditional_edges(
        "validate_plan",
        route_after_validation,
        {
            "supervisor": "supervisor",
            "reviewer": "reviewer",
        },
    )
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        ["worker", "reviewer"],
    )
    builder.add_edge("worker", "aggregate")
    builder.add_edge("aggregate", "supervisor")
    builder.add_edge("reviewer", END)
    return builder.compile(name="taskloom-stage-3-mock"), worker_runtime.tracker


def create_initial_state(goal: str) -> Stage3State:
    """Create a complete reducer-safe state for one mock workflow run."""

    return {
        "run_id": str(uuid4()),
        "thread_id": "stage-3-demo",
        "user_id": "local-user",
        "original_goal": goal,
        "clarified_goal": goal,
        "plan_version": 0,
        "tasks": [],
        "agent_specs": [],
        "worker_results": [],
        "ready_task_ids": [],
        "reviewer_decision": None,
        "retry_count": 0,
        "replan_count": 0,
        "total_steps": 0,
        "budget": ExecutionBudget(),
        "memory_context": [],
        "final_output": None,
        "errors": [],
        "events": [],
        "execution_plan": None,
        "workflow_status": "created",
    }


def run_mock_workflow(
    goal: str = "比较 Redis、向量数据库和文件式 Agent Memory 方案",
    *,
    worker_delay_seconds: float = 0.05,
) -> tuple[Stage3State, ExecutionTracker]:
    """Run the complete deterministic workflow and return state plus evidence."""

    runtime = MockWorkerRuntime(delay_seconds=worker_delay_seconds)
    graph, tracker = build_mock_graph(runtime=runtime)
    result = graph.invoke(create_initial_state(goal))
    return result, tracker
