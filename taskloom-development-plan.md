# TaskLoom 完整开发计划

## 1. 项目定位

TaskLoom 采用“固定核心 Agent + 动态 Worker Sub-Agent”的混合架构：

> 固定的 Planner、Supervisor、Reviewer 控制整个任务生命周期；Planner 生成受约束的任务与 Worker 配置，Supervisor 动态创建短生命周期 Worker 并按依赖并行调度，Reviewer 决定通过、定向重试或重新规划。

固定角色：

- Planner：拆解任务、定义依赖、生成 Worker 配置。
- Supervisor：检查依赖、分配权限、调度任务和控制预算。
- Reviewer：检查结果，决定通过、定向重试或重新规划。

动态角色：

- Worker Sub-Agent：按照具体子任务临时创建，完成后销毁独立上下文。
- Writer Worker：只有任务需要长篇报告时才创建。

这里的“销毁”表示：

- 不保留 Worker 独立会话；
- 不为 Worker 建立长期记忆；
- 释放本次执行上下文；
- 保留结构化结果、工具调用记录和审计日志；
- 下次任务即使角色同名，也创建新的 Worker 实例。

## 2. 核心架构原则

### 2.1 固定的是图，动态的是 Worker 配置

不建议在运行时反复修改 LangGraph 拓扑或添加新节点。图结构保持固定，动态的是传入通用 Worker 节点的 TaskSpec 和 SubAgentSpec。

    接收用户目标
        ↓
    加载会话与长期记忆
        ↓
    Planner 生成计划
        ↓
    Plan Guard 校验计划
        ├─ 非法 → 返回 Planner 修复
        └─ 合法
             ↓
    Supervisor 选择可执行任务
        ├─ 存在就绪任务 → 动态 Worker 并行执行
        │                       ↓
        │                  汇总 Worker 结果
        │                       ↓
        └────────────────── Supervisor
             ↓ 所有任务完成
    Reviewer 审核
        ├─ 通过 → Finalizer 输出结果
        ├─ 定向重试 → Supervisor
        ├─ 重新规划 → Planner
        └─ 超出预算 → 失败或降级结束

LangGraph 的 Send API 可以把不同输入并行发送给同一个 Worker 节点，再通过 reducer 聚合结果，适合实现动态 orchestrator-worker 工作流。

### 2.2 组件边界

| 组件 | 是否为 Agent | 是否固定 | 职责 |
|---|---:|---:|---|
| Planner | 是 | 固定 | 拆任务、定义依赖、提出 Worker 配置 |
| Plan Guard | 否 | 固定 | 校验计划、权限、依赖和预算 |
| Supervisor | 规则与 Agent 结合 | 固定 | 调度、授权工具、启动 Worker |
| Worker Runtime | 执行容器 | 固定 | 根据配置运行动态 Worker |
| Worker Sub-Agent | 是 | 动态 | 完成单个子任务，结束后销毁上下文 |
| Result Aggregator | 否 | 固定 | 汇总并行结果、更新任务状态 |
| Reviewer | 是 | 固定 | 通过、定向重试、重新规划 |
| Finalizer | 否 | 固定 | 包装已经审核通过的最终结果 |
| Writer Worker | 是 | 动态可选 | 需要长报告时生成最终内容 |

### 2.3 动态不等于无限自由

动态 Worker 必须遵守：

- 只能从 Capability Registry 中选择能力；
- 只能申请 Tool Registry 中存在的工具；
- 实际工具权限由 Supervisor 批准；
- 最大并发数、总 Worker 数、执行步数受到限制；
- 每个 Worker 有 Token 和时间预算；
- 输出必须符合注册的 Pydantic 协议；
- Worker 禁止继续创建 Worker；
- 写操作和外部副作用需要人工确认。

## 3. 核心数据模型

### 3.1 TaskSpec

