from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.checkpoint.redis import (
    RedisCheckpointConfigurationError,
    open_redis_checkpointer,
    validate_redis_checkpoint_server,
)
from app.contracts.budget import ExecutionBudget
from app.contracts.plan import ExecutionPlan
from app.contracts.result import ToolCallRecord, WorkerResult
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.builder import create_runtime_initial_state
from app.graph.rehydration import (
    rehydrate_stage6_state,
    rehydrate_tool_call_records,
)


class FakeRedis:
    def __init__(self, version: str, modules: list[bytes]) -> None:
        self.version = version
        self.modules = modules

    def ping(self) -> bool:
        return True

    def info(self, section: str) -> dict[str, str]:
        assert section == "server"
        return {"redis_version": self.version}

    def module_list(self) -> list[dict[bytes, bytes]]:
        return [{b"name": name} for name in self.modules]


def test_plain_redis_before_version_eight_is_rejected() -> None:
    with pytest.raises(
        RedisCheckpointConfigurationError,
        match="RedisJSON.*RediSearch",
    ):
        validate_redis_checkpoint_server(FakeRedis("6.0.16", []))


def test_redis_stack_modules_are_accepted() -> None:
    version = validate_redis_checkpoint_server(
        FakeRedis("7.4.0", [b"ReJSON", b"search"])
    )

    assert version == "7.4.0"


def test_redis_eight_is_accepted_without_module_entries() -> None:
    assert validate_redis_checkpoint_server(FakeRedis("8.0.0", [])) == "8.0.0"


def _constructor_envelope(value: BaseModel) -> dict:
    return {
        "lc": 2,
        "type": "constructor",
        "id": [value.__class__.__module__, value.__class__.__name__],
        "kwargs": value.model_dump(mode="python"),
    }


def test_redis_state_rehydrates_nested_pydantic_contracts() -> None:
    task = TaskSpec(
        task_id="report",
        title="Write report",
        description="Write a checkpoint report",
        task_type="report",
        assigned_agent_id="writer",
        output_contract="report_result_v1",
    )
    agent = SubAgentSpec(
        agent_id="writer",
        task_id="report",
        role="Writer",
        objective="Write the report",
        instructions=["Return structured output"],
        required_capabilities=["report_writing"],
        output_contract="report_result_v1",
    )
    budget = ExecutionBudget(max_concurrency=1)
    plan = ExecutionPlan(
        goal_summary="Write a report",
        tasks=[task],
        agents=[agent],
        expected_deliverable="A report",
        budget=budget,
    )
    serialized_plan = _constructor_envelope(plan)
    serialized_plan["kwargs"]["tasks"] = [_constructor_envelope(task)]
    serialized_plan["kwargs"]["agents"] = [_constructor_envelope(agent)]
    serialized_plan["kwargs"]["budget"] = _constructor_envelope(budget)
    state = create_runtime_initial_state("Write a report")
    state["budget"] = _constructor_envelope(budget)  # type: ignore[typeddict-item]
    state["tasks"] = [_constructor_envelope(task)]  # type: ignore[list-item]
    state["agent_specs"] = [_constructor_envelope(agent)]  # type: ignore[list-item]
    state["execution_plan"] = serialized_plan  # type: ignore[typeddict-item]

    restored = rehydrate_stage6_state(state)

    assert isinstance(restored["budget"], ExecutionBudget)
    assert isinstance(restored["tasks"][0], TaskSpec)
    assert isinstance(restored["agent_specs"][0], SubAgentSpec)
    assert isinstance(restored["execution_plan"], ExecutionPlan)
    assert isinstance(restored["execution_plan"].tasks[0], TaskSpec)


def test_redis_pending_write_rehydrates_tool_call_records() -> None:
    record = ToolCallRecord(
        call_id="call-1",
        tool_name="web_search",
        arguments={"query": "LangGraph"},
        success=False,
        error="timed out",
        execution_time_ms=30000,
        observation={"recoverable": True},
    )

    restored = rehydrate_tool_call_records([_constructor_envelope(record)])

    assert restored == [record]
    assert isinstance(restored[0], ToolCallRecord)


def test_legacy_worker_results_are_migrated_to_active_results() -> None:
    result = WorkerResult(
        run_id="legacy-run",
        task_id="legacy-task",
        agent_id="legacy-agent",
        status="failed",
        output_contract="report_result_v1",
        error="legacy failure",
        execution_time_ms=1,
        token_usage=0,
    )
    state = create_runtime_initial_state("Resume a legacy checkpoint")
    state.pop("session_id")
    state.pop("active_worker_results")
    state["worker_results"] = [_constructor_envelope(result)]

    restored = rehydrate_stage6_state(state)

    assert restored["active_worker_results"] == [result]
    assert "worker_results" not in restored
    assert restored["session_id"] == restored["thread_id"]


def test_redis_saver_receives_sliding_ttl(monkeypatch) -> None:
    captured: dict = {}

    class Probe:
        def close(self) -> None:
            return None

    class Saver:
        def setup(self) -> None:
            return None

    class SaverContext:
        def __enter__(self):
            return Saver()

        def __exit__(self, *args):
            return None

    def fake_from_conn_string(redis_url: str, **kwargs):
        captured["redis_url"] = redis_url
        captured.update(kwargs)
        return SaverContext()

    monkeypatch.setattr("app.checkpoint.redis.Redis.from_url", lambda *a, **k: Probe())
    monkeypatch.setattr(
        "app.checkpoint.redis.validate_redis_checkpoint_server",
        lambda probe: "8.0.0",
    )
    monkeypatch.setattr(
        "langgraph.checkpoint.redis.RedisSaver.from_conn_string",
        fake_from_conn_string,
    )

    with open_redis_checkpointer("redis://localhost:6379/0", ttl_minutes=1440):
        pass

    assert captured["ttl"] == {
        "default_ttl": 1440,
        "refresh_on_read": True,
    }
