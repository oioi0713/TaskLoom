from __future__ import annotations

from app.agents.supervisor import runtime_supervisor_node
from app.contracts.budget import ExecutionBudget
from app.contracts.memory import MemoryItem
from app.contracts.result import WorkerResult
from app.contracts.subagent import ResolvedSubAgentSpec, SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.builder import create_runtime_initial_state
from app.graph.nodes import aggregate_runtime_node
from app.graph.routes import route_runtime_supervisor


def task(
    task_id: str,
    *,
    priority: int = 0,
    dependencies: list[str] | None = None,
    status: str = "pending",
    retry_count: int = 0,
    max_retries: int = 2,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        title=f"Task {task_id}",
        description=f"Complete {task_id}",
        task_type="research",
        dependencies=dependencies or [],
        priority=priority,
        assigned_agent_id=f"agent_{task_id}",
        output_contract="research_result_v1",
        status=status,
        retry_count=retry_count,
        max_retries=max_retries,
    )


def spec(task_id: str) -> SubAgentSpec:
    return SubAgentSpec(
        agent_id=f"agent_{task_id}",
        task_id=task_id,
        role="Researcher",
        objective=f"Complete {task_id}",
        instructions=["Return structured findings"],
        required_capabilities=["web_research"],
        requested_tools=["web_search", "web_page_reader"],
        knowledge_scope=["LangGraph"],
        output_contract="research_result_v1",
        timeout_seconds=120,
        max_steps=8,
        token_budget=8000,
    )


def completed_result(task_id: str) -> WorkerResult:
    return WorkerResult(
        run_id="run-6",
        task_id=task_id,
        agent_id=f"agent_{task_id}",
        status="completed",
        output_contract="research_result_v1",
        output={
            "summary": f"{task_id} completed",
            "findings": ["verified finding"],
            "sources": [],
        },
        execution_time_ms=10,
        token_usage=100,
    )


def failed_result(task_id: str, status: str = "failed") -> WorkerResult:
    return WorkerResult(
        run_id="run-6",
        task_id=task_id,
        agent_id=f"agent_{task_id}",
        status=status,
        output_contract="research_result_v1",
        output=None,
        error="simulated failure",
        execution_time_ms=10,
        token_usage=20,
    )


def scheduler_state(tasks: list[TaskSpec], specs: list[SubAgentSpec]):
    state = create_runtime_initial_state(
        "test scheduler",
        budget=ExecutionBudget(
            max_workers=8,
            max_concurrency=2,
            max_retries_per_task=1,
            max_total_steps=10,
            max_worker_timeout_seconds=30,
            max_worker_token_budget=1000,
            total_token_budget=1500,
        ),
        user_allowed_tools=["web_search"],
        enabled_tools=["web_search", "web_page_reader"],
    )
    state["tasks"] = tasks
    state["agent_specs"] = specs
    return state


def test_scheduler_applies_priority_concurrency_permissions_and_budgets() -> None:
    tasks = [
        task("low", priority=1),
        task("high", priority=10),
        task("medium", priority=5),
    ]
    state = scheduler_state(tasks, [spec(item.task_id) for item in tasks])

    update = runtime_supervisor_node(state)

    assert update["ready_task_ids"] == ["high", "medium"]
    assert [item.task_id for item in update["resolved_agent_specs"]] == [
        "high",
        "medium",
    ]
    assert [item.granted_tools for item in update["resolved_agent_specs"]] == [
        ["web_search"],
        ["web_search"],
    ]
    assert [item.timeout_seconds for item in update["resolved_agent_specs"]] == [
        30,
        30,
    ]
    assert [item.token_budget for item in update["resolved_agent_specs"]] == [
        1000,
        1000,
    ]
    statuses = {item.task_id: item.status for item in update["tasks"]}
    assert statuses == {"low": "pending", "high": "running", "medium": "running"}


def test_scheduler_does_not_duplicate_claim_running_tasks() -> None:
    running = task("running", status="running")
    pending = task("pending")
    state = scheduler_state([running, pending], [spec("running"), spec("pending")])
    state["budget"] = state["budget"].model_copy(update={"max_concurrency": 1})

    update = runtime_supervisor_node(state)

    assert update["ready_task_ids"] == []
    assert not update.get("errors")
    assert update["events"][0].event_type == "scheduler.waiting"


def test_aggregator_retries_once_then_marks_task_failed() -> None:
    running = task("retry_me", status="running", max_retries=2)
    state = scheduler_state([running], [spec("retry_me")])
    state["worker_results"] = [failed_result("retry_me", "timeout")]

    first = aggregate_runtime_node(state)

    assert first["tasks"][0].status == "pending"
    assert first["tasks"][0].retry_count == 1
    assert first["retry_count"] == 1

    state["tasks"] = [first["tasks"][0].model_copy(update={"status": "running"})]
    state["worker_results"] = [
        failed_result("retry_me", "timeout"),
        failed_result("retry_me", "invalid_output"),
    ]
    state["retry_count"] = first["retry_count"]

    second = aggregate_runtime_node(state)

    assert second["tasks"][0].status == "failed"
    assert second["tasks"][0].retry_count == 1


def test_permission_denied_is_not_retried() -> None:
    running = task("denied", status="running")
    state = scheduler_state([running], [spec("denied")])
    state["worker_results"] = [failed_result("denied", "permission_denied")]

    update = aggregate_runtime_node(state)

    assert update["tasks"][0].status == "failed"
    assert update["retry_count"] == 0


def test_failed_dependency_blocks_downstream_task() -> None:
    failed = task("source", status="failed")
    downstream = task("downstream", dependencies=["source"])
    state = scheduler_state(
        [failed, downstream],
        [spec("source"), spec("downstream")],
    )

    update = runtime_supervisor_node(state)

    assert {item.task_id: item.status for item in update["tasks"]} == {
        "source": "failed",
        "downstream": "blocked",
    }
    assert update["ready_task_ids"] == []
    assert update["events"][0].event_type == "task.blocked"


def test_step_budget_stops_scheduling() -> None:
    state = scheduler_state([task("one")], [spec("one")])
    state["total_steps"] = state["budget"].max_total_steps

    update = runtime_supervisor_node(state)

    assert update["workflow_status"] == "failed"
    assert update["events"][0].event_type == "run.budget_exhausted"


def test_runtime_route_sends_only_dependencies_and_relevant_memory() -> None:
    current_task = task(
        "current",
        dependencies=["source"],
        status="running",
    )
    base_spec = spec("current")
    resolved = ResolvedSubAgentSpec.model_validate(
        {**base_spec.model_dump(mode="python"), "granted_tools": ["web_search"]}
    )
    state = scheduler_state([current_task], [base_spec])
    state["ready_task_ids"] = ["current"]
    state["resolved_agent_specs"] = [resolved]
    state["worker_results"] = [completed_result("source")]
    state["memory_context"] = [
        MemoryItem(memory_id="global", content="global constraint", scope=[]),
        MemoryItem(memory_id="match", content="LangGraph preference", scope=["LangGraph"]),
        MemoryItem(memory_id="other", content="private other task", scope=["Redis"]),
    ]

    sends = route_runtime_supervisor(state)

    assert isinstance(sends, list) and len(sends) == 1
    assert sends[0].node == "worker"
    assert [item.task_id for item in sends[0].arg["dependency_results"]] == [
        "source"
    ]
    assert [item.memory_id for item in sends[0].arg["relevant_memory"]] == [
        "global",
        "match",
    ]
    assert sends[0].arg["spec"].granted_tools == ["web_search"]
