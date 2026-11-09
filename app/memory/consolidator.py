"""Extract bounded reusable Session Memory from one workflow state."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.contracts.memory import MemoryItem
from app.contracts.review import ReviewerDecision
from app.graph.state import Stage6State


def _memory_id(kind: str, content: str, scope: list[str]) -> str:
    digest = hashlib.sha256(
        json.dumps([kind, content, sorted(scope)], ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    return f"mem-{digest}"


def _bounded_text(value: Any, *, limit: int = 1500) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _result_summary(output: dict[str, Any] | None) -> str | None:
    if not output:
        return None
    selected: list[str] = []
    for key in ("title", "summary", "executive_summary", "recommendation"):
        value = output.get(key)
        if value:
            selected.append(f"{key}: {_bounded_text(value, limit=900)}")
    findings = output.get("findings")
    if isinstance(findings, list) and findings:
        selected.append("findings: " + _bounded_text(findings[:5], limit=900))
    return _bounded_text(" | ".join(selected)) if selected else None


def consolidate_state_memory(
    state: Stage6State,
    decision: ReviewerDecision | None = None,
    *,
    max_items: int = 50,
) -> list[MemoryItem]:
    """Merge stable result summaries and Reviewer guidance into semantic memory."""

    merged = {item.memory_id: item for item in state.get("memory_context", [])}
    run_id = state.get("run_id")
    plan_version = state.get("plan_version") or None
    goal_content = "会话目标: " + _bounded_text(state.get("original_goal", ""), limit=900)
    if goal_content != "会话目标: ":
        goal_item = MemoryItem(
            memory_id=_memory_id("decision", goal_content, []),
            content=goal_content,
            kind="decision",
            source_run_id=run_id,
            plan_version=plan_version,
        )
        merged[goal_item.memory_id] = goal_item

    for result in state.get("active_worker_results", []):
        if result.status == "completed":
            content = _result_summary(result.output)
            kind = "fact"
        elif decision is not None and decision.action == "replan" and result.error:
            content = "失败经验: " + _bounded_text(result.error, limit=700)
            kind = "failure"
        else:
            continue
        if not content:
            continue
        spec = next(
            (
                item
                for item in state.get("agent_specs", [])
                if item.task_id == result.task_id
            ),
            None,
        )
        scope = list(spec.knowledge_scope) if spec is not None else []
        item = MemoryItem(
            memory_id=_memory_id(kind, content, scope),
            content=content,
            scope=scope,
            kind=kind,
            source_run_id=run_id,
            source_task_id=result.task_id,
            plan_version=plan_version,
        )
        merged[item.memory_id] = item

    if decision is not None:
        guidance: list[tuple[str, str]] = []
        guidance.extend(("constraint", value) for value in decision.replan_constraints)
        if decision.action in {"replan", "fail"}:
            guidance.extend(("failure", value) for value in decision.problems)
        guidance.extend(("decision", value) for value in decision.suggestions)
        for kind, value in guidance:
            content = _bounded_text(value, limit=700)
            if not content:
                continue
            item = MemoryItem(
                memory_id=_memory_id(kind, content, []),
                content=content,
                kind=kind,  # type: ignore[arg-type]
                source_run_id=run_id,
                plan_version=plan_version,
            )
            merged[item.memory_id] = item

    return list(merged.values())[-max_items:]
