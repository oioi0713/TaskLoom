"""Deterministic mock Reviewer and the stage-seven structured LLM Reviewer."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from app.contracts.output_models import ReportResult
from app.contracts.review import ReviewerDecision, ReviewerInput
from app.graph.state import Stage3State, Stage6State, WorkflowEvent
from app.observability import LLMTraceRecorder
from app.progress import invoke_model_cancellable
from app.runtime_control import RunControl


class ReviewGenerationError(RuntimeError):
    """Raised when the Reviewer cannot return a valid bounded decision."""


@lru_cache(maxsize=1)
def load_reviewer_system_prompt() -> str:
    """Load the fixed Reviewer scope and routing rules."""

    prompt_path = Path(__file__).with_name("prompts") / "reviewer.md"
    return prompt_path.read_text(encoding="utf-8")


def build_reviewer_messages(review_input: ReviewerInput) -> list[BaseMessage]:
    """Build a bounded evidence package without private Worker messages."""

    payload = review_input.model_dump(mode="json")
    return [
        SystemMessage(content=load_reviewer_system_prompt()),
        HumanMessage(
            content=(
                "Review this completed or terminal workflow state and return a "
                "ReviewerDecision:\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            )
        ),
    ]


class ReviewerAgent:
    """Judge result quality and request bounded retry, replan, finish, or fail."""

    def __init__(
        self,
        structured_model: Any,
        *,
        trace_recorder: LLMTraceRecorder | None = None,
        model_name: str | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self._structured_model = structured_model
        self._trace_recorder = trace_recorder
        self._model_name = model_name
        self._max_output_tokens = max_output_tokens

    @classmethod
    def from_chat_model(
        cls,
        chat_model: Any,
        *,
        structured_output_method: str = "function_calling",
        trace_recorder: LLMTraceRecorder | None = None,
        model_name: str | None = None,
        max_output_tokens: int = 2048,
    ) -> "ReviewerAgent":
        structured_model = chat_model.with_structured_output(
            ReviewerDecision,
            method=structured_output_method,
            include_raw=True,
            max_tokens=max_output_tokens,
        )
        return cls(
            structured_model,
            trace_recorder=trace_recorder,
            model_name=model_name,
            max_output_tokens=max_output_tokens,
        )

    def review(
        self,
        review_input: ReviewerInput,
        *,
        execution_control: RunControl | None = None,
    ) -> ReviewerDecision:
        """Return one structurally valid decision, retrying format once."""

        messages = build_reviewer_messages(review_input)
        last_error: BaseException | None = None
        for attempt in (1, 2):
            call_id = (
                self._trace_recorder.record_request(
                    component="reviewer",
                    messages=list(messages),
                    attempt=attempt,
                    model=self._model_name,
                    max_tokens=self._max_output_tokens,
                )
                if self._trace_recorder is not None
                else None
            )
            try:
                raw_decision = invoke_model_cancellable(
                    self._structured_model,
                    messages,
                    execution_control=execution_control,
                    operation_name="reviewer",
                )
            except Exception as exc:
                if self._trace_recorder is not None and call_id is not None:
                    self._trace_recorder.record_error(call_id, exc)
                if not isinstance(
                    exc,
                    (ValidationError, OutputParserException, TypeError, ValueError),
                ):
                    raise
                last_error = exc
            else:
                if self._trace_recorder is not None and call_id is not None:
                    self._trace_recorder.record_response(call_id, raw_decision)
                try:
                    return self._normalize_output(raw_decision)
                except (
                    ValidationError,
                    OutputParserException,
                    TypeError,
                    ValueError,
                ) as exc:
                    if self._trace_recorder is not None and call_id is not None:
                        self._trace_recorder.record_validation_error(call_id, exc)
                    last_error = exc

            if attempt == 1:
                messages.append(
                    HumanMessage(
                        content=(
                            "Your previous ReviewerDecision was invalid. Return a "
                            "complete replacement matching the schema. Error: "
                            f"{last_error}"
                        )
                    )
                )

        raise ReviewGenerationError(
            f"Reviewer failed to produce a valid decision: {last_error}"
        ) from last_error

    @staticmethod
    def _normalize_output(raw_decision: Any) -> ReviewerDecision:
        if (
            isinstance(raw_decision, dict)
            and {"raw", "parsed", "parsing_error"}.issubset(raw_decision)
        ):
            parsing_error = raw_decision["parsing_error"]
            if parsing_error is not None:
                if isinstance(parsing_error, BaseException):
                    raise parsing_error
                raise ValueError(str(parsing_error))
            raw_decision = raw_decision["parsed"]
        if isinstance(raw_decision, ReviewerDecision):
            return raw_decision
        return ReviewerDecision.model_validate(raw_decision)


def build_runtime_reviewer_input(state: Stage6State) -> ReviewerInput:
    """Project graph state into the evidence visible to the Reviewer."""

    plan = state.get("execution_plan")
    if plan is None or not state.get("tasks"):
        raise ValueError("A validated execution plan is required for LLM review.")
    current_task_ids = {task.task_id for task in state["tasks"]}
    latest_by_task = {
        result.task_id: result
        for result in state.get("active_worker_results", [])
        if result.task_id in current_task_ids
    }
    budget = state["budget"]
    return ReviewerInput(
        original_goal=state["original_goal"],
        plan_version=state["plan_version"],
        expected_deliverable=plan.expected_deliverable,
        tasks=state["tasks"],
        latest_results=list(latest_by_task.values()),
        retry_count=state.get("retry_count", 0),
        replan_count=state.get("replan_count", 0),
        total_steps=state.get("total_steps", 0),
        max_retries_per_task=budget.max_retries_per_task,
        max_replans=budget.max_replans,
        max_total_steps=budget.max_total_steps,
    )


def enforce_reviewer_decision(
    state: Stage6State,
    decision: ReviewerDecision,
) -> ReviewerDecision:
    """Reject unsafe or impossible LLM routing decisions deterministically."""

    tasks_by_id = {task.task_id: task for task in state.get("tasks", [])}
    budget = state["budget"]
    reasons: list[str] = []
    if decision.action == "finish":
        incomplete = [
            task.task_id
            for task in tasks_by_id.values()
            if task.status != "completed"
        ]
        if incomplete:
            reasons.append(
                "Reviewer attempted finish with incomplete tasks: "
                + ", ".join(sorted(incomplete))
            )
        if state.get("errors"):
            reasons.append("Reviewer attempted finish while workflow errors exist.")

    elif decision.action == "retry_tasks":
        unknown = sorted(set(decision.target_task_ids) - set(tasks_by_id))
        if unknown:
            reasons.append("Reviewer targeted unknown tasks: " + ", ".join(unknown))
        exhausted = sorted(
            task_id
            for task_id in decision.target_task_ids
            if task_id in tasks_by_id
            and tasks_by_id[task_id].retry_count
            >= min(
                tasks_by_id[task_id].max_retries,
                budget.max_retries_per_task,
            )
        )
        if exhausted:
            reasons.append(
                "Reviewer targeted tasks with exhausted retries: "
                + ", ".join(exhausted)
            )
        if state.get("total_steps", 0) >= budget.max_total_steps:
            reasons.append("Workflow exhausted its total-step budget.")

    elif decision.action == "replan":
        if state.get("replan_count", 0) >= budget.max_replans:
            reasons.append("Workflow exhausted its replan budget.")
        if state.get("total_steps", 0) >= budget.max_total_steps:
            reasons.append("Workflow exhausted its total-step budget.")

    if not reasons:
        return decision
    return ReviewerDecision(
        action="fail",
        score=0.0,
        problems=[*decision.problems, *reasons],
        suggestions=decision.suggestions,
    )


def build_runtime_review_update(
    state: Stage6State,
    decision: ReviewerDecision,
) -> dict:
    """Convert a guarded Reviewer decision into a graph-state update."""

    decision = enforce_reviewer_decision(state, decision)
    update: dict[str, Any] = {
        "reviewer_decision": decision,
        "events": [
            WorkflowEvent(
                event_type="review.completed",
                message=f"Reviewer selected {decision.action}.",
                data={
                    "action": decision.action,
                    "score": decision.score,
                    "target_task_ids": decision.target_task_ids,
                },
            )
        ],
    }
    if decision.action == "finish":
        latest_results = {
            result.task_id: result
            for result in state.get("active_worker_results", [])
        }
        depended_on = {
            dependency
            for task in state["tasks"]
            for dependency in task.dependencies
        }
        terminal_ids = [
            task.task_id
            for task in state["tasks"]
            if task.task_id not in depended_on
        ]
        update.update(
            {
                "workflow_status": "completed",
                "final_output": json.dumps(
                    {
                        task_id: latest_results[task_id].output
                        for task_id in terminal_ids
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        )
    elif decision.action == "fail":
        update.update({"workflow_status": "failed", "final_output": None})
    else:
        update.update({"workflow_status": "running", "final_output": None})
    return update


def reviewer_node(state: Stage3State) -> dict:
    """Finish only when every fixed task has a valid completed result."""

    completed_ids = {
        result.task_id
        for result in state.get("active_worker_results", [])
        if result.status == "completed"
    }
    expected_ids = {task.task_id for task in state["tasks"]}
    all_tasks_completed = completed_ids == expected_ids and all(
        task.status == "completed" for task in state["tasks"]
    )

    if all_tasks_completed and not state.get("errors"):
        report_result = next(
            result
            for result in state["active_worker_results"]
            if result.task_id == "T5"
        )
        report = ReportResult.model_validate(report_result.output)
        decision = ReviewerDecision(
            action="finish",
            score=1.0,
            problems=[],
            suggestions=[],
        )
        return {
            "reviewer_decision": decision,
            "workflow_status": "completed",
            "final_output": f"{report.title}\n\n{report.executive_summary}",
            "events": [
                WorkflowEvent(
                    event_type="review.completed",
                    message="Reviewer accepted all five mock task results.",
                    data={"score": decision.score},
                )
            ],
        }

    missing = sorted(expected_ids - completed_ids)
    decision = ReviewerDecision(
        action="fail",
        score=0.0,
        problems=[
            "The mock workflow did not complete every required task."
            + (f" Missing: {', '.join(missing)}." if missing else "")
        ],
        suggestions=[],
    )
    return {
        "reviewer_decision": decision,
        "workflow_status": "failed",
        "final_output": None,
        "events": [
            WorkflowEvent(
                event_type="review.completed",
                message="Reviewer rejected the incomplete mock workflow.",
                data={"missing_task_ids": missing},
            )
        ],
    }


def runtime_reviewer_node(state: Stage6State) -> dict:
    """Finish only when every dynamic task has a completed WorkerResult."""

    latest_results = {
        result.task_id: result
        for result in state.get("active_worker_results", [])
    }
    incomplete = [task for task in state["tasks"] if task.status != "completed"]
    if not incomplete and not state.get("errors"):
        depended_on = {
            dependency
            for task in state["tasks"]
            for dependency in task.dependencies
        }
        terminal_ids = [
            task.task_id
            for task in state["tasks"]
            if task.task_id not in depended_on
        ]
        final_payload = {
            task_id: latest_results[task_id].output for task_id in terminal_ids
        }
        decision = ReviewerDecision(
            action="finish",
            score=1.0,
            problems=[],
            suggestions=[],
        )
        return {
            "reviewer_decision": decision,
            "workflow_status": "completed",
            "final_output": json.dumps(
                final_payload,
                ensure_ascii=False,
                indent=2,
            ),
            "events": [
                WorkflowEvent(
                    event_type="review.completed",
                    message="Rule-based Reviewer accepted all dynamic task results.",
                    data={"terminal_task_ids": terminal_ids},
                )
            ],
        }

    problems = [
        f"Task {task.task_id} ended with status {task.status}."
        for task in incomplete
    ]
    problems.extend(state.get("errors", []))
    decision = ReviewerDecision(
        action="fail",
        score=0.0,
        problems=problems or ["The workflow did not complete."],
        suggestions=[],
    )
    return {
        "reviewer_decision": decision,
        "workflow_status": "failed",
        "final_output": None,
        "events": [
            WorkflowEvent(
                event_type="review.completed",
                message="Rule-based Reviewer rejected the dynamic workflow.",
                data={
                    "incomplete_task_ids": [task.task_id for task in incomplete]
                },
            )
        ],
    }
