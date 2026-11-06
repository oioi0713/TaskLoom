import json

from app.contracts.output_models import ReportResult
from app.contracts.result import WorkerResult
from app.contracts.review import ReviewerDecision
from app.contracts.task import TaskSpec
from app.graph.runtime_cli import main


class FakeGraph:
    def invoke(self, state, config=None):
        task = TaskSpec(
            task_id="report",
            title="Report",
            description="Write report",
            task_type="report",
            assigned_agent_id="writer",
            output_contract="report_result_v1",
            status="completed",
        )
        output = ReportResult(
            title="Completed report",
            executive_summary="The fake runtime completed.",
            sections=[{"heading": "Result", "content": "Success"}],
        ).model_dump(mode="json")
        return {
            **state,
            "workflow_status": "completed",
            "plan_version": 1,
            "tasks": [task],
            "worker_results": [
                WorkerResult(
                    run_id=state["run_id"],
                    task_id="report",
                    agent_id="writer",
                    status="completed",
                    output_contract="report_result_v1",
                    output=output,
                    execution_time_ms=5,
                    token_usage=10,
                )
            ],
            "reviewer_decision": ReviewerDecision(
                action="finish",
                score=1.0,
                problems=[],
                suggestions=[],
            ),
            "total_steps": 2,
            "retry_count": 0,
            "errors": [],
            "events": [],
            "final_output": json.dumps(output),
        }


def test_runtime_cli_prints_every_worker_result(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "app.graph.runtime_cli.build_runtime_graph",
        lambda **kwargs: FakeGraph(),
    )
    log_dir = tmp_path / "run-log"

    exit_code = main(
        [
            "--goal",
            "Create a report",
            "--log-dir",
            str(log_dir),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["workflow_status"] == "completed"
    assert payload["worker_results"][0]["task_id"] == "report"
    assert payload["reviewer_decision"]["action"] == "finish"
    saved_payload = json.loads(
        (log_dir / "result.json").read_text(encoding="utf-8")
    )
    assert saved_payload == payload
    run_metadata = json.loads((log_dir / "run.json").read_text(encoding="utf-8"))
    assert run_metadata["status"] == "completed"
    assert (log_dir / "llm_calls.jsonl").read_text(encoding="utf-8") == ""
    assert "开始阶段 7 Reviewer 闭环工作流" in captured.err
    assert "最终 JSON 已保存到" in captured.err


def test_runtime_cli_can_save_without_printing_large_json(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "app.graph.runtime_cli.build_runtime_graph",
        lambda **kwargs: FakeGraph(),
    )
    log_dir = tmp_path / "quiet-log"

    exit_code = main(
        [
            "--goal",
            "Create a report",
            "--log-dir",
            str(log_dir),
            "--no-print-json",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == ""
    assert json.loads((log_dir / "result.json").read_text(encoding="utf-8"))[
        "workflow_status"
    ] == "completed"
