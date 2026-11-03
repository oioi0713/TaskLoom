"""Command-line entry point for the real guarded Planner."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from langchain_core.exceptions import LangChainException
from openai import APIStatusError, OpenAIError

from app.agents.planner import (
    PlanGenerationError,
    create_default_planner_input,
    create_planner_agent,
)
from app.llm.factory import MissingLLMConfigurationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a guarded TaskLoom ExecutionPlan with the configured LLM."
    )
    parser.add_argument("--goal", required=True, help="User goal to decompose.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        planner = create_planner_agent()
        plan = planner.plan(create_default_planner_input(args.goal))
    except MissingLLMConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except PlanGenerationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except APIStatusError as exc:
        print(
            f"LLM API request failed with HTTP {exc.status_code}. "
            "Check the Base URL, API Key, model access, and required network or VPN.",
            file=sys.stderr,
        )
        return 3
    except (OpenAIError, LangChainException) as exc:
        print(
            f"LLM request failed ({type(exc).__name__}). "
            "Check the provider configuration and network connection.",
            file=sys.stderr,
        )
        return 3

    print(plan.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
