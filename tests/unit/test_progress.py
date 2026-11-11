import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.progress import invoke_model_cancellable, run_with_heartbeat
from app.runtime_control import RunControl, RunPaused


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


def test_cancellable_async_model_request_stops_on_pause() -> None:
    started = threading.Event()
    cancelled = threading.Event()

    class AsyncModel:
        async def ainvoke(self, messages):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    control = RunControl()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            invoke_model_cancellable,
            AsyncModel(),
            ["prompt"],
            execution_control=control,
            operation_name="test-llm",
        )
        assert started.wait(timeout=1)
        control.request_pause()
        with pytest.raises(RunPaused):
            future.result(timeout=1)

    assert cancelled.is_set()
