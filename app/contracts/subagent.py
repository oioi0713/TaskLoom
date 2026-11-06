"""Planner-requested and supervisor-resolved worker specifications."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.output_registry import OutputContractName
from app.contracts.types import Identifier, NonEmptyStr
from app.policies.capability_registry import CAPABILITY_REGISTRY
from app.policies.tool_policy import validate_tool_request
from app.tools.registry import TOOL_REGISTRY


class SubAgentSpec(BaseModel):
    """A constrained worker configuration proposed by the planner."""

    model_config = ConfigDict(extra="forbid")

    agent_id: Identifier
    task_id: Identifier
    role: NonEmptyStr
    objective: NonEmptyStr
    instructions: list[NonEmptyStr] = Field(min_length=1)
    required_capabilities: list[str] = Field(min_length=1)
    requested_tools: list[str] = Field(default_factory=list)
    knowledge_scope: list[NonEmptyStr] = Field(default_factory=list)
    output_contract: OutputContractName
    timeout_seconds: int = Field(default=120, ge=1, le=120)
    max_steps: int = Field(default=8, ge=1, le=8)
    token_budget: int = Field(default=8192, ge=1, le=8192)
    can_delegate: Literal[False] = False

    @field_validator("required_capabilities")
    @classmethod
    def capabilities_must_be_registered(cls, capabilities: list[str]) -> list[str]:
        unknown = sorted(set(capabilities) - set(CAPABILITY_REGISTRY))
        if unknown:
            raise ValueError(f"Unknown capabilities: {', '.join(unknown)}")
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("required_capabilities must be unique")
        return capabilities

    @field_validator("requested_tools")
    @classmethod
    def requested_tools_must_be_unique_and_known(cls, tools: list[str]) -> list[str]:
        if len(tools) != len(set(tools)):
            raise ValueError("requested_tools must be unique")
        unknown = sorted(set(tools) - set(TOOL_REGISTRY))
        if unknown:
            raise ValueError(f"Unknown requested tools: {', '.join(unknown)}")
        return tools

    @model_validator(mode="after")
    def tools_must_match_capabilities(self) -> "SubAgentSpec":
        validate_tool_request(self.required_capabilities, self.requested_tools)
        return self


class ResolvedSubAgentSpec(SubAgentSpec):
    """A worker specification after supervisor permission resolution."""

    granted_tools: list[str] = Field(default_factory=list)

    @field_validator("granted_tools")
    @classmethod
    def granted_tools_must_be_unique_and_known(cls, tools: list[str]) -> list[str]:
        if len(tools) != len(set(tools)):
            raise ValueError("granted_tools must be unique")
        unknown = sorted(set(tools) - set(TOOL_REGISTRY))
        if unknown:
            raise ValueError(f"Unknown granted tools: {', '.join(unknown)}")
        return tools

    @model_validator(mode="after")
    def granted_tools_must_be_requested(self) -> "ResolvedSubAgentSpec":
        unrequested = sorted(set(self.granted_tools) - set(self.requested_tools))
        if unrequested:
            raise ValueError(
                "Granted tools were not requested: " + ", ".join(unrequested)
            )
        return self
