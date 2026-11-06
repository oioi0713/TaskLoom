"""Thread-safe, append-only local traces for LLM requests and responses."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage
from pydantic import BaseModel


_BEARER_PATTERN = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+)([^\s\"']+)"
)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def default_run_log_dir(run_id: str) -> Path:
    """Return the ignored project-local directory for one runtime run."""

    project_root = Path(__file__).resolve().parents[2]
    return project_root / "tests" / "log" / run_id


def _redact_text(value: str) -> str:
    value = _BEARER_PATTERN.sub(r"\1<redacted>", value)
    return _OPENAI_KEY_PATTERN.sub("<redacted-api-key>", value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            normalized_key = text_key.lower().replace("-", "_")
            if normalized_key.endswith("api_key") or "authorization" in normalized_key:
                result[text_key] = "<redacted>"
            else:
                result[text_key] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _serialize_messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    return [
        {
            "role": getattr(message, "type", message.__class__.__name__),
            "content": _jsonable(message.content),
        }
        for message in messages
    ]


class LLMTraceRecorder:
    """Write one run's prompts and responses without retaining them in memory."""

    def __init__(self, run_dir: Path, *, run_id: str) -> None:
        self.run_dir = run_dir.resolve()
        self.run_id = run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.calls_path = self.run_dir / "llm_calls.jsonl"
        self.metadata_path = self.run_dir / "run.json"
        self.result_path = self.run_dir / "result.json"
        self._lock = threading.Lock()
        self._started_at: dict[str, float] = {}
        self._call_count = 0
        self._run_metadata: dict[str, Any] = {}
        self.calls_path.touch(exist_ok=True)

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_count

    def write_run_metadata(self, payload: dict[str, Any]) -> None:
        self._run_metadata = {
            "run_id": self.run_id,
            "started_at": datetime.now().astimezone().isoformat(),
            **_jsonable(payload),
        }
        self._write_metadata()

    def update_run_metadata(self, payload: dict[str, Any]) -> None:
        """Update run status while preserving its original start time."""

        self._run_metadata.update(_jsonable(payload))
        self._write_metadata()

    def _write_metadata(self) -> None:
        self.metadata_path.write_text(
            json.dumps(self._run_metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def record_request(
        self,
        *,
        component: str,
        messages: list[BaseMessage],
        attempt: int,
        model: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        call_id = str(uuid4())
        with self._lock:
            self._started_at[call_id] = time.perf_counter()
            self._call_count += 1
        self._append(
            {
                "event": "llm.request",
                "call_id": call_id,
                "component": component,
                "attempt": attempt,
                "model": model,
                "task_id": task_id,
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "messages": _serialize_messages(messages),
            }
        )
        return call_id

    def record_response(self, call_id: str, response: Any) -> None:
        self._append(
            {
                "event": "llm.response",
                "call_id": call_id,
                "duration_ms": self._duration_ms(call_id),
                "response_type": type(response).__name__,
                "response": _jsonable(response),
            }
        )

    def record_error(self, call_id: str, error: BaseException) -> None:
        self._append(
            {
                "event": "llm.error",
                "call_id": call_id,
                "duration_ms": self._duration_ms(call_id),
                "error_type": type(error).__name__,
                "error": _redact_text(str(error)),
            }
        )

    def record_validation_error(self, call_id: str, error: BaseException) -> None:
        """Record local schema validation after an LLM response was received."""

        self._append(
            {
                "event": "llm.validation_error",
                "call_id": call_id,
                "error_type": type(error).__name__,
                "error": _redact_text(str(error)),
            }
        )

    def write_result(self, payload: dict[str, Any]) -> None:
        self.result_path.write_text(
            json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _duration_ms(self, call_id: str) -> int | None:
        with self._lock:
            started = self._started_at.pop(call_id, None)
        if started is None:
            return None
        return max(0, int((time.perf_counter() - started) * 1000))

    def _append(self, payload: dict[str, Any]) -> None:
        event = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "run_id": self.run_id,
            **_jsonable(payload),
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.calls_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
