import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.observability import LLMTraceRecorder
from app.contracts.result import ToolCallRecord


def test_trace_recorder_writes_run_calls_and_result(tmp_path) -> None:
    recorder = LLMTraceRecorder(tmp_path / "run-1", run_id="run-1")
    recorder.write_run_metadata(
        {"status": "running", "goal": "test", "api_key": "secret-value"}
    )
    call_id = recorder.record_request(
        component="worker",
        messages=[
            SystemMessage(content="Authorization: Bearer secret-token"),
            HumanMessage(content="use sk-abcdefgh12345678"),
        ],
        attempt=1,
        model="test-model",
        task_id="T1",
        agent_id="A1",
        max_tokens=256,
    )
    recorder.record_response(call_id, {"answer": "done"})
    recorder.write_result({"workflow_status": "completed"})
    recorder.update_run_metadata({"status": "completed"})

    metadata_text = recorder.metadata_path.read_text(encoding="utf-8")
    events_text = recorder.calls_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in events_text.splitlines()]

    assert "secret-value" not in metadata_text
    assert "secret-token" not in events_text
    assert "sk-abcdefgh12345678" not in events_text
    assert json.loads(metadata_text)["api_key"] == "<redacted>"
    assert events[0]["messages"][0]["content"].endswith("<redacted>")
    assert events[0]["call_id"] == events[1]["call_id"]
    assert recorder.call_count == 1
    assert json.loads(recorder.result_path.read_text(encoding="utf-8")) == {
        "workflow_status": "completed"
    }


def test_trace_recorder_keeps_request_when_call_fails(tmp_path) -> None:
    recorder = LLMTraceRecorder(tmp_path / "run-2", run_id="run-2")
    call_id = recorder.record_request(
        component="planner",
        messages=[HumanMessage(content="plan this")],
        attempt=1,
    )
    recorder.record_error(call_id, TimeoutError("provider timed out"))

    events = [
        json.loads(line)
        for line in recorder.calls_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == [
        "llm.request",
        "llm.error",
    ]
    assert events[0]["call_id"] == events[1]["call_id"]


def test_trace_recorder_preserves_tool_call_message_identity(tmp_path) -> None:
    recorder = LLMTraceRecorder(tmp_path / "run-tools", run_id="run-tools")
    recorder.record_request(
        component="worker_agent",
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "LangGraph"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content='{"results": []}',
                tool_call_id="call-1",
                name="web_search",
            ),
        ],
        attempt=2,
    )

    event = json.loads(recorder.calls_path.read_text(encoding="utf-8"))

    assert event["messages"][0]["tool_calls"][0]["name"] == "web_search"
    assert event["messages"][1]["tool_call_id"] == "call-1"
    assert event["messages"][1]["name"] == "web_search"


def test_trace_recorder_writes_tool_outcome_immediately(tmp_path) -> None:
    recorder = LLMTraceRecorder(tmp_path / "run-tool-event", run_id="run-tool-event")

    recorder.record_tool_call(
        task_id="T1",
        agent_id="A1",
        record=ToolCallRecord(
            call_id="tool-1",
            tool_name="document_reader",
            arguments={"path": "app/config.py"},
            success=True,
            execution_time_ms=12,
            observation={"path": "app/config.py", "content": "example"},
        ),
    )

    event = json.loads(recorder.tool_calls_path.read_text(encoding="utf-8"))
    assert event["event"] == "tool.called"
    assert event["task_id"] == "T1"
    assert event["success"] is True
    assert event["observation"]["path"] == "app/config.py"
    assert recorder.tool_call_count == 1
