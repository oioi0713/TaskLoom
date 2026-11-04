"""Command-line smoke test for one real isolated Worker."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app.contracts.subagent import ResolvedSubAgentSpec
from app.contracts.task import TaskSpec
from app.llm.factory import MissingLLMConfigurationError
from app.workers.context import RuntimeContext
from app.workers.factory import create_worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one real short-lived TaskLoom Worker with no tools."
    )
    parser.add_argument("--task", required=True, help="Task for the Worker to complete.")
    parser.add_argument(
        "--role",
        default="技术报告撰写员",
        help="Dynamic role assigned to the Worker.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = TaskSpec(
        task_id="worker_demo",
        title="阶段 5 真实 Worker 演示",
        description=args.task,
        task_type="report",
        assigned_agent_id="worker_demo_agent",
        output_contract="report_result_v1",
    )
    spec = ResolvedSubAgentSpec(
        agent_id="worker_demo_agent",
        task_id="worker_demo",
        role=args.role,
        objective=args.task,
        instructions=[
            "使用中文回答并保留必要的英文技术术语",
            "不得声称调用了任何工具",
            "只返回符合 report_result_v1 的结构化报告",
        ],
        required_capabilities=["report_writing"],
        requested_tools=[],
        granted_tools=[],
        knowledge_scope=["用户提供的当前任务"],
        output_contract="report_result_v1",
        timeout_seconds=60,
        max_steps=3,
        token_budget=2000,
    )

    try:
        worker = create_worker(
            task=task,
            spec=spec,
            dependency_results=[],
            runtime_context=RuntimeContext(
                run_id="stage5-worker-demo",
                relevant_memory=[],
            ),
        )
    except MissingLLMConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = worker.execute()
    print(result.model_dump_json(indent=2))
    print(f"CONTEXT_RELEASED={worker.released}")
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
