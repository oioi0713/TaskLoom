"""Shared state schema for the future TaskLoom graph."""

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.budget import ExecutionBudget
from app.contracts.memory import MemoryItem
from app.contracts.plan import ExecutionPlan
from app.contracts.result import WorkerResult
from app.contracts.review import ReviewerDecision
from app.contracts.subagent import ResolvedSubAgentSpec, SubAgentSpec
from app.contracts.task import TaskSpec
from app.contracts.types import NonEmptyStr


class WorkflowEvent(BaseModel):
    """One observable state-transition event."""

    model_config = ConfigDict(extra="forbid")

    event_type: NonEmptyStr
    message: NonEmptyStr
    data: dict[str, Any] = Field(default_factory=dict)


class AgentState(TypedDict):
    """State shared by fixed nodes and dynamic worker fan-out operations."""

    run_id: str
    thread_id: str
    session_id: str
    user_id: str
    original_goal: str
    clarified_goal: str
    plan_version: int
    tasks: list[TaskSpec]
    agent_specs: list[SubAgentSpec]
    active_worker_results: Annotated[list[WorkerResult], operator.add]
    ready_task_ids: list[str]
    reviewer_decision: ReviewerDecision | None
    retry_count: int
    replan_count: int
    total_steps: int
    budget: ExecutionBudget
    memory_context: list[MemoryItem]
    final_output: str | None
    errors: Annotated[list[str], operator.add]
    events: Annotated[list[WorkflowEvent], operator.add]


class Stage3State(AgentState):
    """Additional state used by the no-LLM stage-three workflow."""

    execution_plan: ExecutionPlan | None
    workflow_status: Literal["created", "running", "completed", "failed"]


class Stage6State(AgentState):
    """State for the real Planner-to-Worker runtime workflow."""

    execution_plan: ExecutionPlan | None
    resolved_agent_specs: list[ResolvedSubAgentSpec]
    user_allowed_tools: list[str]
    enabled_tools: list[str]
    review_feedback: dict[str, list[str]]
    replan_constraints: list[str]
    workflow_status: Literal["created", "running", "completed", "failed"]


class WorkerInput(TypedDict):
    """Isolated input sent to one short-lived mock worker invocation."""

    run_id: str
    task: TaskSpec
    spec: SubAgentSpec
    dependency_results: list[WorkerResult]


class RuntimeWorkerInput(TypedDict):
    """Minimal input sent to one real short-lived Worker invocation."""

    run_id: str
    thread_id: str
    plan_version: int
    task: TaskSpec
    spec: ResolvedSubAgentSpec
    dependency_results: list[WorkerResult]
    relevant_memory: list[MemoryItem]
    review_feedback: list[str]
