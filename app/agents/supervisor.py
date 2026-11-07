"""Rule-first Supervisors for mock and real TaskLoom workflows."""

from app.contracts.subagent import ResolvedSubAgentSpec
from app.graph.state import Stage3State, Stage6State, WorkflowEvent
from app.policies.tool_policy import resolve_granted_tools


def supervisor_node(state: Stage3State) -> dict:
    """Select pending tasks whose dependencies have completed."""

    tasks = state["tasks"]
    completed_ids = {
        task.task_id for task in tasks if task.status == "completed"
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


def runtime_supervisor_node(state: Stage6State) -> dict:
    """Resolve permissions and select a bounded batch of executable tasks."""

    budget = state["budget"]
    current_steps = state.get("total_steps", 0)
    if current_steps >= budget.max_total_steps:
        message = (
            f"Workflow exhausted max_total_steps={budget.max_total_steps}."
        )
        return {
            "ready_task_ids": [],
            "resolved_agent_specs": [],
            "workflow_status": "failed",
            "errors": [message],
            "events": [
                WorkflowEvent(
                    event_type="run.budget_exhausted",
                    message=message,
                    data={"total_steps": current_steps},
                )
            ],
        }

    tasks = list(state["tasks"])
    task_status = {task.task_id: task.status for task in tasks}
    blocked_ids = {
        task.task_id
        for task in tasks
        if task.status == "pending"
        and any(
            task_status.get(dependency) in {"failed", "blocked", "skipped"}
            for dependency in task.dependencies
        )
    }
    if blocked_ids:
        tasks = [
            task.model_copy(update={"status": "blocked"})
            if task.task_id in blocked_ids
            else task
            for task in tasks
        ]

    completed_ids = {
        task.task_id for task in tasks if task.status == "completed"
    }
    candidates = [
        task
        for task in tasks
        if task.status == "pending"
        and all(dependency in completed_ids for dependency in task.dependencies)
    ]
    candidates.sort(key=lambda task: (-task.priority, task.task_id))

    specs_by_task = {spec.task_id: spec for spec in state["agent_specs"]}
    running_count = sum(task.status == "running" for task in tasks)
    available_slots = max(0, budget.max_concurrency - running_count)
    selected_candidates = candidates[:available_slots]
    selected = []
    resolved_specs: list[ResolvedSubAgentSpec] = []
    permission_events: list[WorkflowEvent] = []
    for task in selected_candidates:
        spec = specs_by_task[task.task_id]
        granted_tools = resolve_granted_tools(
            spec.required_capabilities,
            spec.requested_tools,
            state.get("user_allowed_tools", []),
            state.get("enabled_tools", []),
        )
        worker_token_budget = min(
            spec.token_budget,
            budget.max_worker_token_budget,
        )
        resolved = ResolvedSubAgentSpec.model_validate(
            {
                **spec.model_dump(mode="python"),
                "timeout_seconds": min(
                    spec.timeout_seconds,
                    budget.max_worker_timeout_seconds,
                ),
                "token_budget": worker_token_budget,
                "granted_tools": granted_tools,
            }
        )
        selected.append(task)
        resolved_specs.append(resolved)
        permission_events.append(
            WorkflowEvent(
                event_type="worker.permissions_resolved",
                message=f"Supervisor resolved permissions for {task.task_id}.",
                data={
                    "task_id": task.task_id,
                    "requested_tools": spec.requested_tools,
                    "granted_tools": granted_tools,
                },
            )
        )

    if selected:
        selected_ids = {task.task_id for task in selected}
        updated_tasks = [
            task.model_copy(update={"status": "running"})
            if task.task_id in selected_ids
            else task
            for task in tasks
        ]
        return {
            "tasks": updated_tasks,
            "ready_task_ids": [task.task_id for task in selected],
            "resolved_agent_specs": resolved_specs,
            "total_steps": current_steps + 1,
            "workflow_status": "running",
            "events": [
                WorkflowEvent(
                    event_type="task.ready",
                    message="Supervisor selected ready tasks.",
                    data={"task_ids": [task.task_id for task in selected]},
                ),
                *permission_events,
                *(
                    [
                        WorkflowEvent(
                            event_type="task.blocked",
                            message="Tasks were blocked by failed dependencies.",
                            data={"task_ids": sorted(blocked_ids)},
                        )
                    ]
                    if blocked_ids
                    else []
                ),
            ],
        }

    terminal = {"completed", "failed", "blocked", "skipped"}
    unfinished = [task.task_id for task in tasks if task.status not in terminal]
    if unfinished and running_count == 0:
        message = (
            "Supervisor found no runnable task while unfinished tasks remain: "
            + ", ".join(unfinished)
        )
        return {
            "tasks": tasks,
            "ready_task_ids": [],
            "resolved_agent_specs": [],
            "workflow_status": "failed",
            "errors": [message],
            "events": [
                WorkflowEvent(
                    event_type="run.deadlock",
                    message="No runnable task remained.",
                    data={"task_ids": unfinished},
                )
            ],
        }

    events = []
    if running_count:
        events.append(
            WorkflowEvent(
                event_type="scheduler.waiting",
                message="Supervisor did not duplicate-claim running tasks.",
                data={
                    "running_task_ids": [
                        task.task_id for task in tasks if task.status == "running"
                    ]
                },
            )
        )
    if blocked_ids:
        events.append(
            WorkflowEvent(
                event_type="task.blocked",
                message="Tasks were blocked by failed dependencies.",
                data={"task_ids": sorted(blocked_ids)},
            )
        )
    return {
        "tasks": tasks,
        "ready_task_ids": [],
        "resolved_agent_specs": [],
        "events": events,
    }
