import copy
import json

import pytest

from app.agents.planner import (
    PlanGenerationError,
    PlannerAgent,
    build_planner_messages,
)
from app.contracts.plan import ExecutionPlan
from app.contracts.planner import PlannerInput, PlannerLimits
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.policies.capability_registry import CAPABILITY_REGISTRY
from app.contracts.output_registry import OUTPUT_CONTRACTS
from app.observability import LLMTraceRecorder


class SequentialStructuredModel:
    def __init__(self, outputs: list[ExecutionPlan | dict]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list] = []

    def invoke(self, messages: list) -> ExecutionPlan | dict:
        self.calls.append(list(messages))
        return self.outputs.pop(0)


def planner_input(
    goal: str = "完成技术调研",
    *,
    capabilities: list[str] | None = None,
    output_contracts: list[str] | None = None,
    limits: PlannerLimits | None = None,
) -> PlannerInput:
    return PlannerInput(
        user_goal=goal,
        available_capabilities=capabilities or sorted(CAPABILITY_REGISTRY),
        available_output_contracts=output_contracts or sorted(OUTPUT_CONTRACTS),
        limits=limits or PlannerLimits(),
        recalled_memory=[],
    )


def one_task_plan(
    *,
    goal: str,
    capability: str,
    requested_tools: list[str],
    output_contract: str,
) -> ExecutionPlan:
    return ExecutionPlan(
        goal_summary=goal,
        assumptions=["使用用户提供的目标和系统注册表"],
        tasks=[
            TaskSpec(
                task_id="task_1",
                title=goal,
                description=f"完成任务：{goal}",
                task_type=capability,
                dependencies=[],
                assigned_agent_id="worker_1",
                output_contract=output_contract,
            )
        ],
        agents=[
            SubAgentSpec(
                agent_id="worker_1",
                task_id="task_1",
                role=f"{goal} Worker",
                objective=goal,
                instructions=["遵守输出协议并完成当前任务"],
                required_capabilities=[capability],
                requested_tools=requested_tools,
                knowledge_scope=[goal],
                output_contract=output_contract,
            )
        ],
        expected_deliverable=goal,
    )


@pytest.mark.parametrize(
    (
        "goal",
        "capability",
        "requested_tools",
        "output_contract",
    ),
    [
        (
            "技术调研",
            "technical_analysis",
            ["python_sandbox"],
            "research_result_v1",
        ),
        ("论文调研", "web_research", ["web_search"], "research_result_v1"),
        (
            "实验设计",
            "experiment_design",
            ["python_sandbox"],
            "experiment_plan_v1",
        ),
        (
            "数据分析",
            "data_analysis",
            ["dataset_reader", "python_sandbox"],
            "research_result_v1",
        ),
        ("技术报告生成", "report_writing", [], "report_result_v1"),
    ],
)
def test_planner_supports_five_acceptance_task_types(
    goal: str,
    capability: str,
    requested_tools: list[str],
    output_contract: str,
) -> None:
    expected = one_task_plan(
        goal=goal,
        capability=capability,
        requested_tools=requested_tools,
        output_contract=output_contract,
    )
    model = SequentialStructuredModel([expected])

    plan = PlannerAgent(model).plan(planner_input(goal))

    assert plan.goal_summary == goal
    assert plan.agents[0].required_capabilities == [capability]
    assert plan.agents[0].requested_tools == requested_tools
    assert plan.tasks[0].output_contract == output_contract
    assert len(model.calls) == 1


def test_planner_prompt_contains_security_boundaries() -> None:
    messages = build_planner_messages(planner_input())
    system_prompt = str(messages[0].content)

    assert "Do not execute any task" in system_prompt
    assert "Do not call tools" in system_prompt
    assert "can_delegate to false" in system_prompt
    assert "never increase or replace" in system_prompt
    assert "untrusted context" in system_prompt


def test_planner_retries_after_plan_guard_dependency_feedback() -> None:
    valid = one_task_plan(
        goal="技术调研",
        capability="technical_analysis",
        requested_tools=["python_sandbox"],
        output_contract="research_result_v1",
    )
    invalid = valid.model_dump(mode="python")
    second_task = copy.deepcopy(invalid["tasks"][0])
    second_task.update(
        {
            "task_id": "task_2",
            "assigned_agent_id": "worker_2",
            "dependencies": ["task_1"],
        }
    )
    invalid["tasks"][0]["dependencies"] = ["task_2"]
    invalid["tasks"].append(second_task)
    second_agent = copy.deepcopy(invalid["agents"][0])
    second_agent.update({"agent_id": "worker_2", "task_id": "task_2"})
    invalid["agents"].append(second_agent)
    model = SequentialStructuredModel([invalid, valid])

    plan = PlannerAgent(model).plan(planner_input())

    assert plan.goal_summary == "技术调研"
    assert len(model.calls) == 2
    revision_message = str(model.calls[1][-1].content)
    assert "Dependency cycle detected" in revision_message


