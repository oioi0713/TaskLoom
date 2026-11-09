from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.memory.compression import ContextCompressor, estimate_message_tokens


def compressor(*, context: int = 5000, output: int = 500) -> ContextCompressor:
    return ContextCompressor(
        context_window_tokens=context,
        reserved_output_tokens=output,
        safety_margin_tokens=0,
        keep_recent=4,
    )


def test_below_half_capacity_keeps_messages_unchanged() -> None:
    messages = [SystemMessage(content="rules"), HumanMessage(content="task")]
    result = compressor().compress(messages)

    assert result.layer == "none"
    assert result.messages == messages
    assert result.context_exhausted is False


def test_half_capacity_snips_old_large_tool_observation() -> None:
    messages = [
        SystemMessage(content="rules"),
        HumanMessage(content="task"),
        AIMessage(
            content="",
            tool_calls=[{"name": "reader", "args": {}, "id": "c1"}],
        ),
        ToolMessage(content="x" * 7000, tool_call_id="c1", name="reader"),
        HumanMessage(content="continue"),
        AIMessage(content="working"),
    ]

    result = compressor(context=4000, output=500).compress(messages)

    assert result.layer in {"tool_snip", "summarize", "hard_collapse"}
    tool_messages = [m for m in result.messages if isinstance(m, ToolMessage)]
    assert tool_messages
    assert "snipped for context" in str(tool_messages[0].content)
    assert result.final_tokens < result.original_tokens


def test_summary_keeps_tool_call_and_tool_message_together() -> None:
    messages = [SystemMessage(content="r"), HumanMessage(content="task")]
    for index in range(6):
        messages.extend(
            [
                AIMessage(
                    content="decision " + "d" * 600,
                    tool_calls=[
                        {"name": "reader", "args": {"i": index}, "id": f"c{index}"}
                    ],
                ),
                ToolMessage(
                    content="observation " + "o" * 600,
                    tool_call_id=f"c{index}",
                    name="reader",
                ),
            ]
        )

    result = compressor(context=3500, output=500).compress(
        messages,
        summarizer=lambda old, emergency: "verified facts and next action",
    )

    assert result.layer in {"summarize", "hard_collapse"}
    for index, message in enumerate(result.messages):
        if isinstance(message, ToolMessage):
            assert index > 0
            assert isinstance(result.messages[index - 1], (AIMessage, ToolMessage))
            preceding = result.messages[index - 1]
            if isinstance(preceding, AIMessage):
                assert any(
                    call["id"] == message.tool_call_id for call in preceding.tool_calls
                )


def test_impossibly_large_required_prefix_reports_context_exhausted() -> None:
    messages = [
        SystemMessage(content="s" * 10000),
        HumanMessage(content="task" * 1000),
    ]
    result = compressor(context=3000, output=500).compress(messages)

    assert estimate_message_tokens(result.messages) > result.final_tokens - 1
    assert result.context_exhausted is True
