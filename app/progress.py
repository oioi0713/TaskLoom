"""Small progress helpers for long blocking provider calls."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.runtime_control import (
    RunCancelled,
    RunControl,
    RunPaused,
    RunSuperseded,
)


T = TypeVar("T")


def run_with_heartbeat(
    operation: Callable[[], T],
    *,
    progress: Callable[[str], None] | None,
    label: str,
    interval_seconds: float = 10.0,
) -> T:
    """Run a blocking operation and report periodically until it returns."""

    if progress is None:
        return operation()
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    stopped = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        while not stopped.wait(interval_seconds):
            elapsed = int(time.monotonic() - started)
            progress(f"{label} 仍在处理中，已等待约 {elapsed} 秒")

    thread = threading.Thread(
        target=heartbeat,
        name=f"taskloom-{label}-heartbeat",
        daemon=True,
    )
    thread.start()
    try:
        return operation()
    finally:
        stopped.set()
        thread.join(timeout=min(interval_seconds, 0.1))


def invoke_model_cancellable(
    model: Any,
    messages: list[Any],
    *,
    execution_control: RunControl | None,
    operation_name: str,
) -> Any:
    """Invoke a model, using cancellable ``ainvoke`` when Web control is active."""

    if execution_control is None or not hasattr(model, "ainvoke"):
        if execution_control is None:
            return model.invoke(messages)
        with execution_control.operation("llm", operation_name):
            return model.invoke(messages)

    loop = asyncio.new_event_loop()
    task: asyncio.Task[Any] | None = None

    def abort() -> None:
        if task is not None and not task.done() and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)

    try:
        asyncio.set_event_loop(loop)
        async_call: Awaitable[Any] = model.ainvoke(messages)
        task = loop.create_task(async_call)
        try:
            with execution_control.operation(
                "llm",
                operation_name,
                abort=abort,
            ):
                return loop.run_until_complete(task)
        except asyncio.CancelledError as exc:
            snapshot = execution_control.snapshot()
            if snapshot.stop_reason == "cancelled":
                raise RunCancelled("Run was cancelled during an LLM request.") from exc
            if snapshot.stop_reason == "superseded":
                raise RunSuperseded(
                    "Run was superseded during an LLM request."
                ) from exc
            if snapshot.pause_requested:
                raise RunPaused("LLM request was cancelled for pause.") from exc
            raise
    finally:
        asyncio.set_event_loop(None)
        if task is not None and not task.done():
            task.cancel()
            loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        loop.close()
