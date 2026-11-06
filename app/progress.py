"""Small progress helpers for long blocking provider calls."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar


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
