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
