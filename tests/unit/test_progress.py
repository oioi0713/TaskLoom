import time

import pytest

from app.progress import run_with_heartbeat


def test_run_with_heartbeat_reports_while_operation_is_blocked() -> None:
    messages: list[str] = []

    result = run_with_heartbeat(
        lambda: (time.sleep(0.035), "done")[1],
        progress=messages.append,
        label="test request",
        interval_seconds=0.01,
    )

    assert result == "done"
    assert any("test request 仍在处理中" in message for message in messages)


def test_run_with_heartbeat_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        run_with_heartbeat(
            lambda: None,
            progress=lambda message: None,
            label="test",
            interval_seconds=0,
        )
