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
