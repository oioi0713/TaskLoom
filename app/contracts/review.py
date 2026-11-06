"""Structured decision returned by the fixed reviewer agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.types import Identifier, NonEmptyStr
from app.contracts.result import WorkerResult
from app.contracts.task import TaskSpec


class ReviewerInput(BaseModel):
    """Bounded workflow evidence supplied to the stage-seven Reviewer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    original_goal: NonEmptyStr
    plan_version: int = Field(ge=1)
    expected_deliverable: NonEmptyStr
    tasks: list[TaskSpec] = Field(min_length=1)
    latest_results: list[WorkerResult] = Field(default_factory=list)
    retry_count: int = Field(ge=0)
    replan_count: int = Field(ge=0)
    total_steps: int = Field(ge=0)
    max_retries_per_task: int = Field(ge=0)
    max_replans: int = Field(ge=0)
    max_total_steps: int = Field(ge=1)


class ReviewerDecision(BaseModel):
    """Reviewer routing decision and actionable feedback."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["finish", "retry_tasks", "replan", "fail"]
    score: float = Field(ge=0.0, le=1.0)
    problems: list[NonEmptyStr] = Field(default_factory=list)
    suggestions: list[NonEmptyStr] = Field(default_factory=list)
    target_task_ids: list[Identifier] = Field(default_factory=list)
    replan_constraints: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def decision_must_include_required_targets(self) -> "ReviewerDecision":
        if self.action == "retry_tasks" and not self.target_task_ids:
            raise ValueError("retry_tasks requires target_task_ids")
        if self.action != "retry_tasks" and self.target_task_ids:
            raise ValueError("target_task_ids are allowed only for retry_tasks")
        if self.action == "replan" and not self.replan_constraints:
            raise ValueError("replan requires replan_constraints")
        if self.action != "replan" and self.replan_constraints:
            raise ValueError("replan_constraints are allowed only for replan")
        if len(self.target_task_ids) != len(set(self.target_task_ids)):
            raise ValueError("target_task_ids must be unique")
        return self
