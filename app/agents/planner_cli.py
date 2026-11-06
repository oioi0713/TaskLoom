"""Command-line entry point for the real guarded Planner."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from langchain_core.exceptions import LangChainException
from openai import APIStatusError, OpenAIError

from app.agents.planner import (
    PlanGenerationError,
    create_default_planner_input,
    create_planner_agent,
)
from app.llm.factory import MissingLLMConfigurationError
from app.config import get_settings
from app.observability import LLMTraceRecorder, default_run_log_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a guarded TaskLoom ExecutionPlan with the configured LLM."
    )
    parser.add_argument("--goal", required=True, help="User goal to decompose.")
    parser.add_argument("--log-dir", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = str(uuid4())
    recorder = LLMTraceRecorder(
        args.log_dir or default_run_log_dir(run_id),
        run_id=run_id,
    )
    settings = get_settings()
    recorder.write_run_metadata(
        {
            "status": "running",
            "entrypoint": "planner_cli",
            "goal": args.goal,
            "model": settings.llm_model,
        }
    )
    try:
        planner = create_planner_agent(settings, trace_recorder=recorder)
        plan = planner.plan(create_default_planner_input(args.goal))
    except MissingLLMConfigurationError as exc:
        recorder.update_run_metadata({"status": "failed", "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 2
    except PlanGenerationError as exc:
        recorder.update_run_metadata({"status": "failed", "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1
    except APIStatusError as exc:
        recorder.update_run_metadata(
            {"status": "failed", "error": f"HTTP {exc.status_code}"}
        )
        print(
            f"LLM API request failed with HTTP {exc.status_code}. "
            "Check the Base URL, API Key, model access, and required network or VPN.",
            file=sys.stderr,
        )
        return 3
    except (OpenAIError, LangChainException) as exc:
        recorder.update_run_metadata(
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        )
        print(
            f"LLM request failed ({type(exc).__name__}). "
            "Check the provider configuration and network connection.",
            file=sys.stderr,
        )
        return 3

    result = plan.model_dump(mode="json")
    recorder.write_result(result)
    recorder.update_run_metadata(
        {
            "status": "completed",
            "llm_call_count": recorder.call_count,
            "result_file": str(recorder.result_path),
        }
    )
    print(plan.model_dump_json(indent=2))
    print(f"日志目录：{recorder.run_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
