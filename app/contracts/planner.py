"""Input limits and context supplied to the real Planner agent."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.memory import MemoryItem
from app.contracts.output_registry import OUTPUT_CONTRACTS
from app.contracts.types import NonEmptyStr
from app.policies.capability_registry import CAPABILITY_REGISTRY


class PlannerLimits(BaseModel):
    """Non-negotiable limits visible to, but not editable by, the Planner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tasks: int = Field(default=8, ge=1, le=8)
    max_workers: int = Field(default=8, ge=1, le=8)
    max_concurrency: int = Field(default=3, ge=1, le=3)
    max_timeout_seconds: int = Field(default=120, ge=1, le=120)
    max_steps_per_worker: int = Field(default=8, ge=1, le=8)
    max_tokens_per_worker: int = Field(default=8000, ge=1, le=8000)

    @model_validator(mode="after")
    def concurrency_cannot_exceed_workers(self) -> "PlannerLimits":
        if self.max_concurrency > self.max_workers:
            raise ValueError("max_concurrency cannot exceed max_workers")
        return self


class PlannerInput(BaseModel):
    """User goal plus the exact registries and limits available for planning."""

    model_config = ConfigDict(extra="forbid")

    user_goal: NonEmptyStr
    available_capabilities: list[str] = Field(min_length=1)
    available_output_contracts: list[str] = Field(min_length=1)
    limits: PlannerLimits = Field(default_factory=PlannerLimits)
    recalled_memory: list[MemoryItem] = Field(default_factory=list)

    @field_validator("available_capabilities")
    @classmethod
    def capabilities_must_be_registered(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - set(CAPABILITY_REGISTRY))
        if unknown:
            raise ValueError(f"Unknown available capabilities: {', '.join(unknown)}")
        if len(values) != len(set(values)):
            raise ValueError("available_capabilities must be unique")
        return values

    @field_validator("available_output_contracts")
    @classmethod
    def output_contracts_must_be_registered(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - set(OUTPUT_CONTRACTS))
        if unknown:
            raise ValueError(
                f"Unknown available output contracts: {', '.join(unknown)}"
            )
        if len(values) != len(set(values)):
            raise ValueError("available_output_contracts must be unique")
        return values
