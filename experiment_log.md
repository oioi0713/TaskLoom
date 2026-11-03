# TaskLoom 实验日志

本文档用于记录 TaskLoom 每个版本新增的功能、验证结果和已知限制。

## v0.1.0——阶段 0：项目初始化

日期：2026-11-02

### 新增功能

| 功能模块 | 文件位置 | 作用和实现说明 |
|---|---|---|
| 项目与依赖配置 | `pyproject.toml` | 声明项目名称、版本、Python 3.11/3.12 版本范围、运行依赖和测试依赖；同时配置项目安装入口和 pytest。后续开发者只需执行一次安装命令即可获得统一依赖。 |
| 基础配置读取 | `app/config.py` | 使用 Pydantic Settings 从 `.env` 和 `TASKLOOM_` 前缀的系统环境变量读取配置，并校验环境名称、日志级别和最大 Worker 数，防止非法配置进入程序。`get_settings()` 会在同一进程中复用配置对象。 |
| 环境变量模板 | `.env.example` | 列出程序支持的基础环境变量及开发默认值，供开发者复制为本地 `.env`。真实 `.env` 被 Git 忽略，避免把密钥或本地配置上传到仓库。 |
| 控制台日志 | `app/logging.py` | 统一设置整个应用的日志级别、输出位置和格式。普通文本格式便于本地阅读；JSON 格式便于后续日志平台、审计模块或接口程序自动解析。异常发生时，JSON 日志也会记录异常信息。 |
| 应用主入口 | `app/main.py` | 负责读取配置、初始化日志并启动 TaskLoom。目前阶段 0 只输出初始化成功信息；后续 LangGraph 工作流将从这里接入。运行命令为 `python -m app.main`。 |
| 根目录兼容入口 | `main.py` | 将旧的根目录启动方式转发到 `app.main`，因此执行 `python main.py` 和 `python -m app.main` 会进入同一套启动逻辑，避免维护两个实现。 |
| 配置单元测试 | `tests/test_config.py` | 检查默认配置是否正确、环境变量是否可以覆盖默认值、非法 Worker 数量是否会被拒绝。 |
| 日志单元测试 | `tests/test_logging.py` | 检查指定日志级别是否生效，并验证 JSON 日志中包含时间、级别、记录器名称和消息字段。 |
| 启动单元测试 | `tests/test_main.py` | 调用应用主入口并检查返回值是否为 0，用于确认基础配置和日志初始化可以顺利完成。 |
| 项目说明 | `README.md` | 说明 Conda 和虚拟环境安装方式、应用启动命令、测试命令以及当前开发阶段。 |
| Git 仓库配置 | `.gitignore` | 忽略 `.env`、虚拟环境、Python 缓存、测试缓存、IDE 配置、运行日志和本地 development plan。Git 默认分支为 `main`。 |

### 当前模块关系

```text
.env / 系统环境变量
        ↓
app/config.py 读取并校验配置
        ↓
app/logging.py 根据配置初始化日志
        ↓
app/main.py 启动应用并输出初始化日志

tests/test_config.py   验证配置模块
tests/test_logging.py  验证日志模块
tests/test_main.py     验证完整启动过程
```

### 验证结果

- 运行环境：`TaskLoom` Conda 环境，Python 3.11.18。
- 应用启动：执行 `python -m app.main` 成功。
- 测试结果：共 6 个测试，全部通过。
- GitHub 同步：首次推送后，本地 `main` 与远程 `origin/main` 的提交一致。

### 已知限制

- 当前版本只完成开发计划阶段 0，提供可运行的项目骨架。
- 尚未实现核心数据协议、注册表、Plan Guard、LangGraph 工作流和真实 Agent。
- 当前 Conda 环境中存在与 TaskLoom 无关的旧包依赖缺失，但不会影响阶段 0 的应用启动和测试。

## v0.2.0——阶段 1：核心数据协议和注册表

日期：2026-11-02

### 新增功能

