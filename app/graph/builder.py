"""Build mock and real TaskLoom LangGraph workflows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.agents.planner import (
    PlannerAgent,
    create_default_planner_input,
    mock_planner_node,
)
from app.agents.reviewer import (
    ReviewerAgent,
    build_runtime_review_update,
    build_runtime_reviewer_input,
    reviewer_node,
    runtime_reviewer_node,
)
from app.agents.supervisor import runtime_supervisor_node, supervisor_node
from app.config import Settings, get_settings
from app.contracts.budget import ExecutionBudget
from app.contracts.planner import PlannerLimits
from app.graph.nodes import (
    aggregate_node,
    aggregate_runtime_node,
    apply_review_decision_node,
    validate_plan_node,
)
from app.graph.rehydration import (
    with_rehydrated_state,
    with_rehydrated_worker_input,
)
from app.graph.routes import (
    route_after_review_application,
    route_after_runtime_review,
    route_after_validation,
    route_runtime_supervisor,
    route_supervisor,
)
from app.graph.state import Stage3State, Stage6State, WorkflowEvent
from app.llm.factory import create_chat_model
from app.memory.consolidator import consolidate_state_memory
from app.observability import LLMTraceRecorder
from app.progress import run_with_heartbeat
from app.tools.registry import TOOL_REGISTRY
from app.tools.runtime import EXECUTABLE_TOOL_NAMES
from app.workers.factory import WorkerFactory
from app.workers.runtime import (
    ExecutionTracker,
    MockWorkerRuntime,
    WorkerFactoryRuntime,
)


def build_mock_graph(
    *,
    runtime: MockWorkerRuntime | None = None,
) -> tuple[Any, ExecutionTracker]:
    """Compile the fixed stage-three graph and expose its execution tracker."""

    worker_runtime = runtime or MockWorkerRuntime()
    builder = StateGraph(Stage3State)
    builder.add_node("mock_planner", mock_planner_node)
    builder.add_node("validate_plan", validate_plan_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("worker", worker_runtime.execute)
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("reviewer", reviewer_node)

    builder.add_edge(START, "mock_planner")
    builder.add_edge("mock_planner", "validate_plan")
    builder.add_conditional_edges(
        "validate_plan",
        route_after_validation,
        {
            "supervisor": "supervisor",
            "reviewer": "reviewer",
        },
    )
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        ["worker", "reviewer"],
    )
    builder.add_edge("worker", "aggregate")
    builder.add_edge("aggregate", "supervisor")
    builder.add_edge("reviewer", END)
    return builder.compile(name="taskloom-stage-3-mock"), worker_runtime.tracker


def create_initial_state(goal: str) -> Stage3State:
    """Create a complete reducer-safe state for one mock workflow run."""

    return {
        "run_id": str(uuid4()),
        "thread_id": "stage-3-demo",
        "session_id": "stage-3-demo",
        "user_id": "local-user",
        "original_goal": goal,
        "clarified_goal": goal,
        "plan_version": 0,
        "tasks": [],
        "agent_specs": [],
        "active_worker_results": [],
        "ready_task_ids": [],
        "reviewer_decision": None,
        "retry_count": 0,
        "replan_count": 0,
        "total_steps": 0,
        "budget": ExecutionBudget(),
        "memory_context": [],
        "final_output": None,
        "errors": [],
        "events": [],
        "execution_plan": None,
        "workflow_status": "created",
    }


def run_mock_workflow(
    goal: str = "比较 Redis、向量数据库和文件式 Agent Memory 方案",
    *,
    worker_delay_seconds: float = 0.05,
) -> tuple[Stage3State, ExecutionTracker]:
    """Run the complete deterministic workflow and return state plus evidence."""

    runtime = MockWorkerRuntime(delay_seconds=worker_delay_seconds)
    graph, tracker = build_mock_graph(runtime=runtime)
    result = graph.invoke(create_initial_state(goal))
    return result, tracker


def build_runtime_graph(
    *,
    planner: PlannerAgent | Any | None = None,
    reviewer: ReviewerAgent | Any | None = None,
    worker_factory: WorkerFactory | None = None,
    settings: Settings | None = None,
    progress: Callable[[str], None] | None = None,
    trace_recorder: LLMTraceRecorder | None = None,
    checkpointer: Any | None = None,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
    session_memory_store: Any | None = None,
) -> Any:
    """Compile the real workflow with Worker loops and optional checkpoints."""

    if planner is None or reviewer is None or worker_factory is None:
        resolved = settings or get_settings()
        chat_model = create_chat_model(resolved)
        if planner is None:
            planner = PlannerAgent.from_chat_model(
                chat_model,
                structured_output_method=resolved.llm_structured_output_method,
                trace_recorder=trace_recorder,
                model_name=resolved.llm_model,
                max_output_tokens=resolved.llm_planner_max_output_tokens,
            )
        if worker_factory is None:
            worker_factory = WorkerFactory(
                chat_model,
                structured_output_method=resolved.llm_structured_output_method,
                max_output_tokens=resolved.llm_worker_max_output_tokens,
                trace_recorder=trace_recorder,
                model_name=resolved.llm_model,
                context_window_tokens=resolved.llm_context_window_tokens,
                context_safety_margin_tokens=resolved.context_safety_margin_tokens,
            )
        if reviewer is None:
            reviewer = ReviewerAgent.from_chat_model(
                chat_model,
                structured_output_method=resolved.llm_structured_output_method,
                trace_recorder=trace_recorder,
                model_name=resolved.llm_model,
                max_output_tokens=resolved.llm_reviewer_max_output_tokens,
            )

    assert planner is not None
    assert reviewer is not None
    assert worker_factory is not None

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    worker_runtime = WorkerFactoryRuntime(worker_factory, progress=progress)

    def real_planner_node(state: Stage6State) -> dict:
        emit("Planner 开始调用 LLM 生成 ExecutionPlan（格式错误时最多尝试两次）")
        budget = state["budget"]
        limits = PlannerLimits(
            max_tasks=budget.max_workers,
            max_workers=budget.max_workers,
            max_concurrency=budget.max_concurrency,
            max_timeout_seconds=budget.max_worker_timeout_seconds,
            max_steps_per_worker=8,
            max_tokens_per_worker=budget.max_worker_token_budget,
        )
        planner_input = create_default_planner_input(
            state["original_goal"],
            limits=limits,
            recalled_memory=state.get("memory_context", []),
            replan_constraints=state.get("replan_constraints", []),
        )
        plan = run_with_heartbeat(
            lambda: planner.plan(planner_input),
            progress=progress,
            label="Planner LLM 请求",
        )
        emit(f"Planner 完成：生成 {len(plan.tasks)} 个 Task 和 {len(plan.agents)} 个 AgentSpec")
        return {
            "execution_plan": plan,
            "plan_version": state.get("plan_version", 0) + 1,
            "workflow_status": "running",
            "reviewer_decision": None,
            "review_feedback": {},
            "replan_constraints": [],
            "events": [
                WorkflowEvent(
                    event_type="plan.created",
                    message="Real Planner created an ExecutionPlan.",
                    data={"task_count": len(plan.tasks)},
                )
            ],
        }

    def runtime_supervisor_with_progress(state: Stage6State) -> dict:
        emit(
            "Supervisor 开始调度，"
            f"当前 total_steps={state.get('total_steps', 0)}"
        )
        update = runtime_supervisor_node(state)
        ready = update.get("ready_task_ids", [])
        if ready:
            emit("Supervisor 选中任务：" + ", ".join(ready))
        elif update.get("errors"):
            emit("Supervisor 停止调度：" + " | ".join(update["errors"]))
        else:
            emit("Supervisor 没有新的就绪任务，进入 Reviewer")
        return update

    def aggregate_with_progress(state: Stage6State) -> dict:
        update = aggregate_runtime_node(state)
        statuses = ", ".join(
            f"{task.task_id}={task.status}" for task in update["tasks"]
        )
        emit("Aggregate 已更新任务状态：" + statuses)
        if update.get("retry_count", state.get("retry_count", 0)) > state.get(
            "retry_count", 0
        ):
            emit("Aggregate 检测到可重试失败，任务将重新进入调度")
        return update

    def reviewer_with_progress(state: Stage6State) -> dict:
        emit("Reviewer 开始审核目标覆盖、结果质量和剩余预算")
        if state.get("execution_plan") is None or not state.get("tasks"):
            update = runtime_reviewer_node(state)
        else:
            review_input = build_runtime_reviewer_input(state)
            decision = run_with_heartbeat(
                lambda: reviewer.review(review_input),
                progress=progress,
                label="Reviewer LLM 请求",
            )
            update = build_runtime_review_update(state, decision)
        decision = update.get("reviewer_decision")
        if decision is not None and decision.action in {"finish", "replan", "fail"}:
            memory_context = consolidate_state_memory(
                state,
                decision,
                max_items=(settings.session_memory_max_items if settings else 50),
            )
            update["memory_context"] = memory_context
            if session_memory_store is not None:
                session_memory_store.save(state["session_id"], memory_context)
                emit(
                    "Session Memory 已写入 Redis："
                    f"session_id={state['session_id']}，items={len(memory_context)}"
                )
        emit(
            "Reviewer 结束："
            + (decision.action if decision is not None else "unknown")
        )
        return update

    builder = StateGraph(Stage6State)
    builder.add_node("planner", with_rehydrated_state(real_planner_node))
    builder.add_node("validate_plan", with_rehydrated_state(validate_plan_node))
    builder.add_node(
        "supervisor",
        with_rehydrated_state(runtime_supervisor_with_progress),
    )
    builder.add_node(
        "worker",
        with_rehydrated_worker_input(worker_runtime.execute),
    )
    builder.add_node("aggregate", with_rehydrated_state(aggregate_with_progress))
    builder.add_node("reviewer", with_rehydrated_state(reviewer_with_progress))
    builder.add_node(
        "apply_review",
        with_rehydrated_state(apply_review_decision_node),
    )

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "validate_plan")
    builder.add_conditional_edges(
        "validate_plan",
        with_rehydrated_state(route_after_validation),
        {"supervisor": "supervisor", "reviewer": "reviewer"},
    )
    builder.add_conditional_edges(
        "supervisor",
        with_rehydrated_state(route_runtime_supervisor),
        ["worker", "reviewer"],
    )
    builder.add_edge("worker", "aggregate")
    builder.add_edge("aggregate", "supervisor")
    builder.add_conditional_edges(
        "reviewer",
        with_rehydrated_state(route_after_runtime_review),
        {"apply_review": "apply_review", "end": END},
    )
    builder.add_conditional_edges(
        "apply_review",
        with_rehydrated_state(route_after_review_application),
        {"supervisor": "supervisor", "planner": "planner", "end": END},
    )
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        name="taskloom-stage-10-runtime",
    )


def create_runtime_initial_state(
    goal: str,
    *,
    thread_id: str | None = None,
    session_id: str | None = None,
    budget: ExecutionBudget | None = None,
    user_allowed_tools: list[str] | None = None,
    enabled_tools: list[str] | None = None,
) -> Stage6State:
    """Create a complete state for one checkpointable runtime workflow."""

    run_id = str(uuid4())
    resolved_thread_id = thread_id or run_id
    return {
        "run_id": run_id,
        "thread_id": resolved_thread_id,
        "session_id": session_id or resolved_thread_id,
        "user_id": "local-user",
        "original_goal": goal,
        "clarified_goal": goal,
        "plan_version": 0,
        "tasks": [],
        "agent_specs": [],
        "resolved_agent_specs": [],
        "active_worker_results": [],
        "ready_task_ids": [],
        "reviewer_decision": None,
        "retry_count": 0,
        "replan_count": 0,
        "total_steps": 0,
        "budget": budget or ExecutionBudget(),
        "memory_context": [],
        "user_allowed_tools": (
            list(TOOL_REGISTRY) if user_allowed_tools is None else user_allowed_tools
        ),
        "enabled_tools": (
            list(EXECUTABLE_TOOL_NAMES)
            if enabled_tools is None
            else enabled_tools
        ),
        "review_feedback": {},
        "replan_constraints": [],
        "final_output": None,
        "errors": [],
        "events": [],
        "execution_plan": None,
        "workflow_status": "created",
    }


def run_runtime_workflow(
    goal: str,
    *,
    planner: PlannerAgent | Any | None = None,
    reviewer: ReviewerAgent | Any | None = None,
    worker_factory: WorkerFactory | None = None,
    settings: Settings | None = None,
    progress: Callable[[str], None] | None = None,
    trace_recorder: LLMTraceRecorder | None = None,
) -> Stage6State:
    """Run the complete workflow with a process-local memory checkpointer."""

    from app.checkpoint import CheckpointSession, open_checkpoint_saver

    resolved_settings = settings or get_settings()
    with open_checkpoint_saver(resolved_settings) as (_, checkpointer):
        graph = build_runtime_graph(
            planner=planner,
            reviewer=reviewer,
            worker_factory=worker_factory,
            settings=resolved_settings,
            progress=progress,
            trace_recorder=trace_recorder,
            checkpointer=checkpointer,
            session_memory_store=None,
        )
        initial_state = create_runtime_initial_state(goal)
        return CheckpointSession(
            graph=graph,
            thread_id=initial_state["thread_id"],
        ).start(initial_state)
