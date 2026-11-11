"""Command-line entry point for the checkpointed TaskLoom runtime graph."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from langchain_core.exceptions import LangChainException
from openai import APIStatusError, OpenAIError

from app.agents.planner import PlanGenerationError
from app.agents.reviewer import ReviewGenerationError
from app.checkpoint import (
    CheckpointSession,
    RedisCheckpointError,
    SessionCheckpointError,
    open_checkpoint_saver,
    open_redis_session_registry,
)
from app.config import get_settings
from app.graph.builder import build_runtime_graph, create_runtime_initial_state
from app.llm.factory import MissingLLMConfigurationError
from app.observability import LLMTraceRecorder, default_run_log_dir
from app.memory.store import (
    SessionMemoryError,
    open_redis_session_memory_store,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or resume the checkpointed TaskLoom outer workflow."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--goal", help="Goal for a new TaskLoom workflow.")
    target.add_argument(
        "--resume",
        metavar="THREAD_ID",
        help="Resume an existing Redis checkpoint thread.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Optional stable thread ID for a new workflow.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help=(
            "Optional front-end session ID. Redis keeps only this session's "
            "latest workflow thread for 24 hours and Session Memory for 72 "
            "hours after their respective last activity."
        ),
    )
    parser.add_argument(
        "--checkpoint-backend",
        choices=["memory", "redis"],
        default=None,
        help="Override TASKLOOM_CHECKPOINT_BACKEND for this invocation.",
    )
    parser.add_argument(
        "--pause-after",
        choices=[
            "planner",
            "validate_plan",
            "supervisor",
            "worker",
            "aggregate",
            "reviewer",
            "apply_review",
        ],
        default=None,
        help="Persist a checkpoint and exit after this outer graph node.",
    )
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
    parser.add_argument(
        "--checkpoint-demo",
        action="store_true",
        help=(
            "Pause once after plan validation, display checkpoint details, "
            "then resume in the same process."
        ),
    )
    return parser


def _print_progress(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[TaskLoom {timestamp}] {message}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    initial_state = None
    if args.goal is not None:
        initial_state = create_runtime_initial_state(
            args.goal,
            thread_id=args.thread_id,
            session_id=args.session_id,
            enabled_tools=[] if args.disable_tools else None,
        )
        thread_id = initial_state["thread_id"]
        run_id = initial_state["run_id"]
        goal = args.goal
    else:
        thread_id = args.resume
        run_id = thread_id
        goal = None
    log_dir = args.log_dir or default_run_log_dir(run_id)
    recorder = LLMTraceRecorder(log_dir, run_id=run_id)
    recorder.write_run_metadata(
        {
            "status": "running",
            "goal": goal,
            "thread_id": thread_id,
            "session_id": args.session_id,
            "resume": args.resume is not None,
            "checkpoint_backend": args.checkpoint_backend or settings.checkpoint_backend,
            "checkpoint_demo": args.checkpoint_demo,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "timeout_seconds": settings.llm_timeout_seconds,
            "max_retries": settings.llm_max_retries,
            "planner_max_output_tokens": settings.llm_planner_max_output_tokens,
            "reviewer_max_output_tokens": settings.llm_reviewer_max_output_tokens,
            "worker_max_output_tokens": settings.llm_worker_max_output_tokens,
            "cumulative_token_budget_enforced": False,
            "enabled_tools": (
                initial_state["enabled_tools"] if initial_state is not None else None
            ),
            "llm_calls_file": str(recorder.calls_path),
            "tool_calls_file": str(recorder.tool_calls_path),
        }
    )
    next_nodes: tuple[str, ...] = ()
    paused = False
    try:
        _print_progress("开始阶段 10 Session Memory 与上下文压缩工作流")
        _print_progress(f"run_id={run_id}")
        _print_progress(f"thread_id={thread_id}")
        _print_progress(f"本次运行日志目录：{recorder.run_dir}")
        with open_checkpoint_saver(
            settings,
            backend=args.checkpoint_backend,
        ) as (backend_name, checkpointer):
            _print_progress(f"Checkpoint 后端={backend_name}")
            if args.session_id is not None and backend_name != "redis":
                raise RedisCheckpointError(
                    "--session-id 需要使用 Redis Checkpoint 后端。"
                )
            registry_context = nullcontext(None)
            memory_store_context = nullcontext(None)
            if args.session_id is not None:
                assert settings.redis_url is not None
                registry_context = open_redis_session_registry(
                    settings.redis_url.get_secret_value(),
                    checkpointer=checkpointer,
                    ttl_minutes=settings.checkpoint_ttl_minutes,
                )
                memory_store_context = open_redis_session_memory_store(
                    settings.redis_url.get_secret_value(),
                    ttl_hours=settings.session_memory_ttl_hours,
                    max_items=settings.session_memory_max_items,
                )

            with registry_context as session_registry, memory_store_context as memory_store:
                pause_after = (
                    "validate_plan" if args.checkpoint_demo else args.pause_after
                )
                graph = build_runtime_graph(
                    settings=settings,
                    progress=_print_progress,
                    trace_recorder=recorder,
                    checkpointer=checkpointer,
                    interrupt_after=[pause_after] if pause_after else None,
                    session_memory_store=memory_store,
                    checkpoint_registry=session_registry,
                )
                session = CheckpointSession(graph=graph, thread_id=thread_id)
                if args.resume is not None:
                    if session_registry is not None:
                        session_registry.require_latest(args.session_id, thread_id)
                    snapshot = session.snapshot()
                    if not snapshot.values:
                        raise RedisCheckpointError(
                            f"没有找到 thread_id={thread_id} 的 Checkpoint。"
                        )
                    _print_progress(
                        "读取到持久化 Checkpoint："
                        f"下一节点={', '.join(snapshot.next) or '无'}"
                    )
                    state = session.resume()
                else:
                    assert initial_state is not None
                    if memory_store is not None:
                        recalled_memory = memory_store.load(args.session_id)
                        initial_state["memory_context"] = recalled_memory
                        _print_progress(
                            "Session Memory 已从 Redis 加载："
                            f"session_id={args.session_id}，items={len(recalled_memory)}"
                        )
                    if session_registry is None:
                        state = session.start(initial_state)
                    else:
                        session.initialize(initial_state)
                        previous_thread_id = session_registry.activate(
                            args.session_id,
                            thread_id,
                        )
                        if previous_thread_id and previous_thread_id != thread_id:
                            _print_progress(
                                "Session 已切换到最新 Thread，旧 Thread 已删除："
                                f"{previous_thread_id}"
                            )
                        state = session.resume()

                checkpoint_count = 0
                while args.checkpoint_demo and session.is_paused:
                    checkpoint_count += 1
                    snapshot = session.snapshot()
                    _print_progress(
                        f"Checkpoint 第 {checkpoint_count} 次暂停："
                        f"下一节点={', '.join(snapshot.next)}，"
                        f"plan_version={snapshot.values.get('plan_version', 0)}"
                    )
                    _print_progress("从 Checkpoint 恢复，已完成节点不会重复执行")
                    state = session.resume()

                if not args.checkpoint_demo and args.pause_after and session.is_paused:
                    snapshot = session.snapshot()
                    next_nodes = tuple(snapshot.next)
                    paused = True
                    _print_progress(
                        "工作流已持久化并暂停："
                        f"下一节点={', '.join(next_nodes)}"
                    )
                    resume_command = (
                        "恢复命令：python -m app.graph.runtime_cli "
                        f"--resume {thread_id}"
                    )
                    if args.session_id is not None:
                        resume_command += f" --session-id {args.session_id}"
                    _print_progress(resume_command + " --no-print-json")
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
    except (RedisCheckpointError, SessionCheckpointError, SessionMemoryError) as exc:
        recorder.update_run_metadata({"status": "checkpoint_error", "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 4
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
        "thread_id": state["thread_id"],
        "session_id": state["session_id"],
        "workflow_status": state["workflow_status"],
        "checkpoint_status": "paused" if paused else "completed",
        "next_nodes": list(next_nodes),
        "plan_version": state["plan_version"],
        "tasks": [task.model_dump(mode="json") for task in state["tasks"]],
        "active_worker_results": [
            result.model_dump(mode="json")
            for result in state["active_worker_results"]
        ],
        "reviewer_decision": (
            state["reviewer_decision"].model_dump(mode="json")
            if state.get("reviewer_decision") is not None
            else None
        ),
        "total_steps": state["total_steps"],
        "retry_count": state["retry_count"],
        "replan_count": state["replan_count"],
        "memory_context": [
            item.model_dump(mode="json") for item in state["memory_context"]
        ],
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
            "status": "paused" if paused else state["workflow_status"],
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
    if paused:
        return 0
    return 0 if state["workflow_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
