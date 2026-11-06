from app.agents.reviewer import build_runtime_review_update
from app.agents.supervisor import runtime_supervisor_node
from app.contracts.review import ReviewerDecision
from app.graph.nodes import apply_review_decision_node
from app.graph.routes import (
    route_after_review_application,
    route_after_runtime_review,
)
from tests.unit.test_reviewer_agent import completed_task, runtime_state


def test_targeted_retry_invalidates_target_and_descendants() -> None:
    research = completed_task()
    report = completed_task().model_copy(
        update={
            "task_id": "report",
            "title": "Report",
            "description": "Write report",
            "task_type": "report",
            "assigned_agent_id": "researcher",
            "dependencies": ["research"],
        }
    )
    state = runtime_state()
    state["tasks"] = [research, report]
    state.update(
        build_runtime_review_update(
            state,
            ReviewerDecision(
                action="retry_tasks",
                score=0.4,
                problems=["Research lacks detail"],
                suggestions=["Add official evidence"],
                target_task_ids=["research"],
            ),
        )
    )

    update = apply_review_decision_node(state)

    assert [(task.task_id, task.status) for task in update["tasks"]] == [
        ("research", "pending"),
        ("report", "pending"),
    ]
    assert update["tasks"][0].retry_count == 1
    assert update["tasks"][1].retry_count == 0
    assert "Add official evidence" in update["review_feedback"]["research"]
    assert "upstream task" in update["review_feedback"]["report"][0]
    assert route_after_runtime_review(state) == "apply_review"
    state.update(update)
    assert route_after_review_application(state) == "supervisor"


def test_replan_records_constraints_and_routes_to_planner() -> None:
    state = runtime_state()
    state.update(
        build_runtime_review_update(
            state,
            ReviewerDecision(
                action="replan",
                score=0.3,
                problems=["Plan omitted comparison"],
                replan_constraints=["Add a comparison task"],
            ),
        )
    )

    update = apply_review_decision_node(state)

    assert update["replan_count"] == 1
    assert update["replan_constraints"] == ["Add a comparison task"]
    state.update(update)
    assert route_after_review_application(state) == "planner"


def test_terminal_decisions_route_to_end() -> None:
    state = runtime_state()
    state["reviewer_decision"] = ReviewerDecision(action="fail", score=0.0)

    assert route_after_runtime_review(state) == "end"


def test_old_completed_results_do_not_unlock_invalidated_descendant() -> None:
    research = completed_task().model_copy(update={"status": "pending"})
    report = completed_task().model_copy(
        update={
            "task_id": "report",
            "title": "Report",
            "description": "Write report",
            "task_type": "report",
            "assigned_agent_id": "researcher",
            "dependencies": ["research"],
            "status": "pending",
        }
    )
    state = runtime_state()
    state["tasks"] = [research, report]

    update = runtime_supervisor_node(state)

    assert update["ready_task_ids"] == ["research"]
