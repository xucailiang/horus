# horus

业务无关的 AI 原生后端接口测试平台。Codex/Claude 中的 LLM 读取接口文档和项目源码，分析业务流程、规则与数据依赖，并将这些理解与测试步骤一起写入版本化计划。人工审核该版本后，MCP/CLI/CI 执行计划，通过 Hurl 完成功能链路、通过 k6 完成基础并发压测，输出统一证据和报告。

产品固定提供三部分：基于 Python FastMCP 的标准 MCP Server、调用同一执行核心的 horus CLI，以及指导 Agent 完成分析、计划、送审、执行和诊断的 horus Skill。业务知识属于各被测项目的计划资产，平台核心不写死任何业务流程。

主要使用场景是用户在 Codex / Claude Code 中连接 horus MCP，以自然语言发起测试、确认计划、查看报告。CLI 负责安装诊断、辅助操作和 CI；日常测试全链路经 MCP 完成，不要求切换到终端或手写项目文件。

当前状态：需求与技术方案已保存，尚未实现程序；文档中的命令是待实施接口。

开发、测试和 CI 统一使用 Python 3.12 与 uv；项目已用 .python-version 声明版本。实施时以 pyproject.toml 和 uv.lock 锁定依赖。

用户主流程：提供部署地址、文档、源码、凭据和测试边界 → 自动生成业务理解与测试计划 → 在当前 Agent 对话或 CLI 审核确认 → 自动准备数据、执行、校验和清理 → 查看自动生成的报告。补充测试单独送审，不阻塞本轮报告。

- [Agent + MCP 主使用链路与双客户端验收](docs/AGENT-MCP-WORKFLOW.md)
- [需求文档 v0.7](docs/PRD-horus.md)
- [技术方案、接口和首个交付任务](docs/TECHNICAL-DESIGN.md)
- [ADR-0001：架构决定](docs/decisions/0001-agent-tools-and-execution-engines.md)
- [ADR-0002：horus、FastMCP 与计划审核](docs/decisions/0002-horus-fastmcp-and-plan-review.md)
- [ADR-0003：自动测试闭环与 Python 3.12 / uv](docs/decisions/0003-autonomous-workflow-and-python312.md)
- [ADR-0004：MCP 主入口与首次接入](docs/decisions/0004-mcp-primary-workflow.md)

首个正式交付包含：真实 Agent 生成业务理解与计划、人工审核、鉴权/创建/查询/数据断言/失败清理、基础并发指标与阈值、MCP/CLI 一致执行，以及关闭 Agent 后对已审核版本的 CI 回放。增补或修改计划需要重新审核。

采用两个流程结构不同的永久测试后端，验证数据、权限、状态、异步等待和延迟问题；跨业务接入不修改平台核心。不单独做 PoC。
