import pytest

from app.graph.builder import run_mock_workflow


@pytest.fixture(scope="module")
def workflow_run():
    return run_mock_workflow(worker_delay_seconds=0.05)


def test_mock_workflow_completes_all_five_tasks(workflow_run) -> None:
    state, _ = workflow_run

    assert state["workflow_status"] == "completed"
    assert [(task.task_id, task.status) for task in state["tasks"]] == [
        ("T1", "completed"),
        ("T2", "completed"),
        ("T3", "completed"),
        ("T4", "completed"),
        ("T5", "completed"),
    ]
    assert {result.task_id for result in state["active_worker_results"]} == {
        "T1",
        "T2",
        "T3",
        "T4",
        "T5",
    }
    assert len(state["active_worker_results"]) == 5


def test_first_three_tasks_execute_in_parallel(workflow_run) -> None:
    _, tracker = workflow_run

    research_ids = {"T1", "T2", "T3"}
    latest_start = max(tracker.started_at(task_id) for task_id in research_ids)
    earliest_finish = min(tracker.finished_at(task_id) for task_id in research_ids)

    assert tracker.peak_active_workers == 3
    assert latest_start < earliest_finish


def test_dependent_tasks_wait_for_prerequisites(workflow_run) -> None:
    _, tracker = workflow_run

    research_finished = max(
        tracker.finished_at(task_id) for task_id in {"T1", "T2", "T3"}
    )

    assert tracker.started_at("T4") >= research_finished
    assert tracker.started_at("T5") >= tracker.finished_at("T4")


def test_reducer_aggregates_results_once_per_task(workflow_run) -> None:
    state, _ = workflow_run

    result_ids = [result.task_id for result in state["active_worker_results"]]

    assert len(result_ids) == len(set(result_ids)) == 5
    comparison = next(
        result
        for result in state["active_worker_results"]
        if result.task_id == "T4"
    )
    assert len(comparison.output["options"]) == 3


def test_reviewer_finishes_with_report_output(workflow_run) -> None:
    state, _ = workflow_run

    assert state["reviewer_decision"].action == "finish"
    assert state["reviewer_decision"].score == 1.0
    assert "Agent Memory" in state["final_output"]
    assert not state["errors"]


def test_mock_workflow_uses_no_llm_or_tools(workflow_run) -> None:
    state, _ = workflow_run

    assert all(
        result.token_usage == 0 for result in state["active_worker_results"]
    )
    assert all(
        result.tool_calls == [] for result in state["active_worker_results"]
    )
    assert all(result.sources == [] for result in state["active_worker_results"])
