"""Bounded session-memory records recalled into Planner and Worker context."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.types import NonEmptyStr


class MemoryItem(BaseModel):
    """A bounded memory record supplied as context, never as an instruction."""

    model_config = ConfigDict(extra="forbid")

    memory_id: NonEmptyStr
    content: NonEmptyStr
    scope: list[NonEmptyStr] = Field(default_factory=list)
    kind: Literal["fact", "decision", "constraint", "todo", "failure"] = "fact"
    source_run_id: str | None = None
    source_task_id: str | None = None
    plan_version: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
