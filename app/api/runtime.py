"""Process-local Web sessions plus cooperative background run control."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from app.api.schemas import RunEventView, RunView, SessionView
from app.config import Settings, get_settings
from app.graph.builder import resume_runtime_workflow, run_runtime_workflow
from app.runtime_control import RunCancelled, RunControl, RunPaused, RunSuperseded


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    title: str
    created_at: datetime = field(default_factory=_now)
    latest_run_id: str | None = None


@dataclass(slots=True)
class RunRecord:
    run_id: str
    thread_id: str
    session_id: str
    query: str
    control: RunControl
    status: str = "queued"
    phase: str = "queued"
    pending_action: str | None = None
    active_tools: list[str] = field(default_factory=list)
    requires_checkpoint_resume: bool = False
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tasks: list[dict[str, Any]] = field(default_factory=list)
    worker_results: list[dict[str, Any]] = field(default_factory=list)
    memory_count: int = 0
    final_output: str | None = None
    errors: list[str] = field(default_factory=list)
    events: list[RunEventView] = field(default_factory=list)


WorkflowExecutor = Callable[..., Any]
_ACTIVE_STATUSES = {
    "queued",
    "running",
    "pause_requested",
    "pausing_after_tools",
    "paused",
    "resuming",
    "cancelling",
}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "superseded"}


class WebRunManager:
    """Own front-end sessions and execute each query in a background thread."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        executor: WorkflowExecutor = run_runtime_workflow,
        resume_executor: WorkflowExecutor = resume_runtime_workflow,
    ) -> None:
        self.settings = settings or get_settings()
        self.executor = executor
        self.resume_executor = resume_executor
        self._sessions: dict[str, SessionRecord] = {}
        self._runs: dict[str, RunRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._lock = RLock()

    def create_session(self, title: str) -> SessionView:
        session_id = str(uuid4())
        record = SessionRecord(session_id=session_id, title=title.strip() or "新会话")
        with self._lock:
            self._sessions[session_id] = record
        return self._session_view(record)

    def list_sessions(self) -> list[SessionView]:
        with self._lock:
            records = sorted(
                self._sessions.values(), key=lambda item: item.created_at, reverse=True
            )
            return [self._session_view(item) for item in records]

    def get_session(self, session_id: str) -> SessionView | None:
        with self._lock:
            record = self._sessions.get(session_id)
            return self._session_view(record) if record is not None else None

    def get_run(self, run_id: str) -> RunView | None:
        with self._lock:
            record = self._runs.get(run_id)
            return self._run_view(record) if record is not None else None

    def latest_run(self, session_id: str) -> RunView | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.latest_run_id is None:
                return None
            return self._run_view(self._runs[session.latest_run_id])

    def start_query(self, session_id: str, query: str) -> RunView:
        loop = asyncio.get_running_loop()
        superseded_run_id: str | None = None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            if session.latest_run_id is not None:
                previous = self._runs[session.latest_run_id]
                if previous.status in _ACTIVE_STATUSES:
                    previous.status = "superseded"
                    previous.phase = "superseded"
                    previous.pending_action = None
                    previous.finished_at = _now()
                    previous.control.supersede()
                    superseded_run_id = previous.run_id

            run_id = str(uuid4())
            thread_id = str(uuid4())

            def observe(event: str, data: dict[str, Any]) -> None:
                loop.call_soon_threadsafe(
                    self._handle_control_event, run_id, event, data
                )

            record = RunRecord(
                run_id=run_id,
                thread_id=thread_id,
                session_id=session_id,
                query=query.strip(),
                control=RunControl(observe),
            )
            self._runs[run_id] = record
            session.latest_run_id = run_id
            if session.title == "新会话" or session.title.startswith("会话 "):
                session.title = (
                    query.strip()
                    if len(query.strip()) <= 28
                    else query.strip()[:28] + "…"
                )
            self._conditions[run_id] = asyncio.Condition()

        if superseded_run_id is not None:
            self._append_event(
                superseded_run_id,
                "run.superseded",
                "同一会话提交了新 Query，旧运行已停止继续写回。",
                {"replacement_run_id": run_id},
            )
        self._append_event(run_id, "run.created", "运行已创建，等待执行。")
        self._tasks[run_id] = asyncio.create_task(self._execute(record))
        return self._run_view(record)

    def pause_run(self, run_id: str) -> RunView:
        with self._lock:
            record = self._require_run(run_id)
            if record.status not in {"queued", "running", "resuming"}:
                raise ValueError(f"Run status={record.status} cannot be paused.")
            snapshot = record.control.request_pause()
            record.active_tools = list(snapshot.active_tools)
            if snapshot.paused:
                record.status = "paused"
                record.phase = "paused"
                record.pending_action = "resume_current_step"
                event_type = "run.paused"
                message = "运行已在安全边界暂停。"
            elif snapshot.active_tools:
                record.status = "pausing_after_tools"
                record.phase = "tool_running"
                record.pending_action = "pause_after_tools"
                event_type = "run.pausing_after_tools"
                message = "暂停请求已收到，将在当前 Tool 完成后暂停。"
            else:
                record.status = "pause_requested"
                record.phase = "llm_running"
                record.pending_action = "pause_after_llm"
                event_type = "run.pause_requested"
                message = "暂停请求已收到，正在取消当前 LLM 请求。"
        self._append_event(run_id, event_type, message)
        return self.get_run(run_id)  # type: ignore[return-value]

    def resume_run(self, run_id: str) -> RunView:
        with self._lock:
            record = self._require_run(run_id)
            if record.status not in {
                "paused",
                "pause_requested",
                "pausing_after_tools",
            }:
                raise ValueError(f"Run status={record.status} cannot be resumed.")
            record.status = "resuming"
            record.phase = "resuming"
            record.pending_action = None
            record.control.resume()
            restart_from_checkpoint = record.requires_checkpoint_resume
            record.requires_checkpoint_resume = False
            record.status = "resuming" if restart_from_checkpoint else "running"
        if restart_from_checkpoint:
            self._tasks[run_id] = asyncio.create_task(
                self._execute(record, resume_from_checkpoint=True)
            )
        self._append_event(run_id, "run.resumed", "运行已恢复。")
        return self.get_run(run_id)  # type: ignore[return-value]

    def cancel_run(self, run_id: str) -> RunView:
        with self._lock:
            record = self._require_run(run_id)
            if record.status not in _ACTIVE_STATUSES:
                raise ValueError(f"Run status={record.status} cannot be cancelled.")
            record.status = "cancelling"
            record.phase = "cancelling"
            record.pending_action = "stop_at_safe_point"
            record.control.cancel()
        self._append_event(
            run_id,
            "run.cancel_requested",
            "取消请求已收到；LLM 将被取消，已启动的 Tool 完成后停止运行。",
        )
        return self.get_run(run_id)  # type: ignore[return-value]

    def events_after(self, run_id: str, sequence: int) -> list[RunEventView]:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(run_id)
            return [event for event in record.events if event.sequence > sequence]

    async def wait_for_events(
        self, run_id: str, sequence: int, *, timeout: float = 15.0
    ) -> list[RunEventView]:
        current = self.events_after(run_id, sequence)
        if current:
            return current
        condition = self._conditions[run_id]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except TimeoutError:
            return []
        return self.events_after(run_id, sequence)

    async def _execute(
        self,
        record: RunRecord,
        *,
        resume_from_checkpoint: bool = False,
    ) -> None:
        event_loop = asyncio.get_running_loop()
        self._mark_running(record.run_id)

        def progress(message: str) -> None:
            event_loop.call_soon_threadsafe(
                self._append_progress_event, record.run_id, message
            )

        try:
            if resume_from_checkpoint:
                state = await asyncio.to_thread(
                    self.resume_executor,
                    thread_id=record.thread_id,
                    session_id=record.session_id,
                    settings=self.settings,
                    progress=progress,
                    execution_control=record.control,
                )
            else:
                state = await asyncio.to_thread(
                    self.executor,
                    record.query,
                    run_id=record.run_id,
                    session_id=record.session_id,
                    thread_id=record.thread_id,
                    settings=self.settings,
                    progress=progress,
                    execution_control=record.control,
                )
        except RunPaused:
            self._mark_llm_paused(record.run_id)
            return
        except RunCancelled:
            self._mark_cancelled(record.run_id)
            return
        except RunSuperseded:
            self._mark_superseded(record.run_id)
            return
        except Exception as exc:
            stop_reason = record.control.snapshot().stop_reason
            if stop_reason == "cancelled":
                self._mark_cancelled(record.run_id)
                return
            if stop_reason == "superseded":
                self._mark_superseded(record.run_id)
                return
            self._mark_failed(record.run_id, f"{type(exc).__name__}: {exc}")
            return
        self._mark_completed(record.run_id, state)

    def _mark_running(self, run_id: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            if record.status in _TERMINAL_STATUSES:
                return
            if record.status != "paused":
                record.status = "running"
                record.phase = "planner"
            record.started_at = record.started_at or _now()
        self._append_event(run_id, "run.started", "工作流开始执行。")

    def _append_progress_event(self, run_id: str, message: str) -> None:
        phase = self._phase_from_message(message)
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record.status not in {"queued", "running", "resuming"}:
                return
            record.phase = phase
        self._append_event(run_id, f"progress.{phase}", message)

    def _handle_control_event(
        self, run_id: str, event: str, data: dict[str, Any]
    ) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            record.active_tools = list(data.get("active_tools", record.active_tools))
            if event == "operation_started" and record.status == "running":
                record.phase = (
                    "tool_running" if data.get("kind") == "tool" else "llm_running"
                )
                return
            if event != "paused" or record.status in _TERMINAL_STATUSES:
                return
            if record.status == "paused":
                return
            record.status = "paused"
            record.phase = "paused"
            record.pending_action = "resume_current_step"
        self._append_event(
            run_id,
            "run.paused",
            "当前调用已结束，运行已在安全边界暂停。",
            data,
        )

    def _mark_completed(self, run_id: str, state: Any) -> None:
        with self._lock:
            record = self._runs[run_id]
            if record.status in {"cancelled", "superseded", "cancelling"}:
                return
            if state.get("run_id") != run_id:
                record.status = "failed"
                record.errors = [
                    "Web Run ID 与工作流 State run_id 不一致："
                    f"{run_id} != {state.get('run_id')}"
                ]
            else:
                record.status = (
                    "completed"
                    if state.get("workflow_status") == "completed"
                    else "failed"
                )
                record.tasks = [item.model_dump(mode="json") for item in state["tasks"]]
                record.worker_results = [
                    item.model_dump(mode="json")
                    for item in state["active_worker_results"]
                ]
                record.memory_count = len(state.get("memory_context", []))
                record.final_output = state.get("final_output")
                record.errors = list(state.get("errors", []))
            record.phase = record.status
            record.finished_at = _now()
            record.pending_action = None
            record.active_tools = []
            status = record.status
        event_type = "run.completed" if status == "completed" else "run.failed"
        message = "工作流执行完成。" if status == "completed" else "工作流执行失败。"
        self._append_event(run_id, event_type, message)

    def _mark_failed(self, run_id: str, error: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            if record.status in {"cancelled", "superseded"}:
                return
            record.status = "failed"
            record.phase = "failed"
            record.finished_at = _now()
            record.pending_action = None
            record.active_tools = []
            record.errors = [error]
        self._append_event(run_id, "run.failed", error)

    def _mark_cancelled(self, run_id: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            if record.status == "superseded":
                return
            record.status = "cancelled"
            record.phase = "cancelled"
            record.finished_at = _now()
            record.pending_action = None
            record.active_tools = []
        self._append_event(run_id, "run.cancelled", "运行已取消。")

    def _mark_llm_paused(self, run_id: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            if record.status in _TERMINAL_STATUSES:
                return
            record.status = "paused"
            record.phase = "paused"
            record.pending_action = "retry_cancelled_llm"
            record.requires_checkpoint_resume = True
            record.active_tools = []
        self._append_event(
            run_id,
            "run.paused",
            "LLM 请求已取消；恢复后将从 Checkpoint 重新发起该请求。",
        )

    def _mark_superseded(self, run_id: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            record.status = "superseded"
            record.phase = "superseded"
            record.finished_at = record.finished_at or _now()
            record.pending_action = None
            record.active_tools = []

    def _append_event(
        self,
        run_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            record = self._runs[run_id]
            event = RunEventView(
                sequence=len(record.events) + 1,
                event_type=event_type,
                message=message,
                created_at=_now(),
                data=data or {},
            )
            record.events.append(event)
        condition = self._conditions.get(run_id)
        if condition is not None:

            async def notify() -> None:
                async with condition:
                    condition.notify_all()

            asyncio.create_task(notify())

    def _require_run(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise KeyError(run_id) from exc

    @staticmethod
    def _phase_from_message(message: str) -> str:
        if "Planner" in message:
            return "planner"
        if "Supervisor" in message:
            return "supervisor"
        if "Worker" in message or "工具" in message or "Agent Loop" in message:
            return "worker"
        if "Aggregate" in message:
            return "aggregate"
        if "Reviewer" in message:
            return "reviewer"
        if "Memory" in message:
            return "memory"
        return "running"

    @staticmethod
    def _session_view(record: SessionRecord) -> SessionView:
        return SessionView.model_validate(record)

    @staticmethod
    def _run_view(record: RunRecord) -> RunView:
        return RunView(
            run_id=record.run_id,
            thread_id=record.thread_id,
            session_id=record.session_id,
            query=record.query,
            status=record.status,  # type: ignore[arg-type]
            phase=record.phase,
            pending_action=record.pending_action,
            active_tools=list(record.active_tools),
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            tasks=list(record.tasks),
            worker_results=list(record.worker_results),
            memory_count=record.memory_count,
            final_output=record.final_output,
            errors=list(record.errors),
            event_count=len(record.events),
        )
