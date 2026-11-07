from app.contracts.result import WorkerResult
from app.workers.worker_cli import main


class FakeWorker:
    released = True

    def execute(self) -> WorkerResult:
        return WorkerResult(
            run_id="stage5-worker-demo",
            task_id="worker_demo",
            agent_id="worker_demo_agent",
            status="completed",
            output_contract="report_result_v1",
            output={
                "title": "测试报告",
                "executive_summary": "Worker CLI successfully returned a report.",
                "sections": [
                    {"heading": "结果", "content": "CLI smoke test passed."}
                ],
            },
            execution_time_ms=1,
            token_usage=0,
        )


class FakeResearchWorker:
    released = True

    def execute(self, *, progress=None) -> WorkerResult:
        if progress is not None:
            progress("Agent Loop test")
        return WorkerResult(
            run_id="stage8-worker-demo",
            task_id="worker_demo",
            agent_id="worker_demo_agent",
            status="completed",
            output_contract="research_result_v1",
            output={"summary": "done", "findings": ["tested"], "sources": []},
            execution_time_ms=1,
            token_usage=10,
        )


def test_worker_cli_prints_result_and_release_state(
    monkeypatch, capsys, tmp_path
) -> None:
    captured = {}

    def fake_create_worker(**kwargs):
        captured.update(kwargs)
        return FakeWorker()

    monkeypatch.setattr("app.workers.worker_cli.create_worker", fake_create_worker)

    exit_code = main(
        ["--task", "生成一份测试报告", "--log-dir", str(tmp_path)]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"status": "completed"' in output
    assert "CONTEXT_RELEASED=True" in output
    assert captured["task"].description == "生成一份测试报告"
    assert captured["spec"].granted_tools == []


def test_worker_cli_web_research_mode_grants_only_read_tools(
    monkeypatch, capsys, tmp_path
) -> None:
    captured = {}

    def fake_create_worker(**kwargs):
        captured.update(kwargs)
        return FakeResearchWorker()

    monkeypatch.setattr("app.workers.worker_cli.create_worker", fake_create_worker)

    exit_code = main(
        [
            "--task",
            "调研 LangGraph",
            "--mode",
            "web-research",
            "--log-dir",
            str(tmp_path),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert '"output_contract": "research_result_v1"' in output.out
    assert "Agent Loop test" in output.err
    assert captured["spec"].granted_tools == ["web_search", "web_page_reader"]
