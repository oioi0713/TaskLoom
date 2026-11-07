from pathlib import Path

from app.tools.runtime import ToolRuntime


def test_document_reader_is_bounded_to_workspace(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("LangGraph uses nodes and edges.", encoding="utf-8")
    runtime = ToolRuntime(workspace_root=tmp_path)

    result = runtime.execute("document_reader", {"path": "notes.md", "max_chars": 500})

    assert result.success is True
    assert result.observation["path"] == "notes.md"
    assert result.observation["source"]["source_type"] == "local_document"


def test_document_reader_rejects_path_escape(tmp_path: Path) -> None:
    runtime = ToolRuntime(workspace_root=tmp_path)

    result = runtime.execute(
        "document_reader",
        {"path": str(tmp_path.parent / "outside.md"), "max_chars": 500},
    )

    assert result.success is False
    assert "outside the approved workspace" in (result.error or "")


def test_knowledge_search_returns_auditable_local_source(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        "StateGraph connects nodes using conditional edges.", encoding="utf-8"
    )
    runtime = ToolRuntime(workspace_root=tmp_path)

    result = runtime.execute(
        "knowledge_search", {"query": "StateGraph edges", "max_results": 3}
    )

    assert result.success is True
    assert result.observation["matches"][0]["path"] == "guide.md"
    assert result.observation["matches"][0]["source"]["accessed_at"]


def test_tool_arguments_are_schema_validated_before_execution(tmp_path: Path) -> None:
    runtime = ToolRuntime(workspace_root=tmp_path)

    result = runtime.execute("web_search", {"query": "", "unexpected": True})

    assert result.success is False
    assert "ValidationError" in (result.error or "")
