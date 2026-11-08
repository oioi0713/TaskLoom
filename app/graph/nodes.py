"""Non-agent graph nodes for validation and result aggregation."""

from langgraph.types import Overwrite

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
        result.task_id: result
        for result in state.get("active_worker_results", [])
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
        result.task_id: result
        for result in state.get("active_worker_results", [])
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


def apply_review_decision_node(state: Stage6State) -> dict:
    """Apply a guarded retry or replan decision without losing audit history."""

    decision = state.get("reviewer_decision")
    if decision is None:
        return {
            "workflow_status": "failed",
            "errors": ["Review application requires a ReviewerDecision."],
        }

    if decision.action == "retry_tasks":
        targets = set(decision.target_task_ids)
        descendants = set(targets)
        changed = True
        while changed:
            changed = False
            for task in state["tasks"]:
                if task.task_id not in descendants and descendants.intersection(
                    task.dependencies
                ):
                    descendants.add(task.task_id)
                    changed = True

        updated_tasks = []
        review_feedback = dict(state.get("review_feedback", {}))
        feedback = [*decision.problems, *decision.suggestions]
        for task in state["tasks"]:
            if task.task_id in targets:
                updated_tasks.append(
                    task.model_copy(
                        update={
                            "status": "pending",
                            "retry_count": task.retry_count + 1,
                        }
                    )
                )
                review_feedback[task.task_id] = feedback or [
                    "Reviewer requested a higher-quality replacement result."
                ]
            elif task.task_id in descendants:
                updated_tasks.append(task.model_copy(update={"status": "pending"}))
                review_feedback[task.task_id] = [
                    "An upstream task was retried. Regenerate this result using "
                    "the latest dependency outputs."
                ]
            else:
                updated_tasks.append(task)

        return {
            "tasks": updated_tasks,
            "ready_task_ids": [],
            "resolved_agent_specs": [],
            "review_feedback": review_feedback,
            "retry_count": state.get("retry_count", 0) + len(targets),
            "workflow_status": "running",
            "final_output": None,
            "events": [
                WorkflowEvent(
                    event_type="review.retry_applied",
                    message="Reviewer invalidated targeted tasks and descendants.",
                    data={
                        "target_task_ids": sorted(targets),
                        "invalidated_task_ids": sorted(descendants),
                    },
                )
            ],
        }

    if decision.action == "replan":
        return {
            # Everything derived from the rejected plan leaves the active
            # state. Historical audit data remains in logs/checkpoint history
            # and will later move to the audit database.
            "execution_plan": None,
            "tasks": [],
            "agent_specs": [],
            "active_worker_results": Overwrite(value=[]),
            "ready_task_ids": [],
            "resolved_agent_specs": [],
            "review_feedback": {},
            "replan_constraints": decision.replan_constraints,
            "replan_count": state.get("replan_count", 0) + 1,
            "workflow_status": "running",
            "final_output": None,
            "errors": Overwrite(value=[]),
            "events": [
                WorkflowEvent(
                    event_type="review.replan_applied",
                    message="Reviewer sent constraints back to Planner.",
                    data={"constraints": decision.replan_constraints},
                )
            ],
        }

    return {}
