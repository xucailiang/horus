# horus

[English](README.md) · [安装与使用](docs/usage.md) · [测试流程](docs/testing.md) · [MIT](LICENSE)

面向 Codex / Claude Code 的 AI 原生 HTTP API 测试工具。Agent 理解接口和业务、生成计划；
用户确认具体计划后，horus 执行测试、清理数据并输出报告。MCP 是日常入口，Skill 指导编排，
CLI 复用同一核心。horus 自身统一使用 Python 3.12 和 uv。

被测服务的语言和框架不限：Rust、Go、TypeScript、Python 等均通过相同的 HTTP 接口测试。
提供 OpenAPI/Swagger 定义链接或常见 Swagger UI 链接即可导入接口。源码由宿主 Agent 按需阅读；
聊天资料或源码结论直接随计划保存为可选依据，无需额外导入仓库、建立源码索引或知识渠道。

## 开发与安装

```sh
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
uv run horus mcp --workspace /absolute/path/to/test-workspace
```

安装脚本通过 gh 下载固定版本并校验 SHA-256：Hurl 8.0.1、k6 2.3.0。
也可用 HORUS_HURL_BIN / HORUS_K6_BIN 指向这些版本的可执行文件。

MCP 配置和 Skill 安装是两步。运行 `uv run horus skill` 查看随包 Skill 位置，
安装到 Codex 的 `~/.agents/skills/horus` 或 Claude Code 的 `~/.claude/skills/horus`。
两客户端的配置参考 [接入说明](docs/AGENT-MCP-WORKFLOW.md)。

## 当前状态

已实现接口 URL 导入、计划与审核材料、stdio MCP、随包 Skill、Hurl 功能执行、k6 基础并发、
独立 Worker、启动去重、取消清理和自动报告。自动回归覆盖正常、200 数据错误、延迟超标、
审核门槛、隔离和重连等场景。基础 wheel 的无 MCP/模型 CLI 回放已验证。

M1 仍在开发。真实 Agent 生成与实际人工确认、状态/身份/轮询、修复清理及完整双客户端
验收尚未完成；自动测试中的审核记录不算实际人工验收。

```sh
uv run pytest
uv run ruff check src tests scripts
uv run mypy src
uv build
```

产品边界见 [PRD](docs/PRD-horus.md)，实现约定见 [技术方案](docs/TECHNICAL-DESIGN.md)，
进度见 [实施记录](docs/IMPLEMENTATION.md)。
