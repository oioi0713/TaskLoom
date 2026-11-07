"""Command-line smoke tests for LLM-only and tool-enabled Workers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from app.contracts.subagent import ResolvedSubAgentSpec
from app.contracts.task import TaskSpec
from app.llm.factory import MissingLLMConfigurationError
from app.config import get_settings
from app.observability import LLMTraceRecorder, default_run_log_dir
from app.workers.context import RuntimeContext
from app.workers.factory import create_worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one real short-lived TaskLoom Worker."
    )
    parser.add_argument("--task", required=True, help="Task for the Worker to complete.")
    parser.add_argument(
        "--mode",
        choices=["report", "web-research"],
        default="report",
        help="Use a direct report Worker or a web-search Agent Loop.",
    )
    parser.add_argument(
        "--role",
        default=None,
        help="Dynamic role assigned to the Worker.",
    )
    parser.add_argument("--log-dir", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    resolved_role = args.role or (
        "网页研究员" if args.mode == "web-research" else "技术报告撰写员"
    )
    run_id = str(uuid4())
    recorder = LLMTraceRecorder(
        args.log_dir or default_run_log_dir(run_id),
        run_id=run_id,
    )
    settings = get_settings()
    recorder.write_run_metadata(
        {
            "status": "running",
            "entrypoint": "worker_cli",
            "task": args.task,
            "role": resolved_role,
            "model": settings.llm_model,
            "llm_calls_file": str(recorder.calls_path),
            "tool_calls_file": str(recorder.tool_calls_path),
        }
    )
    web_research = args.mode == "web-research"
    output_contract = "research_result_v1" if web_research else "report_result_v1"
    task = TaskSpec(
        task_id="worker_demo",
        title="阶段 8 真实 Worker 演示",
        description=args.task,
        task_type="research" if web_research else "report",
        assigned_agent_id="worker_demo_agent",
        output_contract=output_contract,
    )
    spec = ResolvedSubAgentSpec(
        agent_id="worker_demo_agent",
        task_id="worker_demo",
        role=resolved_role,
        objective=args.task,
        instructions=(
            [
                "使用中文回答并保留必要的英文技术术语",
                "优先搜索官方资料并读取至少一个相关页面",
                "根据 Tool Observation 决定是否继续搜索或结束",
                "只返回符合 research_result_v1 的结构化调研结果",
            ]
            if web_research
            else [
                "使用中文回答并保留必要的英文技术术语",
                "不得声称调用了任何工具",
                "只返回符合 report_result_v1 的结构化报告",
            ]
        ),
        required_capabilities=["web_research" if web_research else "report_writing"],
        requested_tools=(
            ["web_search", "web_page_reader"] if web_research else []
        ),
        granted_tools=(
            ["web_search", "web_page_reader"] if web_research else []
        ),
        knowledge_scope=["用户提供的当前任务"],
        output_contract=output_contract,
        timeout_seconds=120 if web_research else 60,
        max_steps=8 if web_research else 3,
        token_budget=8192 if web_research else 2000,
    )

    try:
        worker = create_worker(
            task=task,
            spec=spec,
            dependency_results=[],
            runtime_context=RuntimeContext(
                run_id=run_id,
                relevant_memory=[],
            ),
            settings=settings,
            trace_recorder=recorder,
        )
    except MissingLLMConfigurationError as exc:
        recorder.update_run_metadata({"status": "failed", "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 2

    if web_research:
        result = worker.execute(
            progress=lambda message: print(
                f"[Worker] {message}", file=sys.stderr, flush=True
            )
        )
    else:
        result = worker.execute()
    recorder.write_result(result.model_dump(mode="json"))
    recorder.update_run_metadata(
        {
            "status": result.status,
            "llm_call_count": recorder.call_count,
            "tool_call_count": recorder.tool_call_count,
            "result_file": str(recorder.result_path),
        }
    )
    print(result.model_dump_json(indent=2))
    print(f"CONTEXT_RELEASED={worker.released}")
    print(f"日志目录：{recorder.run_dir}", file=sys.stderr)
    print(f"工具调用日志：{recorder.tool_calls_path}", file=sys.stderr)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
