# ADR-0002：horus、Python FastMCP 与版本化人工审核

日期：2026-10-15。状态：部分由 [ADR-0003](0003-autonomous-workflow-and-python312.md) 替代。下文保留历史决定：Python 次版本改为统一 3.12，人工决定支持对话记录入口，补充自动收尾和跨业务验收。项目名、业务无关性、FastMCP、共享核心和 MCP Server + CLI + Skill 继续有效。

部分替代 [ADR-0001](0001-agent-tools-and-execution-engines.md) 的语言/入口实现与直接执行流程，保留 Hurl、k6、文件资产、共享核心和正式 M1 交付。

## 背景

项目命名为 horus。它不预置任何业务领域；LLM 从接口文档和源码分析当前系统的业务流程、规则、状态与数据依赖，并写入测试计划，供人工审核后执行。MCP Server、CLI 和 Skill 均必须交付。用户允许 MCP 复用成熟开源框架，例如 FastMCP。

此前 TypeScript/Node 方案只有文档，尚无实现代码。当前优先减少 MCP 接线工作，保留无模型的轻量 CI，并明确“生成计划”和“批准计划”的区别。

## 决定

1. 项目、CLI、Skill、Python import 命名空间和 MCP server name 统一为 horus；MCP 工具前缀 horus_，本地数据目录 .horus，环境变量前缀 HORUS_。发布包/远端仓库尚未创建，不声称名称已被注册。
2. 统一 Python >=3.12，CI 首先固定 3.13；核心、CLI、Worker 同一包。使用 uv/uv.lock 与 wheel，不保留只为 MCP 存在的第二运行时。
3. MCP 采用 PrefectHQ 的 Python FastMCP 4.1.0，通过其官方 fastmcp-slim[server] 分包作为 horus[mcp] 可选依赖，首版 stdio。开发再加 client extra。框架管理协议、注册和 Schema，horus 的 mcp 模块只包装核心服务。
4. CLI 基于 Typer；Pydantic 2 严格模型定义计划、业务理解、审核和结果，CLI/MCP 共用。CI 安装基础 wheel，不安装 MCP/LLM SDK，不启动常驻服务。
5. Plan 必含 businessUnderstanding 和 coverage。业务流程/规则/数据依赖及依据关联 case/assertion/capture；推断、冲突、未知项、未覆盖项明确记录。核心定义通用结构，不硬编码项目实体或规则。
6. 正式执行需要具体不可变 planRef 的有效人工审核。批准绑定环境及操作/负载范围；同版允许范围内重跑，适用于 CI。任何计划内容变化或新版本增补均重新审核。项目策略与审核同时生效。
7. 审核材料从计划确定性渲染，包含业务理解、依据、覆盖、步骤、数据、写操作/清理、规模、差异。审核记录独立保存，计划自身不能声明 approved。CLI 提供人工入口，MCP 仅送审/读状态，不提供批准工具；Skill 在待审核阶段等待人工。
8. 正式执行前执行共享 ReviewGate，未审核不发正式请求。受限只读探测仍由 probeAllow 管理。已有运行的失败清理沿用其已批准 cleanup 与账本，不能借此增加测试动作。
9. 无模型 CI 消费受信来源的已审核包，包括计划、运行所需操作/Schema 快照和审核记录；无需重新克隆或分析业务源码。当前部署版本作为运行元数据，代码版本更新本身不阻止旧版回归计划。

## 框架比较

| 方案 | 评价 |
| --- | --- |
| PrefectHQ Python FastMCP | 采用。成熟开源框架，Apache-2.0；类型化工具、Schema、stdio 和测试客户端可复用；提供官方依赖拆分以避免 CI 安装 MCP |
| 官方 MCP SDK 直接实现 | 可替代薄 MCP 模块，保持核心独立；当前为减少重复接线选择高层框架 |
| PrefectHQ FastMCP TypeScript | 官方同团队项目，但 Python 版更成熟。没有既有 TS 代码时，优先单一 Python 运行时 |
| punkpeye/fastmcp | npm 同名实现，不能与 Prefect 的 Python/TS 项目混淆；当前 README 标注旧协议限制，未作为默认选择 |
| TS 核心 + Python FastMCP 服务 | 需要额外进程间接口、部署与契约同步；当前无存量代码收益，不采用 |

不 fork FastMCP，不自动把所有被测 API 注册为 Agent 可随意调用的工具。horus 仍以受限探测和版本化计划执行为边界；来源分析与业务推断由外部 Codex/Claude 中的 LLM 完成。

## 取舍与限制

- 从 Node 转为 Python 的成本目前限于方案调整；k6 脚本由模板生成，CI 不因此需要 Node。Hurl/k6 仍为独立固定版本二进制。
- 框架不能判断业务语义是否正确，也不能替人批准。结构/引用由程序校验，流程与规则理解由人工审核。
- 审核只绑定固定资产与执行范围，凭据值和每轮变量不参与，允许轮换凭据和生成隔离数据。更改计划、目标或相关策略需要重新审核。
- 首版基于可信本机及受控 CI 输入。没有 MCP 批准工具不等于能抵御同权终端/文件写入者；普通哈希和审核人字段不构成身份认证。需要强制身份隔离时再增加独立审核服务或签名授权。
- 离线 CI 使用受保护分支/受控 artifact 中的审核包；撤销依赖更新这些输入，不承诺跨机器即时撤销。
- 真实 Agent 生成、实际人工审核与确定性执行分别留证。自动测试专用批准记录仅用于分支回归，不算真实人工验收。

## 首个交付的影响

M1 增加业务理解与覆盖契约、审核材料与 CLI 入口、ReviewGate、未审核零正式请求验证、CI 审核包导入导出及增补重新审核。MCP Server、CLI、Skill 三者与完整功能链路、基础并发指标一并交付，不新增独立 PoC 阶段。

完整任务、命令与验收见 [技术方案](../TECHNICAL-DESIGN.md)。

## 核实依据

- [FastMCP 4.1.0 发布](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.0)
- [FastMCP 官方 README 与示例](https://github.com/PrefectHQ/fastmcp/blob/v4.1.0/README.md)
- [fastmcp-slim 的 server/client 拆分及许可证声明](https://github.com/PrefectHQ/fastmcp/blob/v4.1.0/fastmcp_slim/pyproject.toml)
- [类型化工具与输入/输出模型](https://gofastmcp.com/servers/tools)
- [Prefect 的 FastMCP TS](https://github.com/PrefectHQ/fastmcp-ts)
- [另一个 npm fastmcp 项目](https://github.com/punkpeye/fastmcp)