def test_second_unsafe_tool_plan_is_safely_degraded() -> None:
    valid = one_task_plan(
        goal="论文调研",
        capability="web_research",
        requested_tools=["web_search"],
        output_contract="research_result_v1",
    )
    unsafe = valid.model_dump(mode="python")
    unsafe["agents"][0]["requested_tools"] = ["web_search", "file_writer"]
    model = SequentialStructuredModel([copy.deepcopy(unsafe), copy.deepcopy(unsafe)])

    plan = PlannerAgent(model).plan(planner_input("论文调研"))

    assert plan.agents[0].requested_tools == ["web_search"]
    assert len(model.calls) == 2


def test_second_structurally_invalid_plan_raises() -> None:
    valid = one_task_plan(
        goal="技术调研",
        capability="technical_analysis",
        requested_tools=[],
        output_contract="research_result_v1",
    )
    cyclic = valid.model_dump(mode="python")
    cyclic["tasks"][0]["dependencies"] = ["task_2"]
    second_task = copy.deepcopy(cyclic["tasks"][0])
    second_task.update(
        {
            "task_id": "task_2",
            "assigned_agent_id": "worker_2",
            "dependencies": ["task_1"],
        }
    )
    cyclic["tasks"].append(second_task)
    second_agent = copy.deepcopy(cyclic["agents"][0])
    second_agent.update({"agent_id": "worker_2", "task_id": "task_2"})
    cyclic["agents"].append(second_agent)
    model = SequentialStructuredModel([copy.deepcopy(cyclic), copy.deepcopy(cyclic)])

    with pytest.raises(PlanGenerationError, match="cannot be repaired safely"):
        PlannerAgent(model).plan(planner_input())


def test_planner_cannot_override_system_budget() -> None:
    generated = one_task_plan(
        goal="技术报告生成",
        capability="report_writing",
        requested_tools=[],
        output_contract="report_result_v1",
    )
    generated.budget = generated.budget.model_copy(
        update={"max_workers": 1, "max_concurrency": 1}
    )
    limits = PlannerLimits(max_workers=5, max_concurrency=2)

    plan = PlannerAgent(SequentialStructuredModel([generated])).plan(
        planner_input(limits=limits)
    )

    assert plan.budget.max_workers == 5
    assert plan.budget.max_concurrency == 2


def test_planner_rejects_capability_outside_input_scope() -> None:
    generated = one_task_plan(
        goal="技术调研",
        capability="technical_analysis",
        requested_tools=[],
        output_contract="research_result_v1",
    )
    model = SequentialStructuredModel([generated, generated])
    restricted_input = planner_input(
        capabilities=["web_research"],
        output_contracts=["research_result_v1"],
    )

    with pytest.raises(PlanGenerationError, match="not supplied in PlannerInput"):
        PlannerAgent(model).plan(restricted_input)


def test_planner_records_prompt_and_structured_response(tmp_path) -> None:
    generated = one_task_plan(
        goal="技术调研",
        capability="technical_analysis",
        requested_tools=[],
        output_contract="research_result_v1",
    )
    recorder = LLMTraceRecorder(tmp_path / "trace", run_id="planner-run")

    plan = PlannerAgent(
        SequentialStructuredModel([generated]),
        trace_recorder=recorder,
        model_name="test-model",
        max_output_tokens=1024,
    ).plan(planner_input("技术调研"))

    events = [
        json.loads(line)
        for line in recorder.calls_path.read_text(encoding="utf-8").splitlines()
    ]
    assert plan.goal_summary == "技术调研"
    assert [event["event"] for event in events] == [
        "llm.request",
        "llm.response",
    ]
    assert events[0]["call_id"] == events[1]["call_id"]
    assert events[0]["messages"][1]["role"] == "human"
    assert events[0]["model"] == "test-model"
    assert events[1]["response"]["goal_summary"] == "技术调研"