任务和执行角色应分开定义：

    class TaskSpec(BaseModel):
        task_id: str
        title: str
        description: str
        task_type: str

        dependencies: list[str] = []
        priority: int = 0

        assigned_agent_id: str
        output_contract: str

        status: Literal[
            "pending",
            "ready",
            "running",
            "completed",
            "failed",
            "blocked",
            "skipped",
        ] = "pending"

        retry_count: int = 0
        max_retries: int = 2

### 3.2 SubAgentSpec

Planner 输出的是受约束的 Worker 配置：

    class SubAgentSpec(BaseModel):
        agent_id: str
        task_id: str

        role: str
        objective: str
        instructions: list[str]

        required_capabilities: list[str]
        requested_tools: list[str]
        knowledge_scope: list[str]

        output_contract: str

        timeout_seconds: int = 120
        max_steps: int = 8
        token_budget: int = 8000

        can_delegate: bool = False

这里使用 requested_tools，而不是直接相信 Planner 输出的 allowed_tools。最终工具权限由 Supervisor 计算：

    实际授权工具
    = Planner 请求工具
    ∩ 能力模板允许工具
    ∩ 当前用户权限
    ∩ 系统启用工具

执行时生成：

    class ResolvedSubAgentSpec(SubAgentSpec):
        granted_tools: list[str]

Planner 只能提出权限申请，不能自己批准权限。

### 3.3 输出协议注册表

第一版不让 Planner 任意生成 JSON Schema。自由 Schema 容易导致不受支持的字段类型、输出格式不稳定以及 Reviewer 无法统一审核。

采用注册表：

    OUTPUT_CONTRACTS = {
        "research_result_v1": ResearchResult,
        "comparison_result_v1": ComparisonResult,
        "experiment_plan_v1": ExperimentPlanResult,
        "report_result_v1": ReportResult,
    }

Planner 只能选择已有协议：

    output_contract: Literal[
        "research_result_v1",
        "comparison_result_v1",
        "experiment_plan_v1",
        "report_result_v1",
    ]

后续再增加受限制的动态 JSON Schema。

### 3.4 WorkerResult

    class WorkerResult(BaseModel):
        run_id: str
        task_id: str
        agent_id: str

        status: Literal[
            "completed",
            "failed",
            "timeout",
            "invalid_output",
            "permission_denied",
        ]

        output_contract: str
        output: dict | None

        sources: list[Source] = []
        error: str | None = None

        execution_time_ms: int
        token_usage: int
        tool_calls: list[ToolCallRecord] = []

### 3.5 ReviewerDecision

    class ReviewerDecision(BaseModel):
        action: Literal[
            "finish",
            "retry_tasks",
            "replan",
            "fail",
        ]

        score: float
        problems: list[str]
        suggestions: list[str]

        target_task_ids: list[str] = []
        replan_constraints: list[str] = []

### 3.6 AgentState

    class AgentState(TypedDict):
        run_id: str
        thread_id: str
        user_id: str

        original_goal: str
        clarified_goal: str

        plan_version: int
        tasks: list[TaskSpec]
        agent_specs: list[SubAgentSpec]

        worker_results: Annotated[list[WorkerResult], operator.add]
        ready_task_ids: list[str]

        reviewer_decision: ReviewerDecision | None

        retry_count: int
        replan_count: int
        total_steps: int

        budget: ExecutionBudget
        memory_context: list[MemoryItem]

        final_output: str | None
        errors: Annotated[list[str], operator.add]
        events: Annotated[list[WorkflowEvent], operator.add]

并行 Worker 会同时写入 worker_results，因此必须配置 reducer，防止后完成的 Worker 覆盖先完成的结果。

## 4. 能力、工具和 Worker 模板

### 4.1 Capability Registry

Planner 不能随意编造能力名称：

    CAPABILITY_REGISTRY = {
        "web_research",
        "document_analysis",
        "technical_analysis",
        "data_analysis",
        "comparison",
        "report_writing",
        "experiment_design",
    }

能力与工具映射：

    CAPABILITY_TOOL_MAP = {
        "web_research": {
            "web_search",
            "web_page_reader",
        },
        "document_analysis": {
            "knowledge_search",
            "document_reader",
        },
        "data_analysis": {
            "python_sandbox",
            "dataset_reader",
        },
        "report_writing": set(),
    }

