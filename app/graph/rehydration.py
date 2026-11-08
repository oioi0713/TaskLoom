"""Rebuild typed outer-workflow values after durable JSON checkpoints."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from pydantic import BaseModel

from app.contracts.budget import ExecutionBudget
from app.contracts.memory import MemoryItem
from app.contracts.plan import ExecutionPlan
from app.contracts.result import ToolCallRecord, WorkerResult
from app.contracts.review import ReviewerDecision
from app.contracts.subagent import ResolvedSubAgentSpec, SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.state import RuntimeWorkerInput, Stage6State, WorkflowEvent


def _unwrap_langchain_constructors(value: Any) -> Any:
    """Recursively unwrap JsonPlus ``lc/constructor/kwargs`` envelopes."""

    if (
        isinstance(value, dict)
        and value.get("lc") in {1, 2}
        and value.get("type") == "constructor"
        and isinstance(value.get("kwargs"), dict)
    ):
        return _unwrap_langchain_constructors(value["kwargs"])
    if isinstance(value, dict):
        return {
            key: _unwrap_langchain_constructors(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_unwrap_langchain_constructors(item) for item in value]
    return value


def _model(model_type: type[BaseModel], value: Any) -> Any:
    if value is None or isinstance(value, model_type):
        return value
    return model_type.model_validate(_unwrap_langchain_constructors(value))


def _model_list(model_type: type[BaseModel], values: Any) -> list[Any]:
    return [_model(model_type, value) for value in (values or [])]


def rehydrate_tool_call_records(values: Any) -> list[ToolCallRecord]:
    """Restore nested tool audit records recovered from Redis pending writes."""

    return cast(list[ToolCallRecord], _model_list(ToolCallRecord, values))


def rehydrate_stage6_state(state: Mapping[str, Any]) -> Stage6State:
    """Restore Pydantic contracts flattened by Redis JSON serialization."""

    restored = dict(state)
    restored["budget"] = _model(ExecutionBudget, restored.get("budget"))
    restored["tasks"] = _model_list(TaskSpec, restored.get("tasks"))
    restored["agent_specs"] = _model_list(
        SubAgentSpec,
        restored.get("agent_specs"),
    )
    restored["resolved_agent_specs"] = _model_list(
        ResolvedSubAgentSpec,
        restored.get("resolved_agent_specs"),
    )
    serialized_results = restored.get("active_worker_results")
    if serialized_results is None:
        # Backward compatibility for checkpoints created before active Plan
        # results were separated from historical audit data.
        serialized_results = restored.pop("worker_results", [])
    restored["active_worker_results"] = _model_list(
        WorkerResult,
        serialized_results,
    )
    restored["memory_context"] = _model_list(
        MemoryItem,
        restored.get("memory_context"),
    )
    restored["events"] = _model_list(WorkflowEvent, restored.get("events"))
    restored["execution_plan"] = _model(
        ExecutionPlan,
        restored.get("execution_plan"),
    )
    restored["reviewer_decision"] = _model(
        ReviewerDecision,
        restored.get("reviewer_decision"),
    )
    return cast(Stage6State, restored)


def rehydrate_runtime_worker_input(
    worker_input: Mapping[str, Any],
) -> RuntimeWorkerInput:
    """Restore one dynamic Send payload before entering a Worker node."""

    restored = dict(worker_input)
    restored["task"] = _model(TaskSpec, restored.get("task"))
    restored["spec"] = _model(ResolvedSubAgentSpec, restored.get("spec"))
    restored["dependency_results"] = _model_list(
        WorkerResult,
        restored.get("dependency_results"),
    )
    restored["relevant_memory"] = _model_list(
        MemoryItem,
        restored.get("relevant_memory"),
    )
    return cast(RuntimeWorkerInput, restored)


def with_rehydrated_state(function: Callable[[Stage6State], Any]) -> Callable:
    """Wrap an outer node or route with Redis-safe state reconstruction."""

    def wrapped(state: Stage6State) -> Any:
        return function(rehydrate_stage6_state(state))

    return wrapped


def with_rehydrated_worker_input(
    function: Callable[[RuntimeWorkerInput], Any],
) -> Callable:
    """Wrap a dynamic Worker node with Redis-safe Send reconstruction."""

    def wrapped(worker_input: RuntimeWorkerInput) -> Any:
        return function(rehydrate_runtime_worker_input(worker_input))

    return wrapped