| 功能模块 | 文件位置 | 作用和实现说明 |
|---|---|---|
| Task 任务协议 | `app/contracts/task.py` | 定义单个任务必须包含的字段、允许状态、重试限制和基础字段校验，使 Planner、Supervisor 和 Worker 使用同一种任务格式。 |
| Worker 配置协议 | `app/contracts/subagent.py` | 定义 Planner 申请的 Worker 配置以及 Supervisor 授权后的配置；限制 Worker 的能力、工具、超时、步骤、Token 和委派权限。 |
| Worker 结果协议 | `app/contracts/result.py` | 统一记录 Worker 的执行状态、结构化输出、来源、错误、耗时、Token 用量和工具调用记录。 |
| Reviewer 决策协议 | `app/contracts/review.py` | 统一 Reviewer 的完成、定向重试、重新规划和失败决定，并记录问题与修改建议。 |
| 执行预算协议 | `app/contracts/budget.py` | 限制 Worker 总数、最大并发数、重试次数、重新规划次数、总步骤、单任务超时和 Token 预算。 |
| 完整计划协议 | `app/contracts/plan.py` | 将任务、Worker 配置、交付物和预算组合成一份完整计划，并检查重复的 `task_id` 和 `agent_id`。 |
| AgentState | `app/graph/state.py` | 定义未来 LangGraph 节点之间共享的状态；并行 Worker 的结果、错误和事件使用 reducer 追加，避免互相覆盖。 |
| Capability Registry | `app/policies/capability_registry.py` | 保存系统允许使用的能力名称以及每种能力允许申请的工具，防止 Planner 编造能力。 |
| Tool Registry | `app/tools/registry.py` | 保存系统已知工具的名称、用途和风险类型。阶段 1 只登记工具元数据，尚未实现真实工具调用。 |
| 工具授权策略 | `app/policies/tool_policy.py` | 根据 Planner 请求、能力允许范围、用户权限和系统启用状态计算最终授权工具。 |
| Output Contract Registry | `app/contracts/output_registry.py` | 保存调研、比较、实验计划和报告四种标准输出协议，并用对应 Pydantic 模型校验 Worker 输出。 |

### 标准 Task 格式

每个任务必须符合下面的 JSON 结构：

