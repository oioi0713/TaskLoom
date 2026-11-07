from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.contracts.memory import MemoryItem
from app.contracts.output_models import ResearchResult
from app.contracts.result import WorkerResult
from app.contracts.subagent import ResolvedSubAgentSpec
from app.contracts.task import TaskSpec
from app.workers.context import RuntimeContext
from app.workers.factory import (
    WorkerContextError,
    WorkerFactory,
    WorkerLifecycleError,
)
from app.observability import LLMTraceRecorder


class FakeStructuredModel:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[list[Any]] = []
        self.call_kwargs: list[dict[str, Any]] = []

    def invoke(self, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append(messages)
        self.call_kwargs.append(kwargs)
        response = (
            self.response.pop(0)
            if isinstance(self.response, list)
            else self.response
        )
        if isinstance(response, BaseException):
            raise response
        return response


class FakeChatModel:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.bound_models: list[tuple[type, str]] = []
        self.bound_kwargs: list[dict[str, Any]] = []
        self.structured_models: list[FakeStructuredModel] = []

    def with_structured_output(
        self,
        model: type,
        *,
        method: str,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> FakeStructuredModel:
        self.bound_models.append((model, method))
        self.bound_kwargs.append(kwargs)
        structured = FakeStructuredModel(self.response)
        self.structured_models.append(structured)
        return structured


def research_task(*, dependencies: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        task_id="research_langgraph",
        title="调研 LangGraph",
        description="总结 LangGraph 的核心概念",
        task_type="research",
        dependencies=dependencies or [],
        assigned_agent_id="langgraph_researcher",
        output_contract="research_result_v1",
    )


def research_spec() -> ResolvedSubAgentSpec:
    return ResolvedSubAgentSpec(
        agent_id="langgraph_researcher",
        task_id="research_langgraph",
        role="LangGraph 调研员",
        objective="从授权资料总结 LangGraph 核心概念",
        instructions=["区分官方事实和推断", "返回结构化结论"],
        required_capabilities=["web_research"],
        requested_tools=["web_search", "web_page_reader"],
        granted_tools=["web_search"],
        knowledge_scope=["LangGraph", "StateGraph"],
        output_contract="research_result_v1",
        timeout_seconds=60,
        max_steps=4,
        token_budget=2000,
    )


def completed_dependency(task_id: str) -> WorkerResult:
    return WorkerResult(
        run_id="run-5",
        task_id=task_id,
        agent_id=f"{task_id}_agent",
        status="completed",
        output_contract="research_result_v1",
        output={
            "summary": f"{task_id} summary",
            "findings": [f"{task_id} finding"],
            "sources": [],
        },
        execution_time_ms=10,
        token_usage=20,
    )


def runtime_context() -> RuntimeContext:
    return RuntimeContext(
        run_id="run-5",
        relevant_memory=[
            MemoryItem(
                memory_id="m1",
                content="用户希望保留英文术语。",
                scope=["LangGraph"],
            )
        ],
    )


def successful_response() -> ResearchResult:
    return ResearchResult(
        summary="LangGraph 使用图来组织有状态工作流。",
        findings=["StateGraph 负责定义节点、边和共享状态。"],
        sources=[],
    )


def test_worker_prompt_contains_only_bounded_context_and_granted_tools() -> None:
    chat_model = FakeChatModel(successful_response())
    worker = WorkerFactory(chat_model).create_worker(
        research_task(),
        research_spec(),
        [],
        runtime_context(),
    )

    system_message, human_message = worker.build_messages()
    combined = f"{system_message.content}\n{human_message.content}"

    assert "LangGraph 调研员" in combined
    assert "调研 LangGraph" in combined
    assert "web_search" in combined
    assert "web_page_reader" not in combined
    assert "用户希望保留英文术语" in combined
    assert "research_result_v1" in combined
    assert '"additionalProperties": false' in combined
    assert "API Key" not in combined
    assert "整段主会话" not in combined
    assert "granted tool is optional permission" in combined
    assert "Finalizer" in combined


def test_worker_prompt_contains_reviewer_retry_feedback() -> None:
    worker = WorkerFactory(FakeChatModel(successful_response())).create_worker(
        research_task(),
        research_spec(),
        [],
        RuntimeContext(
            run_id="run-5",
            relevant_memory=[],
            review_feedback=["补充官方证据并解释限制"],
        ),
    )

    combined = "\n".join(str(message.content) for message in worker.build_messages())

    assert "补充官方证据并解释限制" in combined


def test_runtime_context_rejects_conversation_and_credentials() -> None:
    with pytest.raises(ValidationError):
        RuntimeContext(
            run_id="run-5",
            relevant_memory=[],
            messages=["private conversation"],
            api_key="secret",
        )


def test_worker_receives_exact_completed_dependency_results() -> None:
    task = research_task(dependencies=["source_task"])
    dependency = completed_dependency("source_task")
    worker = WorkerFactory(FakeChatModel(successful_response())).create_worker(
        task,
        research_spec(),
        [dependency],
        runtime_context(),
    )

    human_payload = worker.build_messages()[1].content
    assert "source_task summary" in human_payload
    assert '"agent_id"' not in human_payload
    assert '"token_usage"' not in human_payload


def test_worker_rejects_missing_or_unrelated_dependency_context() -> None:
    task = research_task(dependencies=["source_task"])
    factory = WorkerFactory(FakeChatModel(successful_response()))

    with pytest.raises(WorkerContextError, match="missing: source_task"):
        factory.create_worker(task, research_spec(), [], runtime_context())

    with pytest.raises(WorkerContextError, match="unrelated: other_task"):
        factory.create_worker(
            task,
            research_spec(),
            [completed_dependency("source_task"), completed_dependency("other_task")],
            runtime_context(),
        )


def test_worker_rejects_task_agent_mismatch() -> None:
    task = research_task().model_copy(update={"assigned_agent_id": "other_agent"})

    with pytest.raises(WorkerContextError, match="assigned to other_agent"):
        WorkerFactory(FakeChatModel(successful_response())).create_worker(
            task,
            research_spec(),
            [],
            runtime_context(),
        )


def test_worker_executes_once_validates_output_and_releases_context() -> None:
    chat_model = FakeChatModel(successful_response())
    worker = WorkerFactory(chat_model).create_worker(
        research_task(),
        research_spec(),
        [],
        runtime_context(),
    )

    result = worker.execute()

    assert result.status == "completed"
    assert result.output["summary"].startswith("LangGraph")
    assert result.task_id == "research_langgraph"
    assert result.agent_id == "langgraph_researcher"
    assert result.tool_calls == []
    assert chat_model.structured_models[0].call_kwargs == [{}]
    assert chat_model.bound_kwargs == [{"max_tokens": 2000}]
    assert worker.released is True
    with pytest.raises(WorkerLifecycleError, match="cannot be reused"):
        worker.execute()
    with pytest.raises(WorkerLifecycleError, match="released"):
        worker.build_messages()


def test_factory_creates_a_fresh_worker_and_model_binding_each_time() -> None:
    chat_model = FakeChatModel(successful_response())
    factory = WorkerFactory(chat_model)

    first = factory.create_worker(
        research_task(), research_spec(), [], runtime_context()
    )
    second = factory.create_worker(
        research_task(), research_spec(), [], runtime_context()
    )

    assert first is not second
    assert len(chat_model.structured_models) == 2
    assert all(model is ResearchResult for model, _ in chat_model.bound_models)


def test_factory_clamps_worker_output_tokens_below_spec_budget() -> None:
    chat_model = FakeChatModel(successful_response())
    worker = WorkerFactory(chat_model, max_output_tokens=512).create_worker(
        research_task(), research_spec(), [], runtime_context()
    )

    result = worker.execute()

    assert result.status == "completed"
    assert chat_model.structured_models[0].call_kwargs == [{}]
    assert chat_model.bound_kwargs == [{"max_tokens": 512}]


def test_factory_rejects_non_positive_output_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        WorkerFactory(FakeChatModel(successful_response()), max_output_tokens=0)


def test_invalid_structured_output_is_retried_then_becomes_auditable_failure() -> None:
    chat_model = FakeChatModel(
        {"summary": "缺少 findings 和 sources，不能通过输出协议"}
    )
    worker = WorkerFactory(chat_model).create_worker(
        research_task(),
        research_spec(),
        [],
        runtime_context(),
    )

    result = worker.execute()

    assert result.status == "invalid_output"
    assert result.output is None
    assert "research_result_v1" in result.error
    assert "after two attempts" in result.error
    assert len(chat_model.structured_models[0].calls) == 2
    assert "remove all fields" in str(
        chat_model.structured_models[0].calls[1][-1].content
    )
    assert worker.released is True


def test_worker_recovers_when_second_structured_output_is_valid() -> None:
    chat_model = FakeChatModel(
        [
            {"summary": "first response omitted required findings"},
            successful_response(),
        ]
    )
    worker = WorkerFactory(chat_model).create_worker(
        research_task(),
        research_spec(),
        [],
        runtime_context(),
    )

    result = worker.execute()

    assert result.status == "completed"
    assert result.output["findings"]
    assert len(chat_model.structured_models[0].calls) == 2
    assert worker.released is True


def test_timeout_becomes_timeout_result_and_releases_context() -> None:
    worker = WorkerFactory(FakeChatModel(TimeoutError("provider timed out"))).create_worker(
        research_task(),
        research_spec(),
        [],
        runtime_context(),
    )

    result = worker.execute()

    assert result.status == "timeout"
    assert result.error == "provider timed out"
    assert worker.released is True


def test_prompt_is_valid_json_after_the_instruction_prefix() -> None:
    worker = WorkerFactory(FakeChatModel(successful_response())).create_worker(
        research_task(),
        research_spec(),
        [],
        runtime_context(),
    )
    human_content = str(worker.build_messages()[1].content)
    payload = json.loads(human_content[human_content.index("{") :])

    assert payload["task"]["task_id"] == "research_langgraph"
    assert [item["memory_id"] for item in payload["relevant_memory"]] == ["m1"]


def test_worker_records_prompt_response_and_identity(tmp_path) -> None:
    recorder = LLMTraceRecorder(tmp_path / "trace", run_id="run-5")
    worker = WorkerFactory(
        FakeChatModel(successful_response()),
        trace_recorder=recorder,
        model_name="test-model",
    ).create_worker(research_task(), research_spec(), [], runtime_context())

    result = worker.execute()

    events = [
        json.loads(line)
        for line in recorder.calls_path.read_text(encoding="utf-8").splitlines()
    ]
    assert result.status == "completed"
    assert [event["event"] for event in events] == [
        "llm.request",
        "llm.response",
    ]
    assert events[0]["task_id"] == "research_langgraph"
    assert events[0]["agent_id"] == "langgraph_researcher"
    assert events[0]["call_id"] == events[1]["call_id"]


def test_worker_records_provider_token_usage_from_raw_response() -> None:
    response = {
        "raw": AIMessage(
            content="",
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 80,
                "total_tokens": 200,
            },
        ),
        "parsed": successful_response(),
        "parsing_error": None,
    }
    worker = WorkerFactory(FakeChatModel(response)).create_worker(
        research_task(), research_spec(), [], runtime_context()
    )

    result = worker.execute()

    assert result.status == "completed"
    assert result.token_usage == 200
