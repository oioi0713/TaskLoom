"""Execution limits shared by planning and scheduling components."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionBudget(BaseModel):
    """Hard upper bounds for one TaskLoom workflow run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_workers: int = Field(default=8, ge=1, le=8)
    max_concurrency: int = Field(default=3, ge=1, le=3)
    max_retries_per_task: int = Field(default=2, ge=0, le=2)
    max_replans: int = Field(default=2, ge=0, le=2)
    max_total_steps: int = Field(default=30, ge=1, le=30)
    max_worker_timeout_seconds: int = Field(default=120, ge=1, le=120)
    max_worker_token_budget: int = Field(default=8000, ge=1, le=8000)
    total_token_budget: int = Field(default=64000, ge=1)

    @model_validator(mode="after")
    def concurrency_cannot_exceed_worker_limit(self) -> "ExecutionBudget":
        if self.max_concurrency > self.max_workers:
            raise ValueError("max_concurrency cannot exceed max_workers")
        return self
