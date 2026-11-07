"""Command-line entry point for the complete stage-eight runtime graph."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from langchain_core.exceptions import LangChainException
from openai import APIStatusError, OpenAIError

from app.agents.planner import PlanGenerationError
from app.agents.reviewer import ReviewGenerationError
from app.config import get_settings
from app.graph.builder import build_runtime_graph, create_runtime_initial_state
from app.llm.factory import MissingLLMConfigurationError
from app.observability import LLMTraceRecorder, default_run_log_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the stage-eight workflow with autonomous Worker tool loops."
        )
    )
    parser.add_argument("--goal", required=True, help="Goal for TaskLoom to execute.")
    parser.add_argument(
        "--disable-tools",
        action="store_true",
        help="Disable executable tools and run Workers in LLM-only mode.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help=(
            "Directory for run.json, llm_calls.jsonl, tool_calls.jsonl, "
            "and result.json. "
            "Defaults to tests/log/<run_id>."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional compatibility override for the final JSON file only.",
    )
    parser.add_argument(
        "--no-print-json",
        action="store_true",
        help="Show progress only; still save the complete JSON result file.",
    )
    return parser


def _print_progress(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[TaskLoom {timestamp}] {message}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    initial_state = create_runtime_initial_state(
        args.goal,
        enabled_tools=[] if args.disable_tools else None,
    )
    run_id = initial_state["run_id"]
    log_dir = args.log_dir or default_run_log_dir(run_id)
    recorder = LLMTraceRecorder(log_dir, run_id=run_id)
    settings = get_settings()
    recorder.write_run_metadata(
        {
            "status": "running",
            "goal": args.goal,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "timeout_seconds": settings.llm_timeout_seconds,
            "max_retries": settings.llm_max_retries,
            "planner_max_output_tokens": settings.llm_planner_max_output_tokens,
            "reviewer_max_output_tokens": settings.llm_reviewer_max_output_tokens,
            "worker_max_output_tokens": settings.llm_worker_max_output_tokens,
            "cumulative_token_budget_enforced": False,
            "enabled_tools": initial_state["enabled_tools"],
            "llm_calls_file": str(recorder.calls_path),
            "tool_calls_file": str(recorder.tool_calls_path),
        }
    )
    try:
        _print_progress("开始阶段 8 Worker Agent Loop 工作流")
        _print_progress(f"run_id={run_id}")
        _print_progress(f"本次运行日志目录：{recorder.run_dir}")
        graph = build_runtime_graph(
            settings=settings,
            progress=_print_progress,
            trace_recorder=recorder,
        )
        state = graph.invoke(
            initial_state,
            config={"recursion_limit": 100},
        )
    except MissingLLMConfigurationError as exc:
        recorder.update_run_metadata({"status": "failed", "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 2
    except PlanGenerationError as exc:
        recorder.update_run_metadata({"status": "failed", "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1
    except ReviewGenerationError as exc:
        recorder.update_run_metadata({"status": "failed", "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1
    except APIStatusError as exc:
        recorder.update_run_metadata(
            {"status": "failed", "error": f"HTTP {exc.status_code}"}
        )
        print(
            f"LLM API request failed with HTTP {exc.status_code}. "
            "Check the Base URL, API Key, model access, and network.",
            file=sys.stderr,
        )
        return 3
    except (OpenAIError, LangChainException) as exc:
        recorder.update_run_metadata(
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        )
        print(
            f"LLM request failed ({type(exc).__name__}). "
            "Check provider configuration and network access.",
            file=sys.stderr,
        )
        return 3
    except KeyboardInterrupt:
        recorder.update_run_metadata({"status": "interrupted"})
        _print_progress(f"运行已中断；已有日志保留在：{recorder.run_dir}")
        return 130
    except Exception as exc:
        recorder.update_run_metadata(
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        )
        raise

    payload = {
        "run_id": state["run_id"],
        "workflow_status": state["workflow_status"],
        "plan_version": state["plan_version"],
        "tasks": [task.model_dump(mode="json") for task in state["tasks"]],
        "worker_results": [
            result.model_dump(mode="json") for result in state["worker_results"]
        ],
        "reviewer_decision": (
            state["reviewer_decision"].model_dump(mode="json")
            if state.get("reviewer_decision") is not None
            else None
        ),
        "total_steps": state["total_steps"],
        "retry_count": state["retry_count"],
        "replan_count": state["replan_count"],
        "errors": state["errors"],
        "events": [event.model_dump(mode="json") for event in state["events"]],
        "final_output": state["final_output"],
        "log_dir": str(recorder.run_dir),
        "llm_calls_file": str(recorder.calls_path),
        "tool_calls_file": str(recorder.tool_calls_path),
        "llm_call_count": recorder.call_count,
        "tool_call_count": recorder.tool_call_count,
    }
    json_output = json.dumps(payload, ensure_ascii=False, indent=2)
    output_path = args.output or recorder.result_path
    if args.output is None:
        recorder.write_result(payload)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_output + "\n", encoding="utf-8")
    recorder.update_run_metadata(
        {
            "status": state["workflow_status"],
            "finished_at": datetime.now().astimezone().isoformat(),
            "llm_call_count": recorder.call_count,
            "tool_call_count": recorder.tool_call_count,
            "result_file": str(output_path.resolve()),
        }
    )
    _print_progress(f"最终 JSON 已保存到：{output_path.resolve()}")
    _print_progress(f"LLM 请求与返回消息已保存到：{recorder.calls_path}")
    _print_progress(f"工具调用日志已保存到：{recorder.tool_calls_path}")
    if not args.no_print_json:
        print(json_output)
    return 0 if state["workflow_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
