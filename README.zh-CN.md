# horus

在你写代码的地方，把测试也做起来。

[English](README.md) · [安装与使用](docs/usage.md) · [测试流程](docs/testing.md) · [MIT](LICENSE)

horus 是一个基于编程 Agent 的 AI 原生测试平台。我们已经在 Codex、Claude Code
这些工具里读代码、讨论需求、修改实现，测试也可以沿着这份上下文继续做下去。
你说明想验证的行为，提供接口定义和必要资料，Agent 据此设计测试；你确认计划后，
horus 负责执行，并把结果带回对话。

一条有用的测试链路，需要先弄清不少事情：哪些接口应该连起来，后续请求的 ID
从哪里来，哪些数据应当保持一致，检查失败后怎样清理现场。horus 要让 Agent 把
这些判断和依据写进计划，也把推断、未知项和没测到的部分说清楚。你审核时，
能够看懂它对业务的理解，以及准备怎样验证。

这份理解会留下来，成为可以检查、执行和重跑的测试计划。运行结束后，报告说明
实际检查了什么、哪里失败、清理是否完成，Agent 可以拿着这些证据继续排查代码。
后续改动时，这份计划可以继续使用，也可以根据新的需求修订。

## 如何使用

日常使用从一个 OpenAPI/Swagger 定义链接或常见 Swagger UI 链接开始。业务约定可以
直接在聊天里补充；有需要时，Agent 用宿主已有工具阅读源码。测试预期以需求和接口
约定为依据，源码结论作为补充资料随计划保存。被测服务可以用 Rust、Go、TypeScript、
Python 等语言编写，只要提供 HTTP API，就使用同一套接入方式。

Agent 通过 MCP 调用 horus，Skill 说明从理解业务到交付报告的流程和规则。
具体计划展示后，你回复 **“我确定”**，执行才会开始。确认针对刚看过的计划和目标；
修改了范围或并发设置，就需要重新审核。

执行时，Hurl 负责 HTTP 请求，k6 负责基础并发测试，Worker 按批准的计划完成数据
准备、断言和有时限的清理，自动生成 JSON、Markdown、JUnit 及可在 MCP 中读取的报告。
运行按项目隔离，重复的启动请求会返回原运行；宿主断开后仍可继续执行和恢复查询。
Worker 异常退出时会停止对应引擎。已审核计划也能导出、导入，通过基础 CLI 重跑，
无需再次调用模型。

运行中断后，可以根据账本中保存的资源 ID，单独执行原计划批准的清理动作。
丢失的凭据、结果不明的写入仍会列在报告中，不能据此保证所有资源都已清除。

如果希望结合目标机器的资源消耗分析结果，可以授权宿主 Agent 通过 SSH 采集 CPU、
GPU 和内存数据。短时基线用于讨论并发设置，运行期观测写入报告；已经批准的并发不会
被悄悄调整。

horus 在本地运行，用文件保存状态。它依托编程工具已有的模型、上下文和源码阅读能力，
通过 Skill 组织工作，再调用成熟的测试引擎完成执行。

## 开发与安装

在项目目录中，使用 Python 3.12 和 uv 0.9.11 安装：

```sh
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
uv run horus mcp --workspace /absolute/path/to/test-workspace
```

安装脚本需要 GitHub CLI（gh），会下载官方 Hurl 8.0.1、k6 2.3.0 并校验 SHA-256。
目前支持 macOS 和 Linux，也可用 HORUS_HURL_BIN / HORUS_K6_BIN 指向已有的对应版本。

MCP 配置和 Skill 安装是两步。运行 `uv run horus skill` 查看随包 Skill 位置，
安装到 Codex 的 `~/.agents/skills/horus` 或 Claude Code 的 `~/.claude/skills/horus`。
两客户端的配置参考 [接入说明](docs/AGENT-MCP-WORKFLOW.md)。

## 当前状态

项目仍在早期开发。接口导入、计划审核、HTTP 执行、基础并发、清理报告，以及无需模型
的基础 wheel 回放已有本地验证。两种原生客户端中的完整生成与实际人工确认、第二个
流程型后端，以及多身份、分支、轮询和功能用例并行仍待完成。
自动测试中的审核记录不算真实人工验收，M1 尚未完成。

目前使用本地检查，不配置 CI 流水线：

```sh
uv run pytest
uv run ruff check src tests scripts
uv run mypy src
uv build
```

[测试流程](docs/testing.md) 说明了自动回归、基础 wheel 验证与真实 Agent 验收各自
要检查什么。产品边界见 [PRD](docs/PRD-horus.md)，实现约定见 [技术方案](docs/TECHNICAL-DESIGN.md)，
进度见 [实施记录](docs/IMPLEMENTATION.md)。参与开发可参考 [贡献指南](CONTRIBUTING.md)。

项目采用 [MIT 许可证](LICENSE)。
