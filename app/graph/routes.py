"""Conditional routing for the stage-three graph."""

from typing import Literal

from langgraph.types import Send

from app.graph.state import Stage3State, Stage6State


def route_after_validation(
    state: Stage3State,
) -> Literal["supervisor", "reviewer"]:
    """Skip execution and review failure state when validation failed."""

    return "reviewer" if state.get("errors") else "supervisor"


def route_supervisor(
    state: Stage3State,
) -> list[Send] | Literal["reviewer"]:
    """Fan ready tasks out to the shared worker node using Send."""

    ready_task_ids = state.get("ready_task_ids", [])
    if not ready_task_ids:
        return "reviewer"

    tasks_by_id = {task.task_id: task for task in state["tasks"]}
    specs_by_task_id = {spec.task_id: spec for spec in state["agent_specs"]}
    results_by_task_id = {
        result.task_id: result for result in state.get("worker_results", [])
    }
    sends: list[Send] = []
    for task_id in ready_task_ids:
        task = tasks_by_id[task_id]
        dependency_results = [
            results_by_task_id[dependency]
            for dependency in task.dependencies
            if dependency in results_by_task_id
        ]
        sends.append(
            Send(
                "worker",
                {
                    "run_id": state["run_id"],
                    "task": task,
                    "spec": specs_by_task_id[task_id],
                    "dependency_results": dependency_results,
                },
            )
        )
    return sends


def route_runtime_supervisor(
    state: Stage6State,
) -> list[Send] | Literal["reviewer"]:
    """Send resolved, isolated task inputs to the real Worker Factory node."""

    ready_task_ids = state.get("ready_task_ids", [])
    if not ready_task_ids:
        return "reviewer"

    tasks_by_id = {task.task_id: task for task in state["tasks"]}
    specs_by_task_id = {
        spec.task_id: spec for spec in state["resolved_agent_specs"]
    }
    latest_results = {
        result.task_id: result for result in state.get("worker_results", [])
    }
    sends: list[Send] = []
    for task_id in ready_task_ids:
        task = tasks_by_id[task_id]
        spec = specs_by_task_id[task_id]
        knowledge_scope = {item.casefold() for item in spec.knowledge_scope}
        relevant_memory = [
            item
            for item in state.get("memory_context", [])
            if not item.scope
            or knowledge_scope.intersection(
                scope.casefold() for scope in item.scope
            )
        ]
        sends.append(
            Send(
                "worker",
                {
                    "run_id": state["run_id"],
                    "task": task,
                    "spec": spec,
                    "dependency_results": [
                        latest_results[dependency]
                        for dependency in task.dependencies
                    ],
                    "relevant_memory": relevant_memory,
                    "review_feedback": state.get("review_feedback", {}).get(
                        task_id,
                        [],
                    ),
                },
            )
        )
    return sends


def route_after_runtime_review(
    state: Stage6State,
) -> Literal["apply_review", "end"]:
    """Continue only retry/replan decisions; terminal decisions end the run."""

    decision = state.get("reviewer_decision")
    if decision is not None and decision.action in {"retry_tasks", "replan"}:
        return "apply_review"
    return "end"


def route_after_review_application(
    state: Stage6State,
) -> Literal["supervisor", "planner", "end"]:
    """Route the guarded review mutation to scheduling or replanning."""

    decision = state.get("reviewer_decision")
    if decision is None:
        return "end"
    if decision.action == "retry_tasks":
        return "supervisor"
    if decision.action == "replan":
        return "planner"
    return "end"