### 4.2 Worker 模板

动态不代表每次从零生成完整系统提示词。建议定义基础模板：

- research_worker
- analysis_worker
- experiment_worker
- writing_worker
- general_worker

Planner 只输出模板名称和动态配置，例如：

    {
      "role_template": "research_worker",
      "role": "Redis 技术调研员",
      "objective": "调研 Redis 作为 Agent Memory 后端的实现方式与限制"
    }

最终 Worker Prompt 由以下内容拼接：

    固定安全规则
    + Worker 基础模板
    + 动态角色与目标
    + 当前子任务
    + 已授权工具说明
    + 知识范围
    + 输出协议
    + 时间和 Token 预算

## 5. 推荐项目目录

    TaskLoom/
    ├── app/
    │   ├── agents/
    │   │   ├── planner.py
    │   │   ├── supervisor.py
    │   │   ├── reviewer.py
    │   │   └── prompts/
    │   │       ├── planner.md
    │   │       ├── supervisor.md
    │   │       └── reviewer.md
    │   ├── workers/
    │   │   ├── factory.py
    │   │   ├── runtime.py
    │   │   ├── context.py
    │   │   └── templates/
    │   │       ├── research.md
    │   │       ├── analysis.md
    │   │       ├── experiment.md
    │   │       ├── writing.md
    │   │       └── general.md
    │   ├── graph/
    │   │   ├── builder.py
    │   │   ├── nodes.py
    │   │   ├── routes.py
    │   │   └── state.py
    │   ├── planning/
    │   │   ├── validator.py
    │   │   ├── dependency_graph.py
    │   │   └── cycle_detector.py
    │   ├── policies/
    │   │   ├── capability_registry.py
    │   │   ├── tool_policy.py
    │   │   ├── budget_policy.py
    │   │   └── permission_policy.py
    │   ├── contracts/
    │   │   ├── task.py
    │   │   ├── subagent.py
    │   │   ├── result.py
    │   │   ├── review.py
    │   │   └── output_registry.py
    │   ├── tools/
    │   │   ├── registry.py
    │   │   ├── web_search.py
    │   │   ├── document_reader.py
    │   │   └── knowledge_search.py
    │   ├── memory/
    │   │   ├── conversation.py
    │   │   ├── long_term.py
    │   │   ├── extractor.py
    │   │   ├── compressor.py
    │   │   └── retriever.py
    │   ├── persistence/
    │   │   ├── checkpoints.py
    │   │   ├── repositories.py
    │   │   └── models.py
    │   ├── events/
    │   │   ├── models.py
    │   │   └── publisher.py
    │   ├── api/
    │   │   ├── routes.py
    │   │   ├── schemas.py
    │   │   └── dependencies.py
    │   ├── llm/
    │   │   ├── client.py
    │   │   ├── structured.py
    │   │   └── factory.py
    │   ├── config.py
    │   └── main.py
    ├── tests/
    │   ├── unit/
    │   ├── integration/
    │   └── evaluation/
    ├── datasets/
    │   └── task_eval.jsonl
    ├── scripts/
    ├── docker-compose.yml
    ├── .env.example
    ├── pyproject.toml
    └── README.md

以上是最终结构，不需要一开始全部创建，应按阶段逐步增加。

## 6. 完整开发阶段

### 阶段 0：项目初始化

实现内容：

- 创建 Python 3.11 或 3.12 环境；
- 初始化 Git；
- 创建 app 和 tests；
- 配置 .env、.gitignore；
- 使用 pyproject.toml 管理依赖；
- 配置 pytest；
- 实现基础配置读取和日志。

第一批依赖：

    langgraph
    langchain-core
    pydantic
    pydantic-settings
    python-dotenv
    pytest
    pytest-asyncio

验收：

    python -m app.main
    pytest

建议提交：

    chore: initialize taskloom project

### 阶段 1：核心数据协议和注册表

