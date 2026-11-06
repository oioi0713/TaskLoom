"""Minimal inputs exposed to one short-lived Worker Sub-Agent."""

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.memory import MemoryItem
from app.contracts.result import WorkerResult
from app.contracts.subagent import ResolvedSubAgentSpec
from app.contracts.task import TaskSpec
from app.contracts.types import NonEmptyStr


class RuntimeContext(BaseModel):
    """Runtime data already filtered for one worker invocation.

    Callers must pass only task-relevant memory. Main-conversation messages,
    credentials, provider clients, and the complete system tool registry are
    deliberately not part of this contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyStr
    relevant_memory: tuple[MemoryItem, ...] = Field(default_factory=tuple)
    review_feedback: tuple[NonEmptyStr, ...] = Field(default_factory=tuple)


class GrantedToolContext(BaseModel):
    """Safe metadata for one tool the Supervisor has already granted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyStr
    description: NonEmptyStr
    risk: NonEmptyStr


class WorkerExecutionContext(BaseModel):
    """Complete isolated context retained only for a single execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyStr
    task: TaskSpec
    spec: ResolvedSubAgentSpec
    dependency_results: tuple[WorkerResult, ...] = Field(default_factory=tuple)
    relevant_memory: tuple[MemoryItem, ...] = Field(default_factory=tuple)
    granted_tools: tuple[GrantedToolContext, ...] = Field(default_factory=tuple)
    review_feedback: tuple[NonEmptyStr, ...] = Field(default_factory=tuple)
