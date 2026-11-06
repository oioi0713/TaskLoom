"""Deterministic completion Reviewers used before the stage-seven LLM review."""

import json

from app.contracts.output_models import ReportResult
from app.contracts.review import ReviewerDecision
from app.graph.state import Stage3State, Stage6State, WorkflowEvent


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


def runtime_reviewer_node(state: Stage6State) -> dict:
    """Finish only when every dynamic task has a completed WorkerResult."""

    latest_results = {
        result.task_id: result for result in state.get("worker_results", [])
    }
    incomplete = [task for task in state["tasks"] if task.status != "completed"]
    if not incomplete and not state.get("errors"):
        depended_on = {
            dependency
            for task in state["tasks"]
            for dependency in task.dependencies
        }
        terminal_ids = [
            task.task_id
            for task in state["tasks"]
            if task.task_id not in depended_on
        ]
        final_payload = {
            task_id: latest_results[task_id].output for task_id in terminal_ids
        }
        decision = ReviewerDecision(
            action="finish",
            score=1.0,
            problems=[],
            suggestions=[],
        )
        return {
            "reviewer_decision": decision,
            "workflow_status": "completed",
            "final_output": json.dumps(
                final_payload,
                ensure_ascii=False,
                indent=2,
            ),
            "events": [
                WorkflowEvent(
                    event_type="review.completed",
                    message="Rule-based Reviewer accepted all dynamic task results.",
                    data={"terminal_task_ids": terminal_ids},
                )
            ],
        }

    problems = [
        f"Task {task.task_id} ended with status {task.status}."
        for task in incomplete
    ]
    problems.extend(state.get("errors", []))
    decision = ReviewerDecision(
        action="fail",
        score=0.0,
        problems=problems or ["The workflow did not complete."],
        suggestions=[],
    )
    return {
        "reviewer_decision": decision,
        "workflow_status": "failed",
        "final_output": None,
        "events": [
            WorkflowEvent(
                event_type="review.completed",
                message="Rule-based Reviewer rejected the dynamic workflow.",
                data={
                    "incomplete_task_ids": [task.task_id for task in incomplete]
                },
            )
        ],
    }