```json
{
  "task_id": "research_redis",
  "title": "调研 Redis Memory",
  "description": "收集 Redis 用于 Agent Memory 的实现方式、优势和限制",
  "task_type": "research",
  "dependencies": [],
  "priority": 10,
  "assigned_agent_id": "redis_researcher",
  "output_contract": "research_result_v1",
  "status": "pending",
  "retry_count": 0,
  "max_retries": 2
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `task_id` | 字符串 | 是 | 任务唯一标识。必须以英文字母开头，只能包含英文字母、数字、下划线和连字符，最大长度为 64。 |
| `title` | 字符串 | 是 | 简短的任务名称，不能为空。 |
| `description` | 字符串 | 是 | 任务需要完成的具体内容，不能为空。 |
| `task_type` | 字符串 | 是 | 任务类别，例如 `research`、`comparison` 或 `report`。 |
| `dependencies` | 字符串列表 | 否 | 当前任务依赖的其他 `task_id`。默认为空列表，同一依赖不能重复，任务不能依赖自身。 |
| `priority` | 整数 | 否 | 调度优先级，数值越大表示越优先。默认为 0。 |
| `assigned_agent_id` | 字符串 | 是 | 负责该任务的 Worker 配置 ID，格式规则与 `task_id` 相同。 |
| `output_contract` | 字符串 | 是 | 任务必须返回的输出协议，只能选择注册表中已有的协议。 |
| `status` | 字符串 | 否 | 任务状态，默认为 `pending`。 |
| `retry_count` | 整数 | 否 | 当前已经重试的次数，默认为 0，不能超过 `max_retries`。 |
| `max_retries` | 整数 | 否 | 任务最多允许重试的次数，默认为 2，当前系统上限也是 2。 |

允许的任务状态：

```text
pending、ready、running、completed、failed、blocked、skipped
```

允许的输出协议：

```text
research_result_v1
comparison_result_v1
experiment_plan_v1
report_result_v1
```

### 当前测试检查范围

测试使用 `tests/fixtures/valid_plan.json` 中的手写计划，而不是读取 development plan Markdown 文档。Pydantic 会遍历 `tasks` 列表，并将每个元素分别校验为 `TaskSpec`。

阶段 1 已检查：

- Task 必填字段、字段类型和 ID 格式；
- Task 状态和输出协议是否合法；
- 重试次数是否超过限制；
- Task 是否依赖自身或重复声明同一依赖；
- 完整计划中是否存在重复的 `task_id`；
- Worker 的必填字段、能力、工具、超时、步骤和 Token 是否合法；
- Worker 是否尝试把 `can_delegate` 设置为 `true`；
- 完整计划中是否存在重复的 `agent_id`；
- 完整手写计划 JSON 是否可以成功加载。

阶段 2 的 Plan Guard 将继续检查：

- `dependencies` 中引用的任务是否真实存在；
- 任务之间是否存在循环依赖；
- 每个 Task 是否准确对应一个 Worker；
- Task 和 Worker 的输出协议是否一致；
- 是否至少存在一个可以立即执行的任务；
- 整个依赖图是否能够正常执行完成。

可以简单理解为：

```text
阶段 1：检查每张任务表填写得是否规范。
阶段 2：检查这些任务组合起来是否真的可以执行。
```

### 验证结果

- 应用启动：执行 `python -m app.main` 成功。
- 完整计划：成功加载 2 个 Task 和 2 个 Worker 配置。
- 测试结果：共 22 个测试，全部通过。

### 已知限制

- 当前只完成数据格式和注册表级校验，尚未实现阶段 2 的完整 Plan Guard。
- Tool Registry 当前只包含工具元数据，没有接入真实工具实现。
- 当前没有真实 Planner、Supervisor、Worker 或 Reviewer Agent。

## v0.3.0——阶段 2：Plan Guard

日期：2026-11-03

### 新增功能

| 功能模块 | 文件位置 | 作用和实现说明 |
|---|---|---|
| Plan Guard | app/planning/validator.py | 作为 Planner 输出进入执行系统前的统一安全入口，依次执行高风险权限预扫描、阶段 1 数据协议校验和阶段 2 计划关系校验，并返回结构化错误。 |
| 依赖图 | app/planning/dependency_graph.py | 将 Task 列表转换为 task_id → dependencies 关系，查找不存在的依赖，并计算当前可以立即执行的任务。 |
| 循环依赖检测 | app/planning/cycle_detector.py | 使用深度优先搜索检测循环依赖，并返回一条闭合循环路径，例如 task_a → task_b → task_a。 |
| 高风险工具名单 | app/policies/tool_policy.py | 标记 Shell、文件写入、数据库写入、外部消息等当前阶段禁止申请的工具。 |
| Plan Guard 测试 | tests/unit/test_plan_guard.py | 测试 Plan Guard 的完整调用流程、错误代码以及 accept、revise、degrade、terminate 决策。 |
| 依赖算法测试 | tests/unit/test_dependency_graph.py | 单独测试依赖图构建、缺失依赖、循环检测和就绪任务排序等底层算法。 |

### 阶段 1 与阶段 2 的调用关系

阶段 1 和阶段 2 不是由最外围分别调用。外部系统只调用 Plan Guard，由 Plan Guard 在内部依次调用阶段 1 数据协议和阶段 2 依赖关系检查。

~~~text
Planner 原始 JSON
        ↓
PlanGuard.validate()
        ↓
高风险工具预扫描
        ↓
阶段 1：ExecutionPlan.model_validate()
        ├─ TaskSpec
        ├─ SubAgentSpec
        ├─ ExecutionBudget
        └─ Capability、Tool、Output Contract Registry
        ↓
阶段 1 是否通过？
        ├─ 否 → 整理格式错误并返回，不执行依赖图检查
        └─ 是
             ↓
阶段 2：依赖与计划关系检查
        ├─ dependency_graph
        ├─ cycle_detector
        ├─ Task 与 Worker 对应关系
        └─ Worker 数量和计划预算
             ↓
返回 PlanValidationReport
~~~

采用统一入口的原因是：阶段 2 必须建立在字段已经通过阶段 1 校验的基础上。例如依赖图可以确定 dependencies 一定是字符串列表，而不是缺失字段、数字或 null。这样可以防止调用者忘记执行阶段 1，直接将非法 JSON 送入依赖算法。

职责可以概括为：

~~~text
阶段 1：检查每张任务表填写得是否规范。
阶段 2：检查这些任务组合起来是否真的可以执行。
~~~

### Plan Guard 检测内容

#### 输入和数据格式

- Planner 输出是否为合法 JSON 对象；
- task_id 和 agent_id 是否重复；
- Task 是否依赖自身；
- capability、tool 和 output contract 是否已经注册；
- Worker 是否申请了能力不允许使用的工具；
- Worker 是否尝试继续委派；
- timeout、step 和 token 是否超过系统上限；
- Worker 数量是否超过系统硬上限。

#### 权限安全

当前明确禁止 Planner 申请：

~~~text
shell
file_writer
database_writer
message_sender
external_action
~~~

这些工具会产生 high_risk_tool_requested 错误，未注册的工具还会同时产生 unknown_tool 错误。

#### 任务依赖

- dependencies 中引用的 Task 是否存在；
- Task 之间是否存在循环依赖；
- 是否至少有一个可以立即执行的 Task；
- 初始就绪任务是否按照优先级和 ID 稳定排序。

#### Task 与 Worker 对应关系

- Task 的 assigned_agent_id 是否存在；
- 每个 Task 是否恰好对应一个 AgentSpec；
- Worker 是否引用了不存在的 Task；
- Task 指定的 Worker 是否声明负责同一个 Task；
- Task 和 Worker 是否使用相同的输出协议。

#### Worker 数量

~~~text
too_many_workers
→ Worker 数量超过系统硬上限 8

worker_count_exceeded
→ Worker 数量未超过 8，但超过本次 ExecutionBudget.max_workers
~~~

### 错误处理流程

validate() 负责找出问题，evaluate() 根据问题类型和失败次数决定下一步。

~~~text
计划合法
→ action = accept

第一次计划非法
→ action = revise
→ 返回错误代码、字段位置和具体反馈，让 Planner 修复

第二次仍然非法
├─ 只有工具、委派、timeout、step 或 token 问题
│  → action = degrade
│  → 删除危险工具、关闭委派并压缩资源上限
└─ 循环依赖、缺失依赖等结构性问题
   → action = terminate
   → 不擅自修改任务结构
~~~

### 为什么依赖图和 Plan Guard 分开测试

test_plan_guard.py 确实会间接检测依赖图，但两个测试文件验证的层级不同。

| 测试文件 | 测试对象 | 主要检查内容 |
|---|---|---|
| tests/unit/test_dependency_graph.py | 底层纯算法 | 依赖图能否正确构建、缺失依赖能否发现、循环路径是否正确、就绪任务排序是否稳定。 |
| tests/unit/test_plan_guard.py | Plan Guard 统一入口 | 是否正确调用数据协议和依赖算法、是否返回正确错误代码，以及最终路由决定是否正确。 |

可以简单理解为：

~~~text
test_dependency_graph.py
→ 测试底层零件本身是否正确

test_plan_guard.py
→ 测试零件组装后，整个安全检查流程是否正确
~~~

如果只保留 Plan Guard 测试，出现失败时难以快速判断是依赖图构建、循环算法、调用关系还是错误转换出了问题。分层测试可以更快定位故障，而且依赖图算法后续还会被 Supervisor 调度器复用。

### 验证结果

- Python 编译检查通过；
- 阶段 0 应用启动未受影响；
- 有效手写计划通过 Plan Guard，结果为 action=accept；
- 有效计划结果：executable=True，错误数量为 0；
- 全量测试：共 39 个测试，全部通过。

### 已知限制

- 当前 Plan Guard 只负责计划进入执行系统前的同步校验，还没有接入真实 Planner 或 LangGraph 节点；
- 安全降级只处理工具、委派和资源上限，不会自动修改依赖关系；
- 当前没有执行 Worker，因此尚未验证运行过程中的动态死锁和失败恢复；
- Supervisor 调度和并行 Worker 执行将在后续阶段实现。

## v0.4.0——阶段 3：无 LLM 的最小 LangGraph 工作流

日期：2026-11-03

### 新增功能

阶段 3 将阶段 1 的数据协议和阶段 2 的 Plan Guard 接入真正的 LangGraph 工作流，但所有计划和任务结果仍使用固定模拟数据，不调用真实 LLM 或外部工具。

~~~text
START
→ mock_planner
→ validate_plan
→ supervisor
→ worker
→ aggregate
→ supervisor
→ reviewer
→ END
~~~

固定任务为：

~~~text
T1：调研 Redis Memory
T2：调研向量记忆
T3：调研文件式记忆
T4：比较三种方案，依赖 T1、T2、T3
T5：生成报告，依赖 T4
~~~

### LangGraph 节点说明

节点通过下面的形式注册：

~~~python
builder.add_node("节点名称", 节点执行函数)
~~~

add_node() 只负责注册节点，不会立即执行函数。图开始运行后，LangGraph 才按照边和条件路由调用节点。

| 节点 | 执行函数 | 文件位置 | 作用 |
|---|---|---|---|
| mock_planner | mock_planner_node | app/agents/planner.py | 模拟 Planner，生成固定的 T1～T5 计划，不调用 LLM。 |
| validate_plan | validate_plan_node | app/graph/nodes.py | 调用阶段 2 的 Plan Guard，只有合法计划才能进入 Supervisor。 |
| supervisor | supervisor_node | app/agents/supervisor.py | 根据已完成任务和 dependencies 选择当前可以执行的任务，并限制最大并发数。 |
| worker | worker_runtime.execute | app/workers/runtime.py | 执行一个动态传入的 Task。图中只有一个 Worker 节点定义，但可以通过 Send 并行调用多次。 |
| aggregate | aggregate_node | app/graph/nodes.py | 根据 reducer 已经合并的 WorkerResult 更新 Task 状态，然后返回 Supervisor 继续调度。 |
| reviewer | reviewer_node | app/agents/reviewer.py | 检查 T1～T5 是否全部成功，生成 finish 或 fail 决定并结束工作流。 |

需要注意：LangGraph Node 不一定是 Agent。当前 mock_planner、worker 和 reviewer 代表 Agent 角色；validate_plan 和 aggregate 是确定性数据处理节点；supervisor 当前也是规则调度节点。

### Send 并行代码位置

Send 的动态路由代码位于：

~~~text
app/graph/routes.py
route_supervisor()
~~~

核心实现为：

~~~python
for task_id in ready_task_ids:
    task = tasks_by_id[task_id]
    dependency_results = [
        results_by_task_id[dependency]
        for dependency in task.dependencies
        if dependency in results_by_task_id
    ]
    sends.append(
        Send(
            "worker",
            {
                "run_id": state["run_id"],
                "task": task,
                "spec": specs_by_task_id[task_id],
                "dependency_results": dependency_results,
            },
        )
    )

return sends
~~~

项目中没有逐行写死：

~~~python
Send("worker", T1的数据)
Send("worker", T2的数据)
Send("worker", T3的数据)
~~~

第一次进入 route_supervisor() 时，Supervisor 已经计算出：

~~~python
ready_task_ids = ["T1", "T2", "T3"]
~~~

因此循环实际生成三个 Send：

~~~text
Send("worker", T1 的 Task、Worker 配置和依赖结果)
Send("worker", T2 的 Task、Worker 配置和依赖结果)
Send("worker", T3 的 Task、Worker 配置和依赖结果)
~~~

LangGraph 收到三个 Send 后，会并行调用三次同一个 worker 节点。worker 节点在 app/graph/builder.py 中注册：

~~~python
builder.add_node("worker", worker_runtime.execute)
~~~

所以每个 Send 最终都会调用：

~~~python
worker_runtime.execute(本次 Send 携带的数据)
~~~

三次调用使用同一个执行函数，但 task、spec 和 dependency_results 不同。固定的是图和 Worker 节点，动态的是传入 Worker 的任务配置。

### Reducer 与 Aggregate 的区别

Stage3State 中的 worker_results 使用 reducer：

~~~python
worker_results: Annotated[list[WorkerResult], operator.add]
~~~

T1、T2、T3 并行返回后，reducer 负责将三个列表相加：

~~~text
[WorkerResult(T1)]
+ [WorkerResult(T2)]
+ [WorkerResult(T3)]
= [WorkerResult(T1), WorkerResult(T2), WorkerResult(T3)]
~~~

aggregate 节点不负责底层列表合并，而是读取已经合并的 WorkerResult，将对应 Task 的状态更新为 completed，再回到 Supervisor。

~~~text
reducer
→ 合并并行 WorkerResult，避免结果互相覆盖

aggregate
→ 根据 WorkerResult 更新 Task 状态
~~~

### 实际调度顺序

~~~text
T1 started
T2 started
T3 started

T1、T2、T3 finished

T4 started
T4 finished

T5 started
T5 finished

Reviewer → finish
~~~

T4 只有在 T1、T2、T3 全部完成后才会进入 ready 状态；T5 只有在 T4 完成后才能运行。

### 验证结果

- T1、T2、T3 通过 Send 并行发送到同一个 Worker 节点；
- 实测峰值并发 Worker 数为 3；
- T4 等待 T1、T2、T3 全部完成；
- T5 等待 T4 完成；
- 5 个 WorkerResult 通过 reducer 正确聚合，没有重复或覆盖；
- Reviewer 返回 action=finish，工作流状态为 completed；
- 所有 WorkerResult 的 token_usage 均为 0；
- 所有 WorkerResult 的 tool_calls 均为空；
- 原有 python -m app.main 启动正常；
- 全量测试：共 45 个测试，全部通过。

### 已知限制

- mock_planner 使用固定计划，没有接入真实 LLM；
- Worker 返回固定模拟数据，没有生成式推理；
- 当前 Worker 不调用 Tool Registry 中的真实工具；
- Reviewer 只检查五个固定任务是否完成；
- 当前没有 Checkpoint，中断后不能恢复；
- 当前没有真实重试、重新规划或运行时超时控制。

## v0.5.0——阶段 4：接入真实 LLM Planner

### 阶段目标

阶段 4 将阶段 3 中返回固定计划的 `mock_planner` 扩展为可以调用真实大模型的 Planner。用户输入一个目标后，Planner 负责生成结构化的 `ExecutionPlan`，但大模型不能直接决定系统权限和资源上限；生成结果仍必须经过阶段 1 的数据协议和阶段 2 的 Plan Guard，只有校验通过的计划才会输出。

当前阶段 3 的固定模拟工作流仍然保留，真实 Planner 通过单独的命令行入口运行，避免影响原有演示和测试。

### 新增和修改的模块

| 模块 | 文件位置 | 作用 |
|---|---|---|
| LLM 配置 | `app/config.py` | 从本地 `.env` 读取模型名称、API Key、Base URL、温度、超时时间和结构化输出方式。API Key 使用 `SecretStr` 保存，避免在日志或对象打印时直接泄露。 |
| 模型工厂 | `app/llm/factory.py` | 根据配置创建 OpenAI Compatible 的 `ChatOpenAI`。这里只负责创建模型客户端，不负责规划逻辑。 |
| Planner 输入协议 | `app/contracts/planner.py` | 定义用户目标、允许使用的能力、允许使用的输出协议、系统资源上限和召回记忆。模型只能在这些明确提供的范围内规划。 |
| Memory 输入协议 | `app/contracts/memory.py` | 定义可以提供给 Planner 的受限记忆记录。记忆只作为上下文，不具有修改系统规则的权限。 |
| Planner 提示词 | `app/agents/prompts/planner.md` | 规定 Planner 的职责、安全边界、任务拆分要求和输出要求。 |
| 真实 Planner | `app/agents/planner.py` | 调用支持结构化输出的模型生成 `ExecutionPlan`，覆盖模型生成的预算，再交给 Plan Guard 检查；第一次失败时将错误反馈给模型，最多生成两次。 |
| 命令行入口 | `app/agents/planner_cli.py` | 接收 `--goal`，运行真实 Planner，并在成功后将计划输出为格式化 JSON；配置、网络或模型调用失败时返回明确错误信息。 |

### 真实 Planner 的调用流程

~~~text
用户输入 goal
→ create_default_planner_input()
→ 加入系统允许的能力、工具、输出协议和资源上限
→ create_chat_model()
→ 调用学校的 OpenAI Compatible API
→ 模型按 ExecutionPlan 结构生成计划
→ 使用系统值覆盖模型返回的 budget
→ Plan Guard 校验数据格式、依赖关系、工具权限和执行安全性
→ 校验通过：输出 JSON
→ 校验失败：把错误反馈给模型并重新生成一次
→ 第二次仍失败：停止并返回 PlanGenerationError
~~~

这里的关键点是：大模型只提出计划，不能绕过阶段 1 和阶段 2 的限制。最终输出的计划一定已经转换为 `ExecutionPlan`，并通过 Plan Guard。

### 学校模型配置

本地 `.env` 使用 OpenAI Compatible 配置，真实 API Key 只保存在 `.env` 中，不写入本文档，也不应提交到 Git。

~~~dotenv
TASKLOOM_LLM_PROVIDER=openai_compatible
TASKLOOM_LLM_MODEL=DeepSeek-V4.1-Flash
TASKLOOM_LLM_API_KEY=<本地 API Key>
TASKLOOM_LLM_BASE_URL=https://agi.tongji.edu.cn/v1
TASKLOOM_LLM_TEMPERATURE=0
TASKLOOM_LLM_TIMEOUT_SECONDS=60
TASKLOOM_LLM_STRUCTURED_OUTPUT_METHOD=function_calling
~~~

项目实际请求地址为：

~~~text
https://agi.tongji.edu.cn/v1/chat/completions
~~~

`function_calling` 会在学校示例的基础请求中增加结构化输出所需的 `tools` 和 `tool_choice` 等字段，使模型按照 `ExecutionPlan` 协议返回数据。使用学校示例中的最小 Chat Completions 请求测试时也曾返回相同的 HTTP 403，因此此前失败并不是这些结构化字段造成的。

### 真实运行命令

在 PowerShell 中进入项目目录后执行：

~~~powershell
conda activate TaskLoom

$env:HTTP_PROXY=$null
$env:HTTPS_PROXY=$null
$env:ALL_PROXY=$null
$env:PYTHONIOENCODING="utf-8"

python -m app.agents.planner_cli --goal "调研 LangGraph 的核心概念并生成一份结构化学习计划"
~~~

成功时程序会输出一个完整 JSON，主要字段包括：

~~~text
goal_summary
assumptions
tasks
agents
expected_deliverable
budget
~~~

本次真实运行成功生成了 4 个任务：先调研 LangGraph 核心概念，再进行技术分析和同类框架比较，最后汇总为学习计划。任务之间包含明确依赖关系，每个任务都关联对应的 Agent、能力、工具和输出协议。

### 代理残留问题

关闭代理软件或关闭 Windows 系统代理后，已经启动的终端、IDE 或 Codex 进程仍可能保留旧的环境变量。本次检查发现当前进程中仍存在：

~~~text
HTTP_PROXY=http://127.0.0.1:7892
HTTPS_PROXY=http://127.0.0.1:7892
ALL_PROXY=http://127.0.0.1:7892
~~~

而 Windows 的 WinHTTP 和用户系统代理均已关闭。这说明“代理软件已关闭”和“当前 Python 进程不走代理”不是同一件事。`ChatOpenAI` 底层的 HTTP 客户端默认会读取这些环境变量，因此仍可能经过代理访问学校接口，随后收到 HTTP 403 和“当前网络环境不支持”。

运行前将三个代理环境变量临时设为 `$null` 后，本次真实模型调用成功。该操作只影响当前 PowerShell 会话，不会修改系统代理设置。

### 中文输出编码

真实 Planner 返回的 JSON 本身是合法 UTF-8。若 Windows 终端显示中文乱码，可在运行前设置：

~~~powershell
$env:PYTHONIOENCODING="utf-8"
~~~

乱码属于终端解码问题，不代表模型返回内容或 JSON 结构错误。

### 验证结果

- 使用 `TaskLoom` Conda 环境成功启动真实 Planner；
- 成功调用 `DeepSeek-V4.1-Flash`；
- 成功返回包含 `tasks`、`agents` 和 `budget` 的完整结构化计划；
- 程序退出码为 0；
- 输出由 `model_dump_json(indent=2)` 生成，是合法 JSON；
- 计划已通过 Pydantic 数据协议和 Plan Guard；
- 关闭当前进程中的代理环境变量后，学校接口可以正常调用；
- 全量离线测试：共 62 个测试，全部通过。

### 当前限制

- 阶段 4 只完成真实 Planner，生成的任务尚未接入真实 Worker 执行；
- Planner 只支持 OpenAI Compatible 接口；
- 目前结构化输出依赖模型对 Function Calling 的支持；
- API 可用性仍受校园网、代理环境变量、模型权限和学校服务状态影响；
- Planner 最多尝试两次，第二次仍无法通过 Plan Guard 时会停止；
- API Key 需要由使用者在本地 `.env` 中配置，仓库不保存真实密钥。
