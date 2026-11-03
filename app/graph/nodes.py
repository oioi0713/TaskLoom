"""Non-agent graph nodes for validation and result aggregation."""

from app.graph.state import Stage3State, WorkflowEvent
from app.planning.validator import PlanGuard


def validate_plan_node(state: Stage3State) -> dict:
    """Run the fixed mock plan through the real stage-two Plan Guard."""

    plan = state.get("execution_plan")
    if plan is None:
        return {
            "errors": ["Mock Planner did not provide an execution plan."],
            "workflow_status": "failed",
        }

    outcome = PlanGuard().evaluate(plan)
    if not outcome.executable or outcome.plan is None:
        return {
            "errors": outcome.feedback or ["Plan Guard rejected the mock plan."],
            "workflow_status": "failed",
            "events": [
                WorkflowEvent(
                    event_type="plan.rejected",
                    message="Plan Guard rejected the mock plan.",
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
                message="Plan Guard accepted the mock plan.",
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
