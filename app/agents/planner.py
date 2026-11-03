"""Deterministic mock Planner used by the stage-three workflow."""

from app.contracts.budget import ExecutionBudget
from app.contracts.plan import ExecutionPlan
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.graph.state import Stage3State, WorkflowEvent


def build_mock_execution_plan() -> ExecutionPlan:
    """Return the fixed five-task memory-backend comparison plan."""

    tasks = [
        TaskSpec(
            task_id="T1",
            title="调研 Redis Memory",
            description="总结 Redis 作为 Agent Memory 后端的特点",
            task_type="research",
            priority=10,
            assigned_agent_id="redis_researcher",
            output_contract="research_result_v1",
        ),
        TaskSpec(
            task_id="T2",
            title="调研向量记忆",
            description="总结向量数据库作为 Agent Memory 后端的特点",
            task_type="research",
            priority=10,
            assigned_agent_id="vector_researcher",
            output_contract="research_result_v1",
        ),
        TaskSpec(
            task_id="T3",
            title="调研文件式记忆",
            description="总结文件系统作为 Agent Memory 后端的特点",
            task_type="research",
            priority=10,
            assigned_agent_id="file_researcher",
            output_contract="research_result_v1",
        ),
        TaskSpec(
            task_id="T4",
            title="比较三种记忆方案",
            description="比较 Redis、向量数据库和文件系统方案",
            task_type="comparison",
            dependencies=["T1", "T2", "T3"],
            priority=5,
            assigned_agent_id="memory_comparator",
            output_contract="comparison_result_v1",
        ),
        TaskSpec(
            task_id="T5",
            title="生成记忆方案报告",
            description="根据比较结果生成结构化报告",
            task_type="report",
            dependencies=["T4"],
            priority=1,
            assigned_agent_id="report_writer",
            output_contract="report_result_v1",
        ),
    ]
    agents = [
        SubAgentSpec(
            agent_id="redis_researcher",
            task_id="T1",
            role="Redis Memory 调研员",
            objective="分析 Redis 记忆方案",
            instructions=["使用固定模拟资料输出结构化调研结果"],
            required_capabilities=["technical_analysis"],
            requested_tools=[],
            knowledge_scope=["Redis", "Agent Memory"],
            output_contract="research_result_v1",
        ),
        SubAgentSpec(
            agent_id="vector_researcher",
            task_id="T2",
            role="向量记忆调研员",
            objective="分析向量数据库记忆方案",
            instructions=["使用固定模拟资料输出结构化调研结果"],
            required_capabilities=["technical_analysis"],
            requested_tools=[],
            knowledge_scope=["Vector Database", "Agent Memory"],
            output_contract="research_result_v1",
        ),
        SubAgentSpec(
            agent_id="file_researcher",
            task_id="T3",
            role="文件式记忆调研员",
            objective="分析文件系统记忆方案",
            instructions=["使用固定模拟资料输出结构化调研结果"],
            required_capabilities=["technical_analysis"],
            requested_tools=[],
            knowledge_scope=["File Storage", "Agent Memory"],
            output_contract="research_result_v1",
        ),
        SubAgentSpec(
            agent_id="memory_comparator",
            task_id="T4",
            role="记忆方案比较员",
            objective="比较三种记忆后端",
            instructions=["只使用 T1、T2、T3 的结构化结果"],
            required_capabilities=["comparison"],
            requested_tools=[],
            knowledge_scope=["T1", "T2", "T3"],
            output_contract="comparison_result_v1",
        ),
        SubAgentSpec(
            agent_id="report_writer",
            task_id="T5",
            role="记忆方案报告撰写员",
            objective="生成最终结构化报告",
            instructions=["只使用 T4 的结构化比较结果"],
            required_capabilities=["report_writing"],
            requested_tools=[],
            knowledge_scope=["T4"],
            output_contract="report_result_v1",
        ),
    ]
    return ExecutionPlan(
        goal_summary="比较三种 Agent Memory 后端并生成报告",
        assumptions=["阶段 3 只运行固定模拟数据，不调用真实 LLM 或工具"],
        tasks=tasks,
        agents=agents,
        expected_deliverable="一份三种 Agent Memory 后端的结构化比较报告",
        budget=ExecutionBudget(max_workers=5, max_concurrency=3),
    )


def mock_planner_node(state: Stage3State) -> dict:
    """Create the fixed plan without calling an LLM."""

    plan = build_mock_execution_plan()
    return {
        "execution_plan": plan,
        "plan_version": state.get("plan_version", 0) + 1,
        "workflow_status": "running",
        "events": [
            WorkflowEvent(
                event_type="plan.created",
                message="Mock Planner created the fixed five-task plan.",
                data={"task_count": len(plan.tasks)},
            )
        ],
    }
