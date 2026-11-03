"""Deterministic Reviewer for the stage-three mock workflow."""

from app.contracts.output_models import ReportResult
from app.contracts.review import ReviewerDecision
from app.graph.state import Stage3State, WorkflowEvent


def reviewer_node(state: Stage3State) -> dict:
    """Finish only when every fixed task has a valid completed result."""

    completed_ids = {
        result.task_id
        for result in state.get("worker_results", [])
        if result.status == "completed"
    }
    expected_ids = {task.task_id for task in state["tasks"]}
    all_tasks_completed = completed_ids == expected_ids and all(
        task.status == "completed" for task in state["tasks"]
    )

    if all_tasks_completed and not state.get("errors"):
        report_result = next(
            result for result in state["worker_results"] if result.task_id == "T5"
        )
        report = ReportResult.model_validate(report_result.output)
        decision = ReviewerDecision(
            action="finish",
            score=1.0,
            problems=[],
            suggestions=[],
        )
        return {
            "reviewer_decision": decision,
            "workflow_status": "completed",
            "final_output": f"{report.title}\n\n{report.executive_summary}",
            "events": [
                WorkflowEvent(
                    event_type="review.completed",
                    message="Reviewer accepted all five mock task results.",
                    data={"score": decision.score},
                )
            ],
        }

    missing = sorted(expected_ids - completed_ids)
    decision = ReviewerDecision(
        action="fail",
        score=0.0,
        problems=[
            "The mock workflow did not complete every required task."
            + (f" Missing: {', '.join(missing)}." if missing else "")
        ],
        suggestions=[],
    )
    return {
        "reviewer_decision": decision,
        "workflow_status": "failed",
        "final_output": None,
        "events": [
            WorkflowEvent(
                event_type="review.completed",
                message="Reviewer rejected the incomplete mock workflow.",
                data={"missing_task_ids": missing},
            )
        ],
    }
