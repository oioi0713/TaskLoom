"""Public request and response contracts for the web API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=80)


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    title: str
    created_at: datetime
    latest_run_id: str | None = None


class QueryCreate(BaseModel):
    query: str = Field(min_length=1, max_length=12000)


class RunEventView(BaseModel):
    sequence: int
    event_type: str
    message: str
    created_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class RunView(BaseModel):
    run_id: str
    thread_id: str
    session_id: str
    query: str
    status: Literal[
        "queued",
        "running",
        "pause_requested",
        "pausing_after_tools",
        "paused",
        "resuming",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
        "superseded",
    ]
    phase: str
    pending_action: str | None = None
    active_tools: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    worker_results: list[dict[str, Any]] = Field(default_factory=list)
    memory_count: int = 0
    final_output: str | None = None
    errors: list[str] = Field(default_factory=list)
    event_count: int = 0
