"""Three-layer compression for one Worker's private message context."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


CompressionLayer = Literal["none", "tool_snip", "summarize", "hard_collapse"]
SummaryFunction = Callable[[list[BaseMessage], bool], str]

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uff65]")
_SYMBOL_RE = re.compile(r"[^\w\s]")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def estimate_text_tokens(value: object) -> int:
    """Estimate mixed Chinese, prose, and code tokens conservatively."""

    text = _text(value)
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    dense = rest > 0 and len(_SYMBOL_RE.findall(text)) / len(text) > 0.25
    return math.ceil(cjk / 1.5) + math.ceil(rest / (2.8 if dense else 3.4))


def estimate_message_tokens(messages: list[BaseMessage]) -> int:
    total = 0
    for message in messages:
        total += 4 + estimate_text_tokens(message.content)
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            total += estimate_text_tokens(tool_calls)
    return total


@dataclass(frozen=True, slots=True)
class ContextCompressionResult:
    messages: list[BaseMessage]
    layer: CompressionLayer
    original_tokens: int
    final_tokens: int
    context_exhausted: bool = False

    @property
    def changed(self) -> bool:
        return self.layer != "none"


class ContextCompressor:
    """Apply cheap-to-expensive compression while preserving tool-call groups."""

    def __init__(
        self,
        *,
        context_window_tokens: int,
        reserved_output_tokens: int,
        tool_schema_tokens: int = 0,
        safety_margin_tokens: int = 1024,
        keep_recent: int = 8,
    ) -> None:
        usable = (
            context_window_tokens
            - reserved_output_tokens
            - tool_schema_tokens
            - safety_margin_tokens
        )
        self.usable_input_tokens = max(1, usable)
        self.snip_at = int(self.usable_input_tokens * 0.50)
        self.summarize_at = int(self.usable_input_tokens * 0.70)
        self.collapse_at = int(self.usable_input_tokens * 0.90)
        self.keep_recent = max(2, keep_recent)

    @staticmethod
    def _safe_tail_start(messages: list[BaseMessage], keep_recent: int) -> int:
        start = max(0, len(messages) - keep_recent)
        while start > 0 and isinstance(messages[start], ToolMessage):
            start -= 1
        return start

    @staticmethod
    def _prefix_end(messages: list[BaseMessage]) -> int:
        """Keep the assignment's leading System/Human messages verbatim."""

        end = 0
        if messages and isinstance(messages[0], SystemMessage):
            end = 1
        if end < len(messages) and isinstance(messages[end], HumanMessage):
            end += 1
        return end

    @staticmethod
    def _snip_tool_outputs(messages: list[BaseMessage]) -> tuple[list[BaseMessage], bool]:
        changed = False
        result: list[BaseMessage] = []
        protected_from = max(0, len(messages) - 2)
        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage) or index >= protected_from:
                result.append(message)
                continue
            content = _text(message.content)
            if len(content) <= 1500:
                result.append(message)
                continue
            snipped = (
                content[:650]
                + f"\n... ({len(content)} chars snipped for context) ...\n"
                + content[-650:]
            )
            result.append(message.model_copy(update={"content": snipped}))
            changed = True
        return result, changed

    def _summarize(
        self,
        messages: list[BaseMessage],
        summarizer: SummaryFunction | None,
        *,
        keep_recent: int,
        emergency: bool,
    ) -> tuple[list[BaseMessage], bool]:
        prefix_end = self._prefix_end(messages)
        split = self._safe_tail_start(messages, keep_recent)
        if split <= prefix_end:
            return messages, False
        old = messages[prefix_end:split]
        if summarizer is not None:
            try:
                summary = summarizer(old, emergency).strip()
            except Exception:  # Compression must degrade without crashing the Worker.
                summary = ""
        else:
            summary = ""
        if not summary:
            summary = self._fallback_summary(old, emergency=emergency)
        label = "Emergency context summary" if emergency else "Compressed context summary"
        summary_message = SystemMessage(content=f"[{label}]\n{summary}")
        return [*messages[:prefix_end], summary_message, *messages[split:]], True

    @staticmethod
    def _fallback_summary(messages: list[BaseMessage], *, emergency: bool) -> str:
        limit = 120 if emergency else 240
        parts: list[str] = []
        for message in messages:
            role = message.type
            content = " ".join(_text(message.content).split())
            if content:
                parts.append(f"{role}: {content[:limit]}")
        max_parts = 8 if emergency else 16
        return "\n".join(parts[-max_parts:]) or "No reusable earlier context."

    def compress(
        self,
        messages: list[BaseMessage],
        *,
        summarizer: SummaryFunction | None = None,
    ) -> ContextCompressionResult:
        original = estimate_message_tokens(messages)
        current = list(messages)
        layer: CompressionLayer = "none"

        if original > self.snip_at:
            current, changed = self._snip_tool_outputs(current)
            if changed:
                layer = "tool_snip"

        current_tokens = estimate_message_tokens(current)
        if current_tokens > self.summarize_at:
            current, changed = self._summarize(
                current,
                summarizer,
                keep_recent=self.keep_recent,
                emergency=False,
            )
            if changed:
                layer = "summarize"

        current_tokens = estimate_message_tokens(current)
        if current_tokens > self.collapse_at:
            current, changed = self._summarize(
                current,
                summarizer,
                keep_recent=4,
                emergency=True,
            )
            if changed:
                layer = "hard_collapse"

        final_tokens = estimate_message_tokens(current)
        return ContextCompressionResult(
            messages=current,
            layer=layer,
            original_tokens=original,
            final_tokens=final_tokens,
            context_exhausted=final_tokens > self.usable_input_tokens,
        )
