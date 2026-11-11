"""Thread-safe cooperative control for one Web workflow run."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal


OperationKind = Literal["llm", "tool"]
ControlObserver = Callable[[str, dict[str, Any]], None]


class RunControlError(RuntimeError):
    """Base exception raised when a controlled run must stop."""


class RunCancelled(RunControlError):
    """Raised at a safe point after the user cancels a run."""


class RunSuperseded(RunControlError):
    """Raised when a newer query replaces this session's run."""


class RunPaused(RunControlError):
    """Raised after an in-flight cancellable LLM request is interrupted."""


@dataclass(frozen=True, slots=True)
class RunControlSnapshot:
    pause_requested: bool
    paused: bool
    stop_reason: Literal["cancelled", "superseded"] | None
    active_llm_calls: int
    active_tools: tuple[str, ...]


class RunControl:
    """Coordinate pause, resume and stop requests across worker threads.

    Async LLM requests register abort callbacks and are cancelled on pause or stop.
    Tool calls are allowed to finish, then the run pauses at the following safe point.
    Synchronous model fallbacks also stop only after the current call returns.
    """

    def __init__(self, observer: ControlObserver | None = None) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._observer = observer
        self._pause_requested = False
        self._paused = False
        self._stop_reason: Literal["cancelled", "superseded"] | None = None
        self._active_llm_calls = 0
        self._active_tools: dict[str, int] = {}
        self._next_operation_id = 0
        self._llm_abort_callbacks: dict[int, Callable[[], None]] = {}

    def snapshot(self) -> RunControlSnapshot:
        with self._condition:
            return RunControlSnapshot(
                pause_requested=self._pause_requested,
                paused=self._paused,
                stop_reason=self._stop_reason,
                active_llm_calls=self._active_llm_calls,
                active_tools=tuple(sorted(self._active_tools)),
            )

    def request_pause(self) -> RunControlSnapshot:
        with self._condition:
            self._raise_if_stopped()
            self._pause_requested = True
            if not self._has_active_operations():
                self._paused = True
            snapshot = self.snapshot()
            abort_callbacks = list(self._llm_abort_callbacks.values())
            self._condition.notify_all()
        for abort in abort_callbacks:
            abort()
        if snapshot.paused:
            self._notify("paused", self._snapshot_data(snapshot))
        return snapshot

    def resume(self) -> RunControlSnapshot:
        with self._condition:
            self._raise_if_stopped()
            if not self._pause_requested and not self._paused:
                raise ValueError("Run is not paused or waiting to pause.")
            self._pause_requested = False
            was_paused = self._paused
            self._paused = False
            snapshot = self.snapshot()
            self._condition.notify_all()
        if was_paused:
            self._notify("resumed", self._snapshot_data(snapshot))
        return snapshot

    def cancel(self) -> None:
        self._stop("cancelled")

    def supersede(self) -> None:
        self._stop("superseded")

    def _stop(self, reason: Literal["cancelled", "superseded"]) -> None:
        with self._condition:
            if self._stop_reason is not None:
                return
            self._stop_reason = reason
            self._pause_requested = False
            self._paused = False
            abort_callbacks = list(self._llm_abort_callbacks.values())
            self._condition.notify_all()
        for abort in abort_callbacks:
            abort()
        self._notify(reason, {"reason": reason})

    def safe_point(self) -> None:
        """Block while paused or raise when the run has been stopped."""

        announced = False
        with self._condition:
            self._raise_if_stopped()
            while self._pause_requested:
                if not self._has_active_operations():
                    self._paused = True
                    if not announced:
                        announced = True
                        snapshot = self.snapshot()
                        self._notify("paused", self._snapshot_data(snapshot))
                self._condition.wait()
                self._raise_if_stopped()
            self._paused = False

    @contextmanager
    def operation(
        self,
        kind: OperationKind,
        name: str,
        *,
        pause_after: bool = True,
        abort: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        """Register an operation and pause at its boundary when requested."""

        self.safe_point()
        with self._condition:
            self._raise_if_stopped()
            self._next_operation_id += 1
            operation_id = self._next_operation_id
            if kind == "llm":
                self._active_llm_calls += 1
                if abort is not None:
                    self._llm_abort_callbacks[operation_id] = abort
            else:
                self._active_tools[name] = self._active_tools.get(name, 0) + 1
            snapshot = self.snapshot()
        self._notify(
            "operation_started",
            {"kind": kind, "name": name, **self._snapshot_data(snapshot)},
        )
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            with self._condition:
                if kind == "llm":
                    self._active_llm_calls = max(0, self._active_llm_calls - 1)
                    self._llm_abort_callbacks.pop(operation_id, None)
                else:
                    remaining = self._active_tools.get(name, 0) - 1
                    if remaining > 0:
                        self._active_tools[name] = remaining
                    else:
                        self._active_tools.pop(name, None)
                snapshot = self.snapshot()
                self._condition.notify_all()
            self._notify(
                "operation_finished",
                {"kind": kind, "name": name, **self._snapshot_data(snapshot)},
            )
        if not failed and pause_after:
            self.safe_point()

    def _has_active_operations(self) -> bool:
        return self._active_llm_calls > 0 or bool(self._active_tools)

    def _raise_if_stopped(self) -> None:
        if self._stop_reason == "cancelled":
            raise RunCancelled("Run was cancelled by the user.")
        if self._stop_reason == "superseded":
            raise RunSuperseded("Run was replaced by a newer session query.")

    @staticmethod
    def _snapshot_data(snapshot: RunControlSnapshot) -> dict[str, Any]:
        return {
            "pause_requested": snapshot.pause_requested,
            "paused": snapshot.paused,
            "active_llm_calls": snapshot.active_llm_calls,
            "active_tools": list(snapshot.active_tools),
        }

    def _notify(self, event: str, data: dict[str, Any]) -> None:
        if self._observer is not None:
            self._observer(event, data)
