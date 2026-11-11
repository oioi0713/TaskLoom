from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.runtime import WebRunManager
from app.config import Settings
from app.runtime_control import RunPaused


def test_web_app_serves_workspace_and_session_api() -> None:
    manager = WebRunManager(settings=Settings(_env_file=None))
    client = TestClient(create_app(manager))

    response = client.get("/")
    assert response.status_code == 200
    assert "TaskLoom · 多智能体工作台" in response.text

    health = client.get("/api/health")
    assert health.json() == {"status": "ok", "stage": "11-controls"}

    created = client.post("/api/sessions", json={"title": "前端测试"})
    assert created.status_code == 201
    session = created.json()
    assert session["title"] == "前端测试"

    sessions = client.get("/api/sessions").json()
    assert [item["session_id"] for item in sessions] == [session["session_id"]]


def test_control_endpoint_rejects_unknown_run() -> None:
    client = TestClient(create_app(WebRunManager(settings=Settings(_env_file=None))))

    response = client.post("/api/runs/not-running/pause")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_run_manager_executes_query_and_emits_terminal_event() -> None:
    def executor(goal: str, **kwargs: Any) -> dict[str, Any]:
        kwargs["progress"]("Planner 开始规划")
        kwargs["progress"]("Worker demo 执行结束")
        return {
            "run_id": kwargs["run_id"],
            "workflow_status": "completed",
            "tasks": [],
            "active_worker_results": [],
            "memory_context": [],
            "final_output": f"完成：{goal}",
            "errors": [],
        }

    manager = WebRunManager(
        settings=Settings(_env_file=None),
        executor=executor,
    )
    session = manager.create_session("测试")
    run = manager.start_query(session.session_id, "整理报告")

    for _ in range(50):
        current = manager.get_run(run.run_id)
        if current is not None and current.status == "completed":
            break
        await asyncio.sleep(0.01)

    current = manager.get_run(run.run_id)
    assert current is not None
    assert current.status == "completed"
    assert current.run_id == run.run_id
    assert current.thread_id != current.run_id
    assert current.final_output == "完成：整理报告"
    events = manager.events_after(run.run_id, 0)
    assert events[0].event_type == "run.created"
    assert events[-1].event_type == "run.completed"
    assert any(event.event_type == "progress.planner" for event in events)


@pytest.mark.asyncio
async def test_run_manager_pauses_after_active_call_then_resumes() -> None:
    operation_started = threading.Event()
    release_operation = threading.Event()

    def executor(goal: str, **kwargs: Any) -> dict[str, Any]:
        control = kwargs["execution_control"]
        with control.operation("llm", "test-planner"):
            operation_started.set()
            release_operation.wait(timeout=2)
        return {
            "run_id": kwargs["run_id"],
            "workflow_status": "completed",
            "tasks": [],
            "active_worker_results": [],
            "memory_context": [],
            "final_output": goal,
            "errors": [],
        }

    manager = WebRunManager(settings=Settings(_env_file=None), executor=executor)
    session = manager.create_session("暂停测试")
    run = manager.start_query(session.session_id, "等待后完成")
    assert await asyncio.to_thread(operation_started.wait, 1)

    pausing = manager.pause_run(run.run_id)
    assert pausing.status == "pause_requested"
    release_operation.set()
    for _ in range(100):
        current = manager.get_run(run.run_id)
        if current is not None and current.status == "paused":
            break
        await asyncio.sleep(0.01)

    paused = manager.get_run(run.run_id)
    assert paused is not None and paused.status == "paused"
    manager.resume_run(run.run_id)
    for _ in range(100):
        current = manager.get_run(run.run_id)
        if current is not None and current.status == "completed":
            break
        await asyncio.sleep(0.01)
    assert manager.get_run(run.run_id).status == "completed"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_new_query_supersedes_active_run_in_same_session() -> None:
    release = threading.Event()

    def executor(goal: str, **kwargs: Any) -> dict[str, Any]:
        with kwargs["execution_control"].operation("llm", goal):
            release.wait(timeout=2)
        return {
            "run_id": kwargs["run_id"],
            "workflow_status": "completed",
            "tasks": [],
            "active_worker_results": [],
            "memory_context": [],
            "final_output": goal,
            "errors": [],
        }

    manager = WebRunManager(settings=Settings(_env_file=None), executor=executor)
    session = manager.create_session("替换测试")
    first = manager.start_query(session.session_id, "first")
    await asyncio.sleep(0.02)
    second = manager.start_query(session.session_id, "second")

    assert manager.get_run(first.run_id).status == "superseded"  # type: ignore[union-attr]
    assert second.run_id != first.run_id
    assert second.thread_id != first.thread_id
    assert second.session_id == first.session_id
    release.set()


@pytest.mark.asyncio
async def test_cancelled_llm_pause_resumes_from_checkpoint_executor() -> None:
    started = threading.Event()
    resumed = threading.Event()
    captured_run_id: str | None = None

    def executor(goal: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal captured_run_id
        captured_run_id = kwargs["run_id"]
        interrupted = threading.Event()
        with kwargs["execution_control"].operation(
            "llm", "test", abort=interrupted.set
        ):
            started.set()
            interrupted.wait(timeout=1)
            raise RunPaused("test pause")

    def resume_executor(**kwargs: Any) -> dict[str, Any]:
        resumed.set()
        return {
            "run_id": captured_run_id,
            "workflow_status": "completed",
            "tasks": [],
            "active_worker_results": [],
            "memory_context": [],
            "final_output": "resumed",
            "errors": [],
        }

    manager = WebRunManager(
        settings=Settings(_env_file=None),
        executor=executor,
        resume_executor=resume_executor,
    )
    session = manager.create_session("LLM 恢复")
    run = manager.start_query(session.session_id, "pause me")
    assert await asyncio.to_thread(started.wait, 1)
    manager.pause_run(run.run_id)
    for _ in range(100):
        current = manager.get_run(run.run_id)
        if (
            current is not None
            and current.status == "paused"
            and current.pending_action == "retry_cancelled_llm"
        ):
            break
        await asyncio.sleep(0.01)

    paused = manager.get_run(run.run_id)
    assert paused is not None
    assert paused.pending_action == "retry_cancelled_llm"
    manager.resume_run(run.run_id)
    assert await asyncio.to_thread(resumed.wait, 1)
    for _ in range(100):
        current = manager.get_run(run.run_id)
        if current is not None and current.status == "completed":
            break
        await asyncio.sleep(0.01)
    assert manager.get_run(run.run_id).final_output == "resumed"  # type: ignore[union-attr]


def test_static_assets_exist_and_are_served() -> None:
    client = TestClient(create_app(WebRunManager(settings=Settings(_env_file=None))))

    css = client.get("/assets/app.css")
    javascript = client.get("/assets/app.js")

    assert css.status_code == 200
    assert "--paper" in css.text
    assert javascript.status_code == 200
    assert "connectEvents" in javascript.text
