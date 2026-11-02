# TaskLoom 实验日志

本文档用于记录 TaskLoom 每个版本新增的功能、验证结果和已知限制。

## v0.1.0——阶段 0：项目初始化

日期：2026-11-02

### 新增功能

- 初始化 Git 仓库，并将 `main` 设置为默认分支。
- 创建 `app` 应用包和 `tests` 测试包。
- 在 `pyproject.toml` 中声明项目支持 Python 3.11 和 Python 3.12。
- 添加第一批依赖，包括 LangGraph、LangChain Core、Pydantic、环境配置和测试工具。
- 实现从环境变量和 `.env` 文件读取并校验基础配置。
- 实现统一的控制台日志配置，支持普通文本和 JSON 两种格式。
- 添加 `python -m app.main` 应用启动入口。
- 支持以 editable 模式安装项目，并提供 `taskloom` 命令行入口。
- 添加配置读取、日志格式和应用启动相关的单元测试。
- 添加本地环境变量模板、Git 忽略规则、安装说明和基础使用文档。

### 验证结果

- 运行环境：`TaskLoom` Conda 环境，Python 3.11.18。
- 应用启动：执行 `python -m app.main` 成功。
- 测试结果：共 6 个测试，全部通过。
- GitHub 同步：首次推送后，本地 `main` 与远程 `origin/main` 的提交一致。

### 已知限制

- 当前版本只完成开发计划阶段 0，提供可运行的项目骨架。
- 尚未实现核心数据协议、注册表、Plan Guard、LangGraph 工作流和真实 Agent。
- 当前 Conda 环境中存在与 TaskLoom 无关的旧包依赖缺失，但不会影响阶段 0 的应用启动和测试。
