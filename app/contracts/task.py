"""Task contract produced by the planner and consumed by the supervisor."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.output_registry import OutputContractName
from app.contracts.types import Identifier, NonEmptyStr


TaskStatus = Literal[
    "pending",
    "ready",
    "running",
    "completed",
    "failed",
    "blocked",
    "skipped",
]


class TaskSpec(BaseModel):
    """A single dependency-aware unit of work."""

    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    title: NonEmptyStr
    description: NonEmptyStr
    task_type: NonEmptyStr
    dependencies: list[Identifier] = Field(default_factory=list)
    priority: int = 0
    assigned_agent_id: Identifier
    output_contract: OutputContractName
    status: TaskStatus = "pending"
    retry_count: int = Field(default=0, ge=0, le=2)
    max_retries: int = Field(default=2, ge=0, le=2)

    @model_validator(mode="after")
    def validate_local_task_invariants(self) -> "TaskSpec":
        if self.task_id in self.dependencies:
            raise ValueError("A task cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("Task dependencies must be unique")
        if self.retry_count > self.max_retries:
            raise ValueError("retry_count cannot exceed max_retries")
        return self
