# TaskLoom

TaskLoom 是一个基于 LangGraph 的受控多智能体任务编排项目。系统使用固定外层 Workflow 负责计划、依赖调度、并行执行、审核、重试和重新规划；每个短生命周期 Worker 在内部运行受权限约束的 `LLM → Tool → Observation → LLM` 自主循环。

当前已完成阶段 0～10，包括真实 LLM Planner、动态 Worker、Reviewer 质量闭环、只读工具、Redis Checkpoint、Session 生命周期、Session Memory 和上下文压缩。

## 主要功能

- Planner 生成结构化 ExecutionPlan；
- Plan Guard 校验字段、能力、工具权限、依赖环和预算；
- Supervisor 根据依赖关系并行调度动态 Worker；
- Worker 只能调用授权工具，并通过结构化 Output Contract 返回结果；
- Reviewer 支持完成、定向重试、Replan 和失败终止；
- Redis Checkpoint 支持节点级暂停、恢复和旧 Thread 替换；
- 同一 Session 只保留最新可恢复 Thread；
- Session Memory 独立存入 Redis，不随旧 Thread Checkpoint 删除；
- Worker 使用 50%/70%/90% 三级 Message 压缩；
- 支持本地文档读取、项目知识搜索、网页搜索和网页读取工具。

## 环境要求

- Python 3.11 或 3.12；
- Redis 8 或 Redis Stack，用于 Redis Checkpoint 和 Session Memory；
- 一个 OpenAI Chat Completions 兼容模型服务。

## 安装

使用已有 Conda 环境：

```powershell
conda activate TaskLoom
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

也可以创建项目虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

真实密钥只填写在被 Git 忽略的 `.env` 中，不要写入 `.env.example`。

## 核心配置

```env
TASKLOOM_LLM_MODEL=你的模型名称
TASKLOOM_LLM_API_KEY=你的API密钥
TASKLOOM_LLM_BASE_URL=https://example.com/v1

TASKLOOM_CHECKPOINT_BACKEND=redis
TASKLOOM_REDIS_URL=redis://:password@127.0.0.1:6379/0
TASKLOOM_CHECKPOINT_TTL_MINUTES=1440

TASKLOOM_SESSION_MEMORY_TTL_HOURS=72
TASKLOOM_SESSION_MEMORY_MAX_ITEMS=50
TASKLOOM_LLM_CONTEXT_WINDOW_TOKENS=32768
TASKLOOM_CONTEXT_SAFETY_MARGIN_TOKENS=1024
```

Checkpoint 和 Session Memory 使用同一个 Redis 服务，但键空间和生命周期相互独立：

```text
Checkpoint：一次 Query/Thread 的可恢复执行状态，默认 24 小时
Session Memory：同一 Session 跨 Query 共享的压缩上下文，默认 72 小时
```

## 运行完整工作流

```powershell
python -m app.graph.runtime_cli `
  --goal "分析 TaskLoom 的 Memory 机制并整理报告" `
  --session-id stage10-demo `
  --checkpoint-backend redis `
  --no-print-json
```

使用相同 `session_id` 再提交一个新 Query 时，程序会在 Planner 运行前加载已有 Memory：

```text
Session Memory 已从 Redis 加载：session_id=stage10-demo，items=...
```

运行结束、失败或发生 Replan 时，系统会从目标、WorkerResult 和 ReviewerDecision 中提炼稳定事实、约束、建议和失败经验，再写回 Session Memory。完整原始消息和 Tool Observation 不会作为长期 Memory 保存。

## 暂停和恢复

在外层节点执行后暂停：

```powershell
python -m app.graph.runtime_cli `
  --goal "生成一份 LangGraph 学习报告" `
  --session-id checkpoint-demo `
  --checkpoint-backend redis `
  --pause-after validate_plan `
  --no-print-json
```

根据输出的 `thread_id` 恢复：

```powershell
python -m app.graph.runtime_cli `
  --resume <thread_id> `
  --session-id checkpoint-demo `
  --checkpoint-backend redis `
  --no-print-json
```

当前恢复粒度是外层 LangGraph 节点。正在执行的单次 LLM 或 Tool 调用尚不能从中间位置恢复。

## Worker 三级上下文压缩

压缩阈值基于可用输入容量，而不是模型标称窗口：

```text
可用输入 = 模型上下文窗口 - 输出预留 - Tool Schema - 安全余量
```

- 50%：裁剪较早的超长 Tool Observation；
- 70%：摘要较早交互，保留最近消息；
- 90%：紧急折叠，只保留任务、约束、摘要和最近消息；
- 仍然超限：返回 `context_exhausted`。

压缩过程不会拆开 Assistant Tool Call 与对应的 ToolMessage。

## 测试

```powershell
python -m pytest -q
```

阶段 10 完成时的全量验证结果：

```text
152 passed
```

## 当前边界

- 不保存跨 Session 的用户长期记忆；
- 不建设独立 Knowledge Store、向量数据库或 RAG 索引；
- 不建设任务审计数据库；
- 不开放任意 Shell、任意文件写入和其他高风险工具；
- 前端、SSE、细粒度异步暂停和取消属于下一阶段。
