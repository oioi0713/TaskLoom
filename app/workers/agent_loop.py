"""LangGraph subgraph for one bounded, short-lived autonomous Worker."""

from __future__ import annotations

import json
import operator
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ValidationError

from app.contracts.output_registry import validate_output
from app.contracts.result import ToolCallRecord
from app.observability import LLMTraceRecorder
from app.progress import run_with_heartbeat
from app.tools.runtime import ToolRuntime
from app.workers.context import WorkerExecutionContext


WorkerLoopStatus = Literal[
    "running",
    "completed",
    "failed",
    "timeout",
    "budget_exhausted",
    "permission_denied",
    "invalid_output",
]


class WorkerAgentState(TypedDict):
    """Private state owned by exactly one Worker subgraph invocation."""

    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    token_usage: int
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    final_output: dict[str, Any] | None
    status: WorkerLoopStatus
    error: str | None
    validation_attempts: int
    last_tool_signature: str | None
    tool_call_counts: dict[str, int]
    permission_denials: int


@dataclass(frozen=True, slots=True)
class WorkerLoopOutcome:
    status: WorkerLoopStatus
    output: dict[str, Any] | None
    error: str | None
    token_usage: int
    tool_calls: list[ToolCallRecord]
    step_count: int


def _extract_token_usage(raw_output: Any) -> int:
    raw_message = raw_output
    if isinstance(raw_output, dict) and "raw" in raw_output:
        raw_message = raw_output.get("raw")
    usage = getattr(raw_message, "usage_metadata", None) or {}
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    response_metadata = getattr(raw_message, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage", {})
    total = token_usage.get("total_tokens")
    return (
        total
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0
        else 0
    )


def _normalize_structured_output(raw_output: Any) -> dict[str, Any]:
    if (
        isinstance(raw_output, dict)
        and {"raw", "parsed", "parsing_error"}.issubset(raw_output)
    ):
        parsing_error = raw_output["parsing_error"]
        if parsing_error is not None:
            if isinstance(parsing_error, BaseException):
                raise parsing_error
            raise ValueError(str(parsing_error))
        raw_output = raw_output["parsed"]
    if isinstance(raw_output, BaseModel):
        return raw_output.model_dump(mode="python")
    if isinstance(raw_output, dict):
        return dict(raw_output)
    raise TypeError("Structured Worker output must be a Pydantic model or mapping.")


def run_worker_agent_loop(
    *,
    context: WorkerExecutionContext,
    initial_messages: list[BaseMessage],
    loop_model: Any,
    structured_model: Any,
    tool_runtime: ToolRuntime,
    max_output_tokens: int,
    progress: Callable[[str], None] | None = None,
    trace_recorder: LLMTraceRecorder | None = None,
    model_name: str | None = None,
) -> WorkerLoopOutcome:
    """Compile and run one fixed Worker Agent Loop with isolated state."""

    started = time.perf_counter()
    granted_tools = frozenset(context.spec.granted_tools)

    def elapsed_seconds() -> float:
        return time.perf_counter() - started

    def budget_error(state: WorkerAgentState) -> tuple[WorkerLoopStatus, str] | None:
        if elapsed_seconds() >= context.spec.timeout_seconds:
            return "timeout", (
                f"Worker exceeded timeout_seconds={context.spec.timeout_seconds}."
            )
        if state["step_count"] >= context.spec.max_steps:
            status: WorkerLoopStatus = (
                "permission_denied"
                if state["permission_denials"]
                else "budget_exhausted"
            )
            return status, f"Worker exhausted max_steps={context.spec.max_steps}."
        return None

    def trace_request(
        component: str,
        messages: list[BaseMessage],
        attempt: int,
    ) -> str | None:
        if trace_recorder is None:
            return None
        return trace_recorder.record_request(
            component=component,
            messages=messages,
            attempt=attempt,
            model=model_name,
            task_id=context.task.task_id,
            agent_id=context.spec.agent_id,
            max_tokens=max_output_tokens,
        )

    def worker_llm(state: WorkerAgentState) -> dict[str, Any]:
        exhausted = budget_error(state)
        if exhausted is not None:
            status, error = exhausted
            return {"status": status, "error": error}
        step = state["step_count"] + 1
        if progress is not None:
            progress(f"Agent Loop 第 {step}/{context.spec.max_steps} 步：LLM 决策")
        messages = list(state["messages"])
        call_id = trace_request("worker_agent", messages, step)
        try:
            response = run_with_heartbeat(
                lambda: loop_model.invoke(messages),
                progress=progress,
                label="Agent Loop LLM 请求",
            )
            if trace_recorder is not None and call_id is not None:
                trace_recorder.record_response(call_id, response)
            if not isinstance(response, AIMessage):
                raise TypeError("Tool-enabled Worker model must return an AIMessage.")
            usage = _extract_token_usage(response)
            update: dict[str, Any] = {
                "messages": [response],
                "step_count": step,
                "token_usage": state["token_usage"] + usage,
            }
            if elapsed_seconds() >= context.spec.timeout_seconds:
                update.update(
                    status="timeout",
                    error=(
                        f"Worker exceeded timeout_seconds={context.spec.timeout_seconds}."
                    ),
                )
            return update
        except TimeoutError as exc:
            if trace_recorder is not None and call_id is not None:
                trace_recorder.record_error(call_id, exc)
            return {"status": "timeout", "error": str(exc) or "Worker timed out."}
        except Exception as exc:
            if trace_recorder is not None and call_id is not None:
                trace_recorder.record_error(call_id, exc)
            return {
                "status": "failed",
                "error": f"Worker Agent Loop failed ({type(exc).__name__}): {exc}",
            }

    def route_worker_action(state: WorkerAgentState) -> str:
        if state["status"] != "running":
            return "end"
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tool"
        return "validate"

    def authorized_tool_node(state: WorkerAgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return {"status": "failed", "error": "Tool route had no AIMessage."}

        records: list[ToolCallRecord] = []
        observations: list[ToolMessage] = []
        counts = dict(state["tool_call_counts"])
        last_signature = state["last_tool_signature"]
        denials = state["permission_denials"]

        def append_record(record: ToolCallRecord) -> None:
            records.append(record)
            if trace_recorder is not None:
                trace_recorder.record_tool_call(
                    task_id=context.task.task_id,
                    agent_id=context.spec.agent_id,
                    record=record,
                )

        for tool_call in last.tool_calls:
            name = str(tool_call.get("name", ""))
            call_id = str(tool_call.get("id") or f"tool-{len(records) + 1}")
            arguments = tool_call.get("args", {})
            if not isinstance(arguments, dict):
                arguments = {"_raw": arguments}
            signature = json.dumps(
                [name, arguments], ensure_ascii=False, sort_keys=True, default=str
            )

            if signature == last_signature:
                message = "Loop protection stopped a repeated identical tool call."
                append_record(
                    ToolCallRecord(
                        call_id=call_id,
                        tool_name=name or "unknown_tool",
                        arguments=arguments,
                        success=False,
                        error=message,
                    )
                )
                return {
                    "tool_calls": records,
                    "status": "failed",
                    "error": message,
                    "last_tool_signature": signature,
                }

            if name not in granted_tools or name not in tool_runtime.executable_tools:
                denials += 1
                message = f"Tool '{name}' is not authorized for this Worker."
                observation = {
                    "error": message,
                    "permission_denied": True,
                    "recoverable": True,
                }
                append_record(
                    ToolCallRecord(
                        call_id=call_id,
                        tool_name=name or "unknown_tool",
                        arguments=arguments,
                        success=False,
                        error=message,
                        observation=observation,
                    )
                )
                observations.append(
                    ToolMessage(
                        content=json.dumps(observation, ensure_ascii=False),
                        tool_call_id=call_id,
                        name=name or "unknown_tool",
                    )
                )
                last_signature = signature
                continue

            counts[name] = counts.get(name, 0) + 1
            if counts[name] > tool_runtime.max_calls_per_tool:
                message = (
                    f"Tool '{name}' exceeded max_calls_per_tool="
                    f"{tool_runtime.max_calls_per_tool}."
                )
                append_record(
                    ToolCallRecord(
                        call_id=call_id,
                        tool_name=name,
                        arguments=arguments,
                        success=False,
                        error=message,
                    )
                )
                return {
                    "tool_calls": records,
                    "status": "budget_exhausted",
                    "error": message,
                    "tool_call_counts": counts,
                    "last_tool_signature": signature,
                }

            if progress is not None:
                progress(f"调用工具 {name}，call_id={call_id}")
            execution = tool_runtime.execute(name, arguments)
            observation = {
                **execution.observation,
                "tool_name": name,
                "tool_call_id": call_id,
            }
            append_record(
                ToolCallRecord(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    success=execution.success,
                    error=execution.error,
                    execution_time_ms=execution.execution_time_ms,
                    observation=observation,
                )
            )
            observations.append(
                ToolMessage(
                    content=json.dumps(observation, ensure_ascii=False),
                    tool_call_id=call_id,
                    name=name,
                )
            )
            last_signature = signature
            if progress is not None:
                result_text = "成功" if execution.success else "失败，可由 Agent 调整策略"
                progress(f"工具 {name} {result_text}")
            if not execution.success and not observation.get("recoverable", True):
                return {
                    "messages": observations,
                    "tool_calls": records,
                    "tool_call_counts": counts,
                    "last_tool_signature": signature,
                    "permission_denials": denials,
                    "status": "failed",
                    "error": f"Tool '{name}' returned an unrecoverable error.",
                }

        update = {
            "messages": observations,
            "tool_calls": records,
            "tool_call_counts": counts,
            "last_tool_signature": last_signature,
            "permission_denials": denials,
        }
        if elapsed_seconds() >= context.spec.timeout_seconds:
            update.update(
                status="timeout",
                error=f"Worker exceeded timeout_seconds={context.spec.timeout_seconds}.",
            )
        return update

    def record_observation(state: WorkerAgentState) -> dict[str, Any]:
        if progress is not None and state["status"] == "running":
            progress("Tool Observation 已写回私有上下文，继续由 LLM 判断")
        return {}

    def validate_worker_output(state: WorkerAgentState) -> dict[str, Any]:
        if elapsed_seconds() >= context.spec.timeout_seconds:
            return {
                "status": "timeout",
                "error": f"Worker exceeded timeout_seconds={context.spec.timeout_seconds}.",
            }
        attempt = state["validation_attempts"] + 1
        messages = [
            *state["messages"],
            HumanMessage(
                content=(
                    "You have finished gathering information. Return the complete "
                    f"final object for {context.spec.output_contract}. Use only the "
                    "task context and tool observations. Do not add unregistered fields."
                )
            ),
        ]
        if progress is not None:
            progress(
                "Agent Loop 已结束：生成并校验最终结构化输出"
                "（Finalizer 不占 Agent Loop 步数）"
            )
        call_id = trace_request("worker_finalizer", messages, attempt)
        response_recorded = False
        try:
            raw_output = run_with_heartbeat(
                lambda: structured_model.invoke(messages),
                progress=progress,
                label="最终结构化输出 LLM 请求",
            )
            if trace_recorder is not None and call_id is not None:
                trace_recorder.record_response(call_id, raw_output)
                response_recorded = True
            usage = _extract_token_usage(raw_output)
            payload = _normalize_structured_output(raw_output)
            output = validate_output(context.spec.output_contract, payload).model_dump(
                mode="json"
            )
            if progress is not None:
                progress("最终结构化输出校验通过")
            return {
                "token_usage": state["token_usage"] + usage,
                "validation_attempts": attempt,
                "final_output": output,
                "status": "completed",
                "error": None,
            }
        except (ValidationError, OutputParserException, TypeError, ValueError) as exc:
            if response_recorded and trace_recorder is not None and call_id is not None:
                trace_recorder.record_validation_error(call_id, exc)
            usage = _extract_token_usage(locals().get("raw_output"))
            update: dict[str, Any] = {
                "token_usage": state["token_usage"] + usage,
                "validation_attempts": attempt,
            }
            can_retry = (
                attempt < 2
                and elapsed_seconds() < context.spec.timeout_seconds
            )
            if can_retry:
                if progress is not None:
                    progress("最终输出格式不合法，反馈具体错误后允许修正一次")
                update["messages"] = [
                    HumanMessage(
                        content=(
                            f"Final output failed {context.spec.output_contract}. "
                            "Correct every validation error and then finish without "
                            f"more tool calls unless essential. Validation error: {exc}"
                        )
                    )
                ]
                return update
            update.update(
                status="invalid_output",
                error=(
                    f"Worker output failed {context.spec.output_contract} "
                    f"after {attempt} finalization attempts: {exc}"
                ),
            )
            return update
        except TimeoutError as exc:
            if trace_recorder is not None and call_id is not None:
                trace_recorder.record_error(call_id, exc)
            return {"status": "timeout", "error": str(exc) or "Worker timed out."}
        except Exception as exc:
            if trace_recorder is not None and call_id is not None:
                trace_recorder.record_error(call_id, exc)
            return {
                "status": "failed",
                "error": f"Worker finalization failed ({type(exc).__name__}): {exc}",
            }

    def route_after_validation(state: WorkerAgentState) -> str:
        return "retry" if state["status"] == "running" else "end"

    builder = StateGraph(WorkerAgentState)
    builder.add_node("worker_llm", worker_llm)
    builder.add_node("authorized_tool", authorized_tool_node)
    builder.add_node("record_observation", record_observation)
    builder.add_node("validate_worker_output", validate_worker_output)
    builder.add_edge(START, "worker_llm")
    builder.add_conditional_edges(
        "worker_llm",
        route_worker_action,
        {"tool": "authorized_tool", "validate": "validate_worker_output", "end": END},
    )
    builder.add_edge("authorized_tool", "record_observation")
    builder.add_conditional_edges(
        "record_observation",
        lambda state: "continue" if state["status"] == "running" else "end",
        {"continue": "worker_llm", "end": END},
    )
    builder.add_conditional_edges(
        "validate_worker_output",
        route_after_validation,
        {"retry": "validate_worker_output", "end": END},
    )
    graph = builder.compile(name="taskloom-worker-agent-loop")
    final_state = graph.invoke(
        {
            "messages": initial_messages,
            "step_count": 0,
            "token_usage": 0,
            "tool_calls": [],
            "final_output": None,
            "status": "running",
            "error": None,
            "validation_attempts": 0,
            "last_tool_signature": None,
            "tool_call_counts": {},
            "permission_denials": 0,
        },
        config={"recursion_limit": max(20, context.spec.max_steps * 4)},
    )
    return WorkerLoopOutcome(
        status=final_state["status"],
        output=final_state["final_output"],
        error=final_state["error"],
        token_usage=final_state["token_usage"],
        tool_calls=list(final_state["tool_calls"]),
        step_count=final_state["step_count"],
    )
