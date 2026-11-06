"""Deterministic safety gate between Planner output and task execution."""

from __future__ import annotations

import copy
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts.plan import ExecutionPlan
from app.planning.cycle_detector import find_cycle
from app.planning.dependency_graph import (
    build_dependency_graph,
    find_missing_dependencies,
    find_ready_task_ids,
)
from app.policies.capability_registry import CAPABILITY_REGISTRY, allowed_tools_for
from app.policies.tool_policy import HIGH_RISK_TOOL_NAMES
from app.tools.registry import TOOL_REGISTRY


class PlanValidationIssue(BaseModel):
    """One actionable reason why a plan cannot be executed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    location: str = "plan"


class PlanValidationReport(BaseModel):
    """Result of schema, registry, dependency, and policy validation."""

    model_config = ConfigDict(extra="forbid")

    plan: ExecutionPlan | None = None
    issues: list[PlanValidationIssue] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.plan is not None and not self.issues


class PlanGuardOutcome(BaseModel):
    """Routing decision made after a Plan Guard attempt."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "revise", "degrade", "terminate"]
    plan: ExecutionPlan | None = None
    issues: list[PlanValidationIssue] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)

    @property
    def executable(self) -> bool:
        return self.action in {"accept", "degrade"} and self.plan is not None


class PlanGuard:
    """Validate untrusted Planner output before the Supervisor can execute it."""

    _SAFE_DEGRADE_CODES = frozenset(
        {
            "high_risk_tool_requested",
            "unknown_tool",
            "tool_not_allowed",
            "delegation_not_allowed",
            "timeout_exceeded",
            "step_limit_exceeded",
            "token_budget_exceeded",
        }
    )

    def validate(self, payload: ExecutionPlan | dict[str, Any] | str) -> PlanValidationReport:
        """Return every validation problem that can be determined safely."""

        raw_payload, decoding_issue = self._normalize_payload(payload)
        if decoding_issue is not None:
            return PlanValidationReport(issues=[decoding_issue])

        issues = self._preflight_permission_issues(raw_payload)
        try:
            plan = ExecutionPlan.model_validate(raw_payload)
        except ValidationError as exc:
            issues.extend(self._convert_schema_errors(exc))
            return PlanValidationReport(issues=self._deduplicate_issues(issues))

        issues.extend(self._validate_plan_relationships(plan))
        issues = self._deduplicate_issues(issues)
        if issues:
            return PlanValidationReport(issues=issues)
        return PlanValidationReport(plan=plan)

    def evaluate(
        self,
        payload: ExecutionPlan | dict[str, Any] | str,
        *,
        invalid_attempt: int = 1,
    ) -> PlanGuardOutcome:
        """Choose accept, revision, safe degradation, or termination."""

        if invalid_attempt < 1:
            raise ValueError("invalid_attempt must be at least 1")

        report = self.validate(payload)
        if report.valid:
            return PlanGuardOutcome(action="accept", plan=report.plan)

        feedback = [f"{issue.location}: {issue.message}" for issue in report.issues]
        if invalid_attempt == 1:
            return PlanGuardOutcome(
                action="revise",
                issues=report.issues,
                feedback=feedback,
            )

        issue_codes = {issue.code for issue in report.issues}
        if issue_codes and issue_codes <= self._SAFE_DEGRADE_CODES:
            degraded_payload = self._build_safe_degraded_payload(payload)
            if degraded_payload is not None:
                degraded_report = self.validate(degraded_payload)
                if degraded_report.valid:
                    return PlanGuardOutcome(
                        action="degrade",
                        plan=degraded_report.plan,
                        issues=report.issues,
                        feedback=[
                            *feedback,
                            "Plan Guard removed unsafe tools, disabled delegation, "
                            "and clamped worker resource limits.",
                        ],
                    )

        return PlanGuardOutcome(
            action="terminate",
            issues=report.issues,
            feedback=[
                *feedback,
                "The plan cannot be repaired safely by deterministic rules.",
            ],
        )

    @staticmethod
    def _normalize_payload(
        payload: ExecutionPlan | dict[str, Any] | str,
    ) -> tuple[dict[str, Any], PlanValidationIssue | None]:
        if isinstance(payload, ExecutionPlan):
            return payload.model_dump(mode="python"), None
        if isinstance(payload, dict):
            return copy.deepcopy(payload), None
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as exc:
            return {}, PlanValidationIssue(
                code="invalid_json",
                message=f"Planner output is not valid JSON: {exc}",
                location="plan",
            )
        if not isinstance(decoded, dict):
            return {}, PlanValidationIssue(
                code="invalid_plan_type",
                message="Planner output must be a JSON object.",
                location="plan",
            )
        return decoded, None

    @staticmethod
    def _preflight_permission_issues(raw_payload: dict[str, Any]) -> list[PlanValidationIssue]:
        issues: list[PlanValidationIssue] = []
        agents = raw_payload.get("agents", [])
        if not isinstance(agents, list):
            return issues
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict):
                continue
            requested_tools = agent.get("requested_tools", [])
            if not isinstance(requested_tools, list):
                continue
            for tool in sorted(set(requested_tools) & HIGH_RISK_TOOL_NAMES):
                issues.append(
                    PlanValidationIssue(
                        code="high_risk_tool_requested",
                        message=f"Planner requested prohibited high-risk tool: {tool}",
                        location=f"agents.{index}.requested_tools",
                    )
                )
        return issues

    @staticmethod
    def _convert_schema_errors(error: ValidationError) -> list[PlanValidationIssue]:
        return [
            PlanValidationIssue(
                code=PlanGuard._schema_error_code(item),
                message=item["msg"],
                location=".".join(str(part) for part in item["loc"]) or "plan",
            )
            for item in error.errors()
        ]

    @staticmethod
    def _schema_error_code(error: dict[str, Any]) -> str:
        location = tuple(error["loc"])
        message = error["msg"]
        last = location[-1] if location else ""

        if "Duplicate task_id" in message:
            return "duplicate_task_id"
        if "Duplicate agent_id" in message:
            return "duplicate_agent_id"
        if "cannot depend on itself" in message:
            return "self_dependency"
        if last == "required_capabilities":
            return "unknown_capability"
        if last == "requested_tools":
            if "not allowed" in message:
                return "tool_not_allowed"
            return "unknown_tool"
        if last == "output_contract":
            return "unknown_output_contract"
        if last == "can_delegate":
            return "delegation_not_allowed"
        if last == "timeout_seconds":
            return "timeout_exceeded"
        if last == "max_steps":
            return "step_limit_exceeded"
        if last == "token_budget":
            return "token_budget_exceeded"
        if last == "agents" and error["type"] == "too_long":
            return "too_many_workers"
        return "schema_validation_error"

    @staticmethod
    def _validate_plan_relationships(plan: ExecutionPlan) -> list[PlanValidationIssue]:
        issues: list[PlanValidationIssue] = []
        graph = build_dependency_graph(plan.tasks)

        for task_id, missing in find_missing_dependencies(graph).items():
            issues.append(
                PlanValidationIssue(
                    code="missing_dependency",
                    message=(
                        f"Task {task_id} references missing dependencies: "
                        + ", ".join(missing)
                    ),
                    location=f"tasks.{task_id}.dependencies",
                )
            )

        cycle = find_cycle(graph)
        if cycle is not None:
            issues.append(
                PlanValidationIssue(
                    code="dependency_cycle",
                    message="Dependency cycle detected: " + " -> ".join(cycle),
                    location="tasks",
                )
            )

        if not find_ready_task_ids(plan.tasks):
            issues.append(
                PlanValidationIssue(
                    code="no_ready_task",
                    message="The plan has no task that can run immediately.",
                    location="tasks",
                )
            )

        agents_by_id = {agent.agent_id: agent for agent in plan.agents}
        tasks_by_id = {task.task_id: task for task in plan.tasks}
        agents_by_task: dict[str, list[str]] = {}
        for agent in plan.agents:
            agents_by_task.setdefault(agent.task_id, []).append(agent.agent_id)
            if agent.task_id not in tasks_by_id:
                issues.append(
                    PlanValidationIssue(
                        code="orphan_agent",
                        message=(
                            f"Agent {agent.agent_id} references missing task "
                            f"{agent.task_id}."
                        ),
                        location=f"agents.{agent.agent_id}.task_id",
                    )
                )

        for task in plan.tasks:
            assigned_agent = agents_by_id.get(task.assigned_agent_id)
            if assigned_agent is None:
                issues.append(
                    PlanValidationIssue(
                        code="missing_assigned_agent",
                        message=(
                            f"Task {task.task_id} references missing agent "
                            f"{task.assigned_agent_id}."
                        ),
                        location=f"tasks.{task.task_id}.assigned_agent_id",
                    )
                )
            elif assigned_agent.task_id != task.task_id:
                issues.append(
                    PlanValidationIssue(
                        code="agent_task_mismatch",
                        message=(
                            f"Agent {assigned_agent.agent_id} is assigned to task "
                            f"{assigned_agent.task_id}, not {task.task_id}."
                        ),
                        location=f"tasks.{task.task_id}.assigned_agent_id",
                    )
                )
            elif assigned_agent.output_contract != task.output_contract:
                issues.append(
                    PlanValidationIssue(
                        code="output_contract_mismatch",
                        message=(
                            f"Task {task.task_id} and agent "
                            f"{assigned_agent.agent_id} must use the same "
                            "output contract."
                        ),
                        location=f"tasks.{task.task_id}.output_contract",
                    )
                )

            matching_agents = agents_by_task.get(task.task_id, [])
            if len(matching_agents) == 0:
                issues.append(
                    PlanValidationIssue(
                        code="task_without_agent",
                        message=f"Task {task.task_id} has no AgentSpec.",
                        location=f"tasks.{task.task_id}",
                    )
                )
            elif len(matching_agents) > 1:
                issues.append(
                    PlanValidationIssue(
                        code="multiple_agents_for_task",
                        message=(
                            f"Task {task.task_id} has multiple AgentSpecs: "
                            + ", ".join(sorted(matching_agents))
                        ),
                        location=f"tasks.{task.task_id}",
                    )
                )

        if len(plan.agents) > plan.budget.max_workers:
            issues.append(
                PlanValidationIssue(
                    code="worker_count_exceeded",
                    message=(
                        f"Plan requests {len(plan.agents)} workers, but the "
                        f"budget allows {plan.budget.max_workers}."
                    ),
                    location="agents",
                )
            )
        return issues

    @staticmethod
    def _deduplicate_issues(
        issues: list[PlanValidationIssue],
    ) -> list[PlanValidationIssue]:
        seen: set[tuple[str, str, str]] = set()
        result: list[PlanValidationIssue] = []
        for issue in issues:
            key = (issue.code, issue.message, issue.location)
            if key not in seen:
                seen.add(key)
                result.append(issue)
        return result

    def _build_safe_degraded_payload(
        self,
        payload: ExecutionPlan | dict[str, Any] | str,
    ) -> dict[str, Any] | None:
        raw_payload, issue = self._normalize_payload(payload)
        if issue is not None:
            return None

        agents = raw_payload.get("agents")
        if not isinstance(agents, list):
            return None
        for agent in agents:
            if not isinstance(agent, dict):
                return None
            capabilities = agent.get("required_capabilities", [])
            if (
                not isinstance(capabilities, list)
                or not set(capabilities) <= set(CAPABILITY_REGISTRY)
            ):
                return None
            allowed_tools = allowed_tools_for(capabilities)
            requested_tools = agent.get("requested_tools", [])
            if not isinstance(requested_tools, list):
                return None
            agent["requested_tools"] = [
                tool
                for tool in requested_tools
                if tool in TOOL_REGISTRY and tool in allowed_tools
            ]
            agent["can_delegate"] = False
            self._clamp_integer(agent, "timeout_seconds", minimum=1, maximum=120)
            self._clamp_integer(agent, "max_steps", minimum=1, maximum=8)
            self._clamp_integer(agent, "token_budget", minimum=1, maximum=8192)
        return raw_payload

    @staticmethod
    def _clamp_integer(
        container: dict[str, Any],
        key: str,
        *,
        minimum: int,
        maximum: int,
    ) -> None:
        value = container.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            container[key] = min(max(value, minimum), maximum)
