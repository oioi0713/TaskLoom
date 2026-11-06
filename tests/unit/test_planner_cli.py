from app.agents.planner_cli import main
from app.config import get_settings
from langchain_core.exceptions import LangChainException


def test_planner_cli_reports_missing_configuration(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    monkeypatch.setenv("TASKLOOM_LLM_MODEL", "")
    monkeypatch.setenv("TASKLOOM_LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        exit_code = main(
            ["--goal", "调研 Agent Memory", "--log-dir", str(tmp_path)]
        )
    finally:
        get_settings.cache_clear()

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "TASKLOOM_LLM_MODEL" in captured.err
    assert "TASKLOOM_LLM_API_KEY" in captured.err


def test_planner_cli_reports_provider_error_without_traceback(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    class FailingPlanner:
        def plan(self, planner_input):
            raise LangChainException("provider unavailable")

    monkeypatch.setattr(
        "app.agents.planner_cli.create_planner_agent",
        lambda *args, **kwargs: FailingPlanner(),
    )

    exit_code = main(
        ["--goal", "调研 Agent Memory", "--log-dir", str(tmp_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 3
    assert "LLM request failed" in captured.err
    assert "Traceback" not in captured.err