实现：

- TaskSpec；
- SubAgentSpec；
- ResolvedSubAgentSpec；
- WorkerResult；
- ReviewerDecision；
- ExecutionBudget；
- AgentState；
- Capability Registry；
- Tool Registry；
- Output Contract Registry。

测试：

- 非法 capability 被拒绝；
- 不存在的 output contract 被拒绝；
- Worker 不能设置 can_delegate 为 true；
- 超出最大 timeout 被修正或拒绝；
- 重复 agent_id、task_id 被拒绝；
- 请求不存在的工具被拒绝。

验收：能够加载一份手写的完整计划 JSON，并通过 Pydantic 和系统策略校验。

### 阶段 2：Plan Guard

Planner 的内容不能直接执行，必须经过 Plan Guard。

校验内容：

1. task_id 是否唯一；
2. agent_id 是否唯一；
3. 每个 Task 是否对应一个 AgentSpec；
4. dependencies 是否引用有效任务；
5. 是否存在循环依赖；
6. 是否至少有一个可立即执行的任务；
7. capability 是否来自注册表；
8. 工具是否允许；
9. 输出协议是否存在；
10. timeout、step、token 是否超过系统限制；
11. Worker 是否试图继续委派；
12. Worker 数量是否超过上限。

错误处理：

    第一次非法
    → 把具体错误返回 Planner 修复

    第二次仍非法
    → 使用规则降级计划或终止任务

必须测试：

- A 依赖 B，B 依赖 A；
- Task 依赖不存在的 Task；
- 两个 Worker 使用相同 ID；
- Planner 申请高风险写权限；
- Planner 生成过多 Worker；
- Worker 尝试继续委派。

### 阶段 3：无 LLM 的最小工作流

全部使用固定模拟数据：

    START
    → mock_planner
    → validate_plan
    → supervisor
    → worker
    → aggregate
    → reviewer
    → END

固定计划：

    T1：调研 Redis Memory
    T2：调研向量记忆
    T3：调研文件式记忆
    T4：比较三种方案，依赖 T1/T2/T3
    T5：生成报告，依赖 T4

前三个任务通过 Send 并行发送给同一个 Worker 节点。

验收：

- T1/T2/T3 并行；
- T4 等待前三个任务完成；
- T5 等待 T4；
- 所有结果正确聚合；
- 工作流可以正常结束；
- 不接任何真实 LLM。

### 阶段 4：真实 Planner

Planner 输入：

    class PlannerInput(BaseModel):
        user_goal: str
        available_capabilities: list[str]
        available_output_contracts: list[str]
        limits: PlannerLimits
        recalled_memory: list[MemoryItem]

Planner 输出：

    class ExecutionPlan(BaseModel):
        goal_summary: str
        assumptions: list[str]
        tasks: list[TaskSpec]
        agents: list[SubAgentSpec]
        expected_deliverable: str

Planner 不允许：

- 直接调用工具；
- 执行子任务；
- 批准工具权限；
- 创建新的核心 Agent；
- 绕过 Plan Guard；
- 创建可以继续委派的 Worker；
- 修改系统预算上限。

验收任务：

1. 技术调研；
2. 论文调研；
3. 实验设计；
4. 数据分析；
5. 技术报告生成。

检查 Planner 是否能生成不同 Worker 组合。

### 阶段 5：Worker Factory 和隔离上下文

Worker Factory 输入：

    create_worker(
        task: TaskSpec,
        spec: ResolvedSubAgentSpec,
        dependency_results: list[WorkerResult],
        runtime_context: RuntimeContext,
    )

动态 Prompt：

    固定安全规则
    + Worker 基础模板
    + 动态角色与目标
    + 当前子任务
    + 依赖任务的结构化结果
    + 已授权工具
    + 知识范围
    + 输出协议
    + 时间和 Token 预算

Worker 只能看到：

- 当前 Task；
- 自己的 SubAgentSpec；
- 必要的依赖结果；
- 与任务相关的 Memory；
- 已授权工具。

Worker 不应看到：

