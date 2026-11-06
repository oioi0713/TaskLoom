"""Real structured Planner plus the deterministic stage-three mock Planner."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.contracts.budget import ExecutionBudget
from app.contracts.memory import MemoryItem
from app.contracts.plan import ExecutionPlan
from app.contracts.output_registry import OUTPUT_CONTRACTS
from app.contracts.planner import PlannerInput, PlannerLimits
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.state import Stage3State, WorkflowEvent
from app.llm.factory import create_chat_model
from app.observability import LLMTraceRecorder
from app.planning.validator import PlanGuard
from app.policies.capability_registry import CAPABILITY_REGISTRY, CAPABILITY_TOOL_MAP
from app.tools.registry import TOOL_REGISTRY


class PlanGenerationError(RuntimeError):
    """Raised when the Planner cannot produce a safe plan in two attempts."""

    def __init__(self, feedback: list[str]) -> None:
        self.feedback = feedback
        super().__init__("Planner failed to produce an executable plan: " + " | ".join(feedback))


@lru_cache(maxsize=1)
def load_planner_system_prompt() -> str:
    """Load the fixed Planner security and scope rules."""

    prompt_path = Path(__file__).with_name("prompts") / "planner.md"
    return prompt_path.read_text(encoding="utf-8")


def build_planner_messages(planner_input: PlannerInput) -> list[BaseMessage]:
    """Build messages containing only bounded registries, limits, and context."""

    available_capabilities = set(planner_input.available_capabilities)
    capability_tools = {
        capability: sorted(CAPABILITY_TOOL_MAP[capability])
        for capability in planner_input.available_capabilities
    }
    available_tools = sorted(
        {
            tool
            for capability in available_capabilities
            for tool in CAPABILITY_TOOL_MAP[capability]
            if tool in TOOL_REGISTRY
        }
    )
    context = {
        "user_goal": planner_input.user_goal,
        "available_capabilities": planner_input.available_capabilities,
        "capability_tool_map": capability_tools,
        "available_tools": available_tools,
        "available_output_contracts": planner_input.available_output_contracts,
        "limits": planner_input.limits.model_dump(mode="json"),
        "recalled_memory": [
            item.model_dump(mode="json") for item in planner_input.recalled_memory
        ],
    }
    return [
        SystemMessage(content=load_planner_system_prompt()),
        HumanMessage(
            content=(
                "Create an ExecutionPlan from this bounded Planner input:\n"
                + json.dumps(context, ensure_ascii=False, indent=2)
            )
        ),
    ]


class PlannerAgent:
    """Generate a structured plan, then force it through the Plan Guard."""

    def __init__(
        self,
        structured_model: Any,
        *,
        guard: PlanGuard | None = None,
        trace_recorder: LLMTraceRecorder | None = None,
        model_name: str | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self._structured_model = structured_model
        self._guard = guard or PlanGuard()
        self._trace_recorder = trace_recorder
        self._model_name = model_name
        self._max_output_tokens = max_output_tokens

    @classmethod
    def from_chat_model(
        cls,
        chat_model: Any,
        *,
        structured_output_method: str = "function_calling",
        guard: PlanGuard | None = None,
        trace_recorder: LLMTraceRecorder | None = None,
        model_name: str | None = None,
        max_output_tokens: int | None = None,
    ) -> "PlannerAgent":
        """Bind a chat model to the ExecutionPlan Pydantic response schema."""

        structured_model = chat_model.with_structured_output(
            ExecutionPlan,
            method=structured_output_method,
            include_raw=True,
        )
        return cls(
            structured_model,
            guard=guard,
            trace_recorder=trace_recorder,
            model_name=model_name,
            max_output_tokens=max_output_tokens,
        )

    def plan(self, planner_input: PlannerInput) -> ExecutionPlan:
        """Generate at most two plans and return only a guarded executable plan."""

        messages = build_planner_messages(planner_input)
        last_feedback: list[str] = []
        for attempt in (1, 2):
            call_id = (
                self._trace_recorder.record_request(
                    component="planner",
                    messages=list(messages),
                    attempt=attempt,
                    model=self._model_name,
                    max_tokens=self._max_output_tokens,
                )
                if self._trace_recorder is not None
                else None
            )
            try:
                raw_plan = self._structured_model.invoke(messages)
            except (ValidationError, OutputParserException, ValueError) as exc:
                if self._trace_recorder is not None and call_id is not None:
                    self._trace_recorder.record_error(call_id, exc)
                last_feedback = [f"Structured output validation failed: {exc}"]
                if attempt == 2:
                    raise PlanGenerationError(last_feedback) from exc
                messages.append(
                    HumanMessage(
                        content=(
                            "Your previous response did not match the ExecutionPlan "
                            "schema. Correct it and return a complete structured plan. "
                            + last_feedback[0]
                        )
                    )
                )
                continue
            except Exception as exc:
                if self._trace_recorder is not None and call_id is not None:
                    self._trace_recorder.record_error(call_id, exc)
                raise

            if self._trace_recorder is not None and call_id is not None:
                self._trace_recorder.record_response(call_id, raw_plan)

            try:
                payload = self._normalize_model_output(raw_plan)
            except (
                ValidationError,
                OutputParserException,
                TypeError,
                ValueError,
            ) as exc:
                if self._trace_recorder is not None and call_id is not None:
                    self._trace_recorder.record_validation_error(call_id, exc)
                last_feedback = [f"Structured output validation failed: {exc}"]
                if attempt == 2:
                    raise PlanGenerationError(last_feedback) from exc
                messages.append(
                    HumanMessage(
                        content=(
                            "Your previous response did not match the ExecutionPlan "
                            "schema. Correct it and return a complete structured plan. "
                            + last_feedback[0]
                        )
                    )
                )
                continue
            payload["budget"] = self._system_budget(planner_input).model_dump(
                mode="python"
            )
            outcome = self._guard.evaluate(payload, invalid_attempt=attempt)
            scope_feedback = self._validate_input_scope(
                outcome.plan if outcome.plan is not None else payload,
                planner_input,
            )
            if outcome.executable and outcome.plan is not None and not scope_feedback:
                return outcome.plan

            last_feedback = [*outcome.feedback, *scope_feedback]
            if attempt == 2:
                raise PlanGenerationError(
                    last_feedback
                    or ["The second Planner response was not executable."]
                )

            messages.extend(
                [
                    AIMessage(
                        content=json.dumps(payload, ensure_ascii=False, default=str)
                    ),
                    HumanMessage(
                        content=(
                            "Plan Guard rejected the plan. Correct every issue and "
                            "return a complete replacement ExecutionPlan:\n- "
                            + "\n- ".join(last_feedback)
                        )
                    ),
                ]
            )

        raise PlanGenerationError(last_feedback or ["Planner attempts exhausted."])

    @staticmethod
    def _normalize_model_output(raw_plan: Any) -> dict[str, Any]:
        if (
            isinstance(raw_plan, dict)
            and {"raw", "parsed", "parsing_error"}.issubset(raw_plan)
        ):
            parsing_error = raw_plan["parsing_error"]
            if parsing_error is not None:
                if isinstance(parsing_error, BaseException):
                    raise parsing_error
                raise ValueError(str(parsing_error))
            raw_plan = raw_plan["parsed"]
        if isinstance(raw_plan, ExecutionPlan):
            return raw_plan.model_dump(mode="python")
        if isinstance(raw_plan, dict):
            return dict(raw_plan)
        raise ValueError(
            "Structured Planner model must return ExecutionPlan or a mapping."
        )

    @staticmethod
    def _system_budget(planner_input: PlannerInput) -> ExecutionBudget:
        limits = planner_input.limits
        return ExecutionBudget(
            max_workers=limits.max_workers,
            max_concurrency=limits.max_concurrency,
            max_worker_timeout_seconds=limits.max_timeout_seconds,
            max_worker_token_budget=limits.max_tokens_per_worker,
            total_token_budget=limits.max_workers * limits.max_tokens_per_worker,
        )

    @staticmethod
    def _validate_input_scope(
        plan_or_payload: ExecutionPlan | dict[str, Any],
        planner_input: PlannerInput,
    ) -> list[str]:
        try:
            plan = (
                plan_or_payload
                if isinstance(plan_or_payload, ExecutionPlan)
                else ExecutionPlan.model_validate(plan_or_payload)
            )
        except ValidationError:
            return []

        feedback: list[str] = []
        allowed_capabilities = set(planner_input.available_capabilities)
        allowed_contracts = set(planner_input.available_output_contracts)
        used_capabilities = {
            capability
            for agent in plan.agents
            for capability in agent.required_capabilities
        }
        unavailable_capabilities = sorted(
            used_capabilities - allowed_capabilities
        )
        if unavailable_capabilities:
            feedback.append(
                "Plan used capabilities not supplied in PlannerInput: "
                + ", ".join(unavailable_capabilities)
            )

        used_contracts = {
            *[task.output_contract for task in plan.tasks],
            *[agent.output_contract for agent in plan.agents],
        }
        unavailable_contracts = sorted(used_contracts - allowed_contracts)
        if unavailable_contracts:
            feedback.append(
                "Plan used output contracts not supplied in PlannerInput: "
                + ", ".join(unavailable_contracts)
            )
        if len(plan.tasks) > planner_input.limits.max_tasks:
            feedback.append(
                f"Plan created {len(plan.tasks)} tasks, but PlannerInput allows "
                f"{planner_input.limits.max_tasks}."
            )
        return feedback


def create_planner_agent(
    settings: Settings | None = None,
    *,
    trace_recorder: LLMTraceRecorder | None = None,
) -> PlannerAgent:
    """Create the production Planner without making an API request."""

    resolved = settings or get_settings()
    chat_model = create_chat_model(resolved)
    return PlannerAgent.from_chat_model(
        chat_model,
        structured_output_method=resolved.llm_structured_output_method,
        trace_recorder=trace_recorder,
        model_name=resolved.llm_model,
        max_output_tokens=resolved.llm_planner_max_output_tokens,
    )


def create_default_planner_input(
    user_goal: str,
    *,
    limits: PlannerLimits | None = None,
    recalled_memory: list[MemoryItem] | None = None,
) -> PlannerInput:
    """Build Planner input from the complete enabled stage-four registries."""

    return PlannerInput(
        user_goal=user_goal,
        available_capabilities=sorted(CAPABILITY_REGISTRY),
        available_output_contracts=sorted(OUTPUT_CONTRACTS),
        limits=limits or PlannerLimits(),
        recalled_memory=recalled_memory or [],
    )


def build_mock_execution_plan() -> ExecutionPlan:
    """Return the fixed five-task memory-backend comparison plan."""

    tasks = [
        TaskSpec(
            task_id="T1",
            title="调研 Redis Memory",
            description="总结 Redis 作为 Agent Memory 后端的特点",
            task_type="research",
            priority=10,
            assigned_agent_id="redis_researcher",
            output_contract="research_result_v1",
        ),
        TaskSpec(
            task_id="T2",
            title="调研向量记忆",
            description="总结向量数据库作为 Agent Memory 后端的特点",
            task_type="research",
            priority=10,
            assigned_agent_id="vector_researcher",
            output_contract="research_result_v1",
        ),
        TaskSpec(
            task_id="T3",
            title="调研文件式记忆",
            description="总结文件系统作为 Agent Memory 后端的特点",
            task_type="research",
            priority=10,
            assigned_agent_id="file_researcher",
            output_contract="research_result_v1",
        ),
        TaskSpec(
            task_id="T4",
            title="比较三种记忆方案",
            description="比较 Redis、向量数据库和文件系统方案",
            task_type="comparison",
            dependencies=["T1", "T2", "T3"],
            priority=5,
            assigned_agent_id="memory_comparator",
            output_contract="comparison_result_v1",
        ),
        TaskSpec(
            task_id="T5",
            title="生成记忆方案报告",
            description="根据比较结果生成结构化报告",
            task_type="report",
            dependencies=["T4"],
            priority=1,
            assigned_agent_id="report_writer",
            output_contract="report_result_v1",
        ),
    ]
    agents = [
        SubAgentSpec(
            agent_id="redis_researcher",
            task_id="T1",
            role="Redis Memory 调研员",
            objective="分析 Redis 记忆方案",
            instructions=["使用固定模拟资料输出结构化调研结果"],
            required_capabilities=["technical_analysis"],
            requested_tools=[],
            knowledge_scope=["Redis", "Agent Memory"],
            output_contract="research_result_v1",
        ),
        SubAgentSpec(
            agent_id="vector_researcher",
            task_id="T2",
            role="向量记忆调研员",
            objective="分析向量数据库记忆方案",
            instructions=["使用固定模拟资料输出结构化调研结果"],
            required_capabilities=["technical_analysis"],
            requested_tools=[],
            knowledge_scope=["Vector Database", "Agent Memory"],
            output_contract="research_result_v1",
        ),
        SubAgentSpec(
            agent_id="file_researcher",
            task_id="T3",
            role="文件式记忆调研员",
            objective="分析文件系统记忆方案",
            instructions=["使用固定模拟资料输出结构化调研结果"],
            required_capabilities=["technical_analysis"],
            requested_tools=[],
            knowledge_scope=["File Storage", "Agent Memory"],
            output_contract="research_result_v1",
        ),
        SubAgentSpec(
            agent_id="memory_comparator",
            task_id="T4",
            role="记忆方案比较员",
            objective="比较三种记忆后端",
            instructions=["只使用 T1、T2、T3 的结构化结果"],
            required_capabilities=["comparison"],
            requested_tools=[],
            knowledge_scope=["T1", "T2", "T3"],
            output_contract="comparison_result_v1",
        ),
        SubAgentSpec(
            agent_id="report_writer",
            task_id="T5",
            role="记忆方案报告撰写员",
            objective="生成最终结构化报告",
            instructions=["只使用 T4 的结构化比较结果"],
            required_capabilities=["report_writing"],
            requested_tools=[],
            knowledge_scope=["T4"],
            output_contract="report_result_v1",
        ),
    ]
    return ExecutionPlan(
        goal_summary="比较三种 Agent Memory 后端并生成报告",
        assumptions=["阶段 3 只运行固定模拟数据，不调用真实 LLM 或工具"],
        tasks=tasks,
        agents=agents,
        expected_deliverable="一份三种 Agent Memory 后端的结构化比较报告",
        budget=ExecutionBudget(max_workers=5, max_concurrency=3),
    )


def mock_planner_node(state: Stage3State) -> dict:
    """Create the fixed plan without calling an LLM."""

    plan = build_mock_execution_plan()
    return {
        "execution_plan": plan,
        "plan_version": state.get("plan_version", 0) + 1,
        "workflow_status": "running",
        "events": [
            WorkflowEvent(
                event_type="plan.created",
                message="Mock Planner created the fixed five-task plan.",
                data={"task_count": len(plan.tasks)},
            )
        ],
    }
