import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.observability import LLMTraceRecorder


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