- 整段主会话；
- 无关 Worker 的中间推理；
- 系统全部工具；
- 数据库密钥；
- 核心 Agent 的完整系统提示词。

生命周期：

    创建上下文
    → 执行
    → 校验结构化输出
    → 保存结果和审计记录
    → 释放上下文

### 阶段 6：Supervisor 调度器

Supervisor 使用“规则优先、LLM 辅助”。

规则负责：

- 依赖检查；
- 并发限制；
- 工具权限；
- 重试次数；
- timeout；
- Token 预算；
- 重复领取防护；
- 死锁检测。

LLM 可辅助：

- 多个 Worker 都能完成任务时选择更合适的配置；
- 失败后判断是否值得更换 Worker 配置；
- 判断是否需要降级执行方案。

调度逻辑：

    ready_tasks = [
        task
        for task in pending_tasks
        if all_dependencies_completed(task)
    ]

    按优先级排序
    → 应用最大并发数
    → 解析工具权限
    → 生成 ResolvedSubAgentSpec
    → Send 到 Worker 节点

第一版限制：

| 限制 | 建议值 |
|---|---:|
| 最大动态 Worker 数 | 8 |
| 最大并发数 | 3 |
| 单任务最多重试 | 2 |
| 最大重新规划次数 | 2 |
| 最大总步骤 | 30 |
| Worker 委派深度 | 0 |
| 单 Worker 超时 | 120 秒 |

### 阶段 7：Reviewer 闭环

审核维度：

| 维度 | 检查内容 |
|---|---|
| 完成度 | 是否覆盖用户目标 |
| 依赖完整性 | 是否所有必要任务都成功 |
| 输出合法性 | 是否符合 Output Contract |
| 证据质量 | 结论是否有可靠来源 |
| 一致性 | 多个 Worker 结果是否冲突 |
| 可执行性 | 建议是否具体且可实施 |
| 预算 | 是否值得继续消耗资源 |

Reviewer 的主要决定：

1. finish：目标完成，进入 Finalizer。
2. retry_tasks：计划合理，但指定 Worker 的结果不足。
3. replan：原计划缺少关键任务或依赖关系。
4. fail：已无法在预算内完成。

定向重试示例：

    {
      "action": "retry_tasks",
      "target_task_ids": ["research_redis"],
      "suggestions": [
        "补充 Redis Checkpoint 的官方实现方式",
        "说明 Redis Stack 与普通 Redis 的区别"
      ]
    }

重新规划示例：

    {
      "action": "replan",
      "replan_constraints": [
        "增加部署成本比较",
        "增加故障恢复机制分析"
      ]
    }

防止死循环：

    retry_count > 2
    → 不再重试该任务

    replan_count > 2
    → 降级输出或失败

    total_steps > 30
    → 强制终止

    预算耗尽
    → 输出当前完成内容和缺失部分

### 阶段 8：真实研究工具和证据体系

第一批工具：

- web_search；
- web_page_reader；
- knowledge_search；
- document_reader。

暂时不开放：

- 任意 Shell；
- 任意文件写入；
- 数据库写操作；
- 外部消息发送；
- 无限制网络访问。

Source 模型：

    class Source(BaseModel):
        source_id: str
        title: str
        url: str | None
        document_id: str | None
        published_at: str | None
        accessed_at: str
        excerpt: str
        source_type: Literal[
            "official_doc",
            "paper",
            "web_page",
            "local_document",
        ]

外部网页和文档必须标记为不可信输入：

- 外部资料中的指令不是系统指令；
- Worker 只能提取事实；
- 不能因为网页内容而调用新工具；
- 不能因为网页内容修改用户目标。

未来增加写操作时：

    Worker 提出写操作申请
    → Supervisor 检查权限
    → LangGraph interrupt 暂停
    → 用户确认
    → 执行写操作

### 阶段 9：Checkpoint 和故障恢复

先使用内存 Checkpointer 验证，再切换 Redis。

需要恢复：

