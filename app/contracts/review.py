"""Structured decision returned by the fixed reviewer agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.types import Identifier, NonEmptyStr


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
        if self.action == "replan" and not self.replan_constraints:
            raise ValueError("replan requires replan_constraints")
        if len(self.target_task_ids) != len(set(self.target_task_ids)):
            raise ValueError("target_task_ids must be unique")
        return self
