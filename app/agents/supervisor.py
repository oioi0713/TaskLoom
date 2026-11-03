"""Rule-based Supervisor used by the stage-three workflow."""

from app.graph.state import Stage3State, WorkflowEvent


def supervisor_node(state: Stage3State) -> dict:
    """Select pending tasks whose dependencies have completed."""

    tasks = state["tasks"]
    completed_ids = {
        result.task_id
        for result in state.get("worker_results", [])
        if result.status == "completed"
    }
    candidates = [
        task
        for task in tasks
        if task.status == "pending"
        and all(dependency in completed_ids for dependency in task.dependencies)
    ]
    candidates.sort(key=lambda task: (-task.priority, task.task_id))
    selected = candidates[: state["budget"].max_concurrency]
    selected_ids = {task.task_id for task in selected}

    if selected:
        updated_tasks = [
            task.model_copy(update={"status": "running"})
            if task.task_id in selected_ids
            else task
            for task in tasks
        ]
        return {
            "tasks": updated_tasks,
            "ready_task_ids": [task.task_id for task in selected],
            "total_steps": state.get("total_steps", 0) + 1,
            "events": [
                WorkflowEvent(
                    event_type="task.ready",
                    message="Supervisor selected ready tasks.",
                    data={"task_ids": [task.task_id for task in selected]},
                )
            ],
        }

    unfinished = [
        task.task_id
        for task in tasks
        if task.status not in {"completed", "failed", "skipped"}
    ]
    if unfinished:
        return {
            "ready_task_ids": [],
            "errors": [
                "Supervisor found no runnable task while unfinished tasks remain: "
                + ", ".join(unfinished)
            ],
            "events": [
                WorkflowEvent(
                    event_type="run.deadlock",
                    message="No runnable task remained.",
                    data={"task_ids": unfinished},
                )
            ],
        }
    return {"ready_task_ids": []}