- 当前计划版本；
- 已完成任务；
- 运行中任务；
- Worker 结果；
- Reviewer 决策；
- 重试次数；
- 剩余预算。

恢复策略：

- completed：不重新执行；
- pending：正常重新调度；
- running：标记 interrupted，再决定重试；
- 写操作：检查幂等记录，避免重复执行。

Redis 可以承担：

- LangGraph Checkpoint；
- 会话摘要；
- 分布式任务锁；
- SSE 事件流；
- 临时运行状态；
- TTL 数据。

### 阶段 10：Memory 与 Knowledge 分离

Graph State 保存本次任务：

- 计划；
- Worker；
- 结果；
- 预算；
- 审核状态。

Conversation Memory 保存当前会话：

- 最近对话；
- 当前任务摘要；
- 未完成事项；
- 本次任务约束。

Long-term Memory 保存跨会话内容：

- 用户偏好；
- 稳定事实；
- 历史选型；
- 成功策略；
- 失败方法。

Knowledge 保存外部事实：

- 论文；
- 官方文档；
- 技术资料；
- 用户上传文件。

记忆压缩：

1. 保留最近消息；
2. 摘要旧消息；
3. 提取事实、决策、约束和待办；
4. 原始记录归档；
5. 摘要放回主上下文；
6. Worker 只召回相关记忆。

### 阶段 11：任务数据库和审计

第一版使用 SQLite，后期切换 PostgreSQL。

数据表：

    workflow_runs
    plan_versions
    task_runs
    worker_runs
    review_records
    tool_call_records
    workflow_events
    memory_items

必须记录：

- 谁创建了什么 Worker；
- Planner 为什么创建它；
- Worker 获得了哪些工具；
- Worker 实际调用了什么；
- 执行时间和 Token 用量；
- 输出是否合法；
- Reviewer 为什么要求重试；
- 每次重规划修改了什么。

### 阶段 12：FastAPI 与 SSE

API：

    POST /api/runs
    GET  /api/runs/{run_id}
    GET  /api/runs/{run_id}/plan
    GET  /api/runs/{run_id}/tasks
    GET  /api/runs/{run_id}/events
    POST /api/runs/{run_id}/resume
    POST /api/runs/{run_id}/cancel
    POST /api/runs/{run_id}/confirm

SSE 事件：

    run.created
    plan.created
    plan.rejected
    plan.revised
    task.ready
    worker.created
    worker.started
    tool.called
    worker.completed
    worker.failed
    review.completed
    run.replanned
    run.completed
    run.failed

界面执行示例：

    Planner 创建 5 个任务
    → Supervisor 并行启动 3 个 Research Worker
    → Redis Worker 执行失败
    → Reviewer 要求定向重试
    → Supervisor 创建新的 Redis Worker
    → 比较 Worker 开始
    → 报告 Worker 完成
    → Reviewer 通过

## 7. 测试与评测计划

### 7.1 单元测试

- Pydantic 数据模型；
- 依赖环检测；
- 依赖解析；
- Capability Registry；
- 工具授权交集；
- Output Contract 校验；
- 预算扣减；
- Reviewer 路由；
- 并行结果 reducer。

### 7.2 集成测试

使用 Fake LLM 固定返回结果：

- 正常规划并完成；
- Planner 第一次生成非法计划；
- 一个 Worker 超时；
- 一个 Worker 输出格式错误；
- Reviewer 定向重试；
- Reviewer 触发重新规划；
- 中断后恢复；
- 多个 Worker 并行写结果。

### 7.3 评测任务集

建议准备约 30 条任务：

| 类型 | 数量 |
|---|---:|
| 并行技术调研 | 6 |
| 有依赖的长任务 | 6 |
| 论文研究 | 4 |
| 实验设计 | 4 |
| 数据分析 | 4 |
| 需要重试或重规划 | 4 |
| 工具故障和超时 | 2 |

### 7.4 指标

