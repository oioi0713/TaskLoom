"""Non-agent graph nodes for validation and result aggregation."""

from app.graph.state import Stage3State, Stage6State, WorkflowEvent
from app.planning.validator import PlanGuard


def validate_plan_node(state: Stage3State | Stage6State) -> dict:
    """Run any proposed execution plan through the stage-two Plan Guard."""

    plan = state.get("execution_plan")
    if plan is None:
        return {
            "errors": ["Planner did not provide an execution plan."],
            "workflow_status": "failed",
        }

    outcome = PlanGuard().evaluate(plan)
    if not outcome.executable or outcome.plan is None:
        return {
            "errors": outcome.feedback or ["Plan Guard rejected the plan."],
            "workflow_status": "failed",
            "events": [
                WorkflowEvent(
                    event_type="plan.rejected",
                    message="Plan Guard rejected the plan.",
                    data={"issue_codes": [issue.code for issue in outcome.issues]},
                )
            ],
        }

    validated_plan = outcome.plan
    return {
        "clarified_goal": validated_plan.goal_summary,
        "tasks": validated_plan.tasks,
        "agent_specs": validated_plan.agents,
        "budget": validated_plan.budget,
        "ready_task_ids": [],
        "events": [
            WorkflowEvent(
                event_type="plan.validated",
                message="Plan Guard accepted the plan.",
                data={"task_count": len(validated_plan.tasks)},
            )
        ],
    }


def aggregate_node(state: Stage3State) -> dict:
    """Apply all reducer-aggregated worker results to task status."""

    results_by_task = {
        result.task_id: result for result in state.get("worker_results", [])
    }
    updated_tasks = []
    for task in state["tasks"]:
        result = results_by_task.get(task.task_id)
        if result is None:
            updated_tasks.append(task)
        elif result.status == "completed":
            updated_tasks.append(task.model_copy(update={"status": "completed"}))
        else:
            updated_tasks.append(task.model_copy(update={"status": "failed"}))

    completed_now = sorted(
        task.task_id
        for task in updated_tasks
        if task.status == "completed"
        and next(
            original.status
            for original in state["tasks"]
            if original.task_id == task.task_id
        )
        != "completed"
    )
    return {
        "tasks": updated_tasks,
        "ready_task_ids": [],
        "total_steps": state.get("total_steps", 0) + 1,
        "events": [
            WorkflowEvent(
                event_type="result.aggregated",
                message="Aggregator applied worker results.",
                data={"completed_task_ids": completed_now},
            )
        ],
    }


def aggregate_runtime_node(state: Stage6State) -> dict:
    """Apply latest real Worker results and enforce bounded task retries."""

    latest_results = {
        result.task_id: result for result in state.get("worker_results", [])
    }
    retryable_statuses = {"failed", "timeout", "invalid_output"}
    retried_ids: list[str] = []
    completed_ids: list[str] = []
    failed_ids: list[str] = []
    updated_tasks = []
    for task in state["tasks"]:
        result = latest_results.get(task.task_id)
        if result is None or task.status != "running":
            updated_tasks.append(task)
            continue
        if result.status == "completed":
            updated_tasks.append(task.model_copy(update={"status": "completed"}))
            completed_ids.append(task.task_id)
            continue

        retry_limit = min(
            task.max_retries,
            state["budget"].max_retries_per_task,
        )
        if result.status in retryable_statuses and task.retry_count < retry_limit:
            updated_tasks.append(
                task.model_copy(
                    update={
                        "status": "pending",
                        "retry_count": task.retry_count + 1,
                    }
                )
            )
            retried_ids.append(task.task_id)
        else:
            updated_tasks.append(task.model_copy(update={"status": "failed"}))
            failed_ids.append(task.task_id)

    events = [
        WorkflowEvent(
            event_type="result.aggregated",
            message="Aggregator applied real Worker results.",
            data={
                "completed_task_ids": sorted(completed_ids),
                "failed_task_ids": sorted(failed_ids),
            },
        )
    ]
    if retried_ids:
        events.append(
            WorkflowEvent(
                event_type="task.retry_scheduled",
                message="Retryable Worker failures returned to pending.",
                data={"task_ids": sorted(retried_ids)},
            )
        )
    return {
        "tasks": updated_tasks,
        "ready_task_ids": [],
        "resolved_agent_specs": [],
        "retry_count": state.get("retry_count", 0) + len(retried_ids),
        "total_steps": state.get("total_steps", 0) + 1,
        "events": events,
    }
