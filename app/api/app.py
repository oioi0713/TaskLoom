"""FastAPI application serving the TaskLoom workspace and live run events."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.runtime import WebRunManager
from app.api.schemas import (
    QueryCreate,
    RunView,
    SessionCreate,
    SessionView,
)


def create_app(manager: WebRunManager | None = None) -> FastAPI:
    resolved_manager = manager or WebRunManager()
    web_root = Path(__file__).resolve().parents[1] / "web"
    app = FastAPI(title="TaskLoom Web", version="0.12.0")
    app.state.run_manager = resolved_manager
    app.mount("/assets", StaticFiles(directory=web_root / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "stage": "11-controls"}

    @app.post("/api/sessions", response_model=SessionView, status_code=201)
    def create_session(payload: SessionCreate) -> SessionView:
        return resolved_manager.create_session(payload.title)

    @app.get("/api/sessions", response_model=list[SessionView])
    def list_sessions() -> list[SessionView]:
        return resolved_manager.list_sessions()

    @app.post(
        "/api/sessions/{session_id}/queries",
        response_model=RunView,
        status_code=202,
    )
    async def create_query(session_id: str, payload: QueryCreate) -> RunView:
        try:
            return resolved_manager.start_query(session_id, payload.query)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session 不存在。") from exc

    @app.get("/api/sessions/{session_id}/latest-run", response_model=RunView)
    def latest_run(session_id: str) -> RunView:
        if resolved_manager.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Session 不存在。")
        run = resolved_manager.latest_run(session_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Session 还没有运行记录。")
        return run

    @app.get("/api/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str) -> RunView:
        run = resolved_manager.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run 不存在。")
        return run

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(
        request: Request,
        run_id: str,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        if resolved_manager.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run 不存在。")

        async def event_stream():
            sequence = after
            while True:
                if await request.is_disconnected():
                    break
                events = await resolved_manager.wait_for_events(run_id, sequence)
                if not events:
                    yield ": heartbeat\n\n"
                    continue
                for event in events:
                    sequence = event.sequence
                    payload = event.model_dump(mode="json")
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: {event.event_type}\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                run = resolved_manager.get_run(run_id)
                if run is not None and run.status in {
                    "completed",
                    "failed",
                    "cancelled",
                    "superseded",
                }:
                    break

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    def control_run(run_id: str, action: str) -> RunView:
        try:
            method = getattr(resolved_manager, f"{action}_run")
            return method(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run 不存在。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/pause", response_model=RunView)
    async def pause_run(run_id: str) -> RunView:
        return control_run(run_id, "pause")

    @app.post("/api/runs/{run_id}/resume", response_model=RunView)
    async def resume_run(run_id: str) -> RunView:
        return control_run(run_id, "resume")

    @app.post("/api/runs/{run_id}/cancel", response_model=RunView)
    async def cancel_run(run_id: str) -> RunView:
        return control_run(run_id, "cancel")
    return app


app = create_app()