- 计划合法率；
- 任务完成率；
- 子任务成功率；
- 首次审核通过率；
- 平均 Worker 数；
- 平均重试次数；
- 平均重规划次数；
- 证据覆盖率；
- 结构化输出成功率；
- 中断恢复成功率；
- 平均耗时；
- Token 消耗；
- 并行加速比。

### 7.5 消融实验

- 固定 Worker 与动态 Worker；
- 无 Reviewer 与有 Reviewer；
- 只有 retry 与 retry 加 replan；
- 串行与并行；
- 无 Memory 与有 Memory；
- 无权限策略与有权限策略。

## 8. 从 CowAgent 借鉴的机制

| CowAgent 机制 | TaskLoom 的实现方式 |
|---|---|
| 短生命周期 Sub-Agent | Worker 使用单任务上下文，完成即销毁 |
| 隔离上下文 | Worker 只看到任务相关信息 |
| 并行执行 | LangGraph Send 并行 fan-out |
| 最大并发 | Supervisor 默认限制为 3 |
| 委派深度 | Worker 禁止继续委派 |
| 超时限制 | 每个 SubAgentSpec 携带 timeout |
| 自定义 Agent 类型 | 使用受控 Worker Template |
| 上下文压缩 | 摘要旧消息并提取约束 |
| 分层 Memory | 会话、长期记忆、Knowledge 分离 |
| 权限模式 | 工具白名单和人工确认 |

CowAgent 同时存在短生命周期 Sub-Agent 和常驻 Agent 团队。TaskLoom 当前主要借鉴前者，不照搬常驻 Agent 的独立工作区、会话和长期记忆。

暂时不照搬：

- 多聊天渠道；
- 桌面控制；
- 自动安装 Skills；
- 完整自我进化；
- 多层常驻 Agent 团队；
- 大量操作系统工具；
- 任意 Markdown 定义可执行 Agent。

这些功能会稀释 TaskLoom 的任务规划、动态调度和审核闭环。

## 9. 开发里程碑

| 里程碑 | 包含阶段 | 完成标志 |
|---|---|---|
| M1：可运行骨架 | 阶段 0～3 | 模拟动态 Worker 完成依赖任务 |
| M2：真实动态 Agent | 阶段 4～6 | Planner 能生成并调度不同 Worker |
| M3：质量闭环 | 阶段 7～8 | 支持证据、定向重试和重规划 |
| M4：可恢复系统 | 阶段 9～11 | Redis Checkpoint、Memory、审计 |
| M5：后端服务 | 阶段 12 | FastAPI、SSE、任务管理 |
| M6：项目完善 | 测试评测 | 指标、消融实验、README、演示 |

优先完成 M1～M3。此时项目的核心技术故事已经成立；Redis、Memory 和 API 属于后续增强，不能反过来拖慢多智能体闭环。

## 10. 实际编写顺序

接下来按照以下顺序开发：

1. 清理当前示例 main.py；
2. 初始化项目目录和依赖；
3. 编写六个核心数据模型；
4. 编写 Capability、Tool、Output Contract 注册表；
5. 编写 Plan Guard；
6. 手写一个 Memory 调研计划；
7. 用固定模拟 Worker 跑通第一版 LangGraph；
8. 为依赖调度、并行聚合和非法计划编写测试；
9. 骨架稳定后接入真实 LLM Planner；
10. 逐步加入 Worker Factory、Reviewer、工具、Redis、Memory 和 API。

第一轮完成后，系统应能读取一份手写计划，启动三个动态 Worker 并行执行，让比较 Worker 等待依赖，并由 Reviewer 给出结构化审核结论。这是 TaskLoom 的第一个可运行版本。

## 11. 参考资料

- CowAgent GitHub：https://github.com/zhayujie/CowAgent
- CowAgent 架构文档：https://docs.cowagent.ai/intro/architecture
- CowAgent Memory 文档：https://docs.cowagent.ai/memory/index
- LangGraph 官方文档：https://docs.langchain.com/oss/python/langgraph/
- LangGraph GitHub：https://github.com/langchain-ai/langgraph
- LangGraph Redis：https://github.com/redis-developer/langgraph-redis
