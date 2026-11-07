"""Structured result and audit records returned by a worker runtime."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.output_models import Source
from app.contracts.output_registry import OutputContractName, validate_output
from app.contracts.types import Identifier, NonEmptyStr


class ToolCallRecord(BaseModel):
    """Auditable record of one tool invocation."""

    model_config = ConfigDict(extra="forbid")

    call_id: str | None = None
    tool_name: NonEmptyStr
    arguments: dict[str, Any] = Field(default_factory=dict)
    success: bool
    error: str | None = None
    execution_time_ms: int = Field(default=0, ge=0)
    observation: dict[str, Any] | None = None


class WorkerResult(BaseModel):
    """Validated result envelope for a short-lived worker."""

    model_config = ConfigDict(extra="forbid")

    run_id: NonEmptyStr
    task_id: Identifier
    agent_id: Identifier
    status: Literal[
        "completed",
        "failed",
        "timeout",
        "invalid_output",
        "permission_denied",
        "budget_exhausted",
    ]
    output_contract: OutputContractName
    output: dict[str, Any] | None = None
    sources: list[Source] = Field(default_factory=list)
    error: str | None = None
    execution_time_ms: int = Field(ge=0)
    token_usage: int = Field(ge=0)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "WorkerResult":
        if self.status == "completed":
            if self.output is None:
                raise ValueError("A completed worker must provide output")
            validate_output(self.output_contract, self.output)
        elif not self.error:
            raise ValueError("A non-completed worker must provide an error")
        return self
