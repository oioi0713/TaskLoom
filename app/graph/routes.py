"""Conditional routing for the stage-three graph."""

from typing import Literal

from langgraph.types import Send

from app.graph.state import Stage3State


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
