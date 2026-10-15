# ADR-0004：MCP 主入口与首次接入

日期：2026-10-15。状态：采用。用户明确主要场景是在 Codex / Claude Code 等 Agent 中连接 horus MCP，要求主链路通顺完整。

补充 [ADR-0003](0003-autonomous-workflow-and-python312.md)，调整此前“服务启动时绑定已有项目”的默认入口；Python 3.12/uv、FastMCP、共享核心、Hurl/k6 和人工审核继续有效。

## 背景

已有工具覆盖资料、计划、执行与报告，但首次接入仍隐含 CLI init 和配置文件编辑；MCP 连接与 Skill 加载也没有区分。长任务还需要防止工具超时重试造成重复写入，并明确项目隔离及重连后的恢复。

## 决定

1. Codex / Claude Code + horus MCP 为主要交互入口。一次性安装配置后，正常测试从项目创建到报告全程走 MCP，不借 CLI/shell 手工填补接口缺失；CLI 用于安装诊断、辅助及 CI。
2. 服务默认从可为空的持久 --workspace 启动。提供能力查询、项目列表、创建/恢复项目、配置草案入口；业务工具显式使用 projectRef，避免跨聊天共享可变当前项目。
3. 配置草案返回不可变 configRef，不自动赋权；与 planRef、scopeHash 一起作为审核材料。执行引用具体 reviewRef 固定配置，不选择模糊的最新批准，不突破服务端和用户边界。
4. MCP 配置和 Skill 安装分开交付并验证。server instructions、工具说明和能力查询提供基本指引，MCP 可独立完成基本流程；Skill 指导宿主更稳定地编排和呈现，不承担核心强制约束。
5. 资料抓取和运行快速返回引用及状态；start_run 使用持久 requestId 幂等和 Worker 独占认领，重复请求不得重复执行业务动作。等待有界，等待取消不等于取消运行。
6. 项目上下文、计划、审核与运行引用持久保存；重连后恢复查询。Worker 与 MCP 生命周期解耦，真实宿主正常断开不影响执行；强杀异常另行恢复 interrupted 摘要与资源账本。
7. 结果提供类型化状态、摘要、引用、问题和下一步；审核材料与报告可经 MCP 读取，避免服务器路径在客户端打不开。宿主工具授权与业务审核分开说明，尊重客户端权限策略。
8. Codex 和 Claude Code 各自进行真实客户端验收，包含空工作区、Skill 已装/未装、版本确认、长任务、响应丢失重试、断开重连和项目隔离。通用 MCP client/Inspector 检查不能替代它们。

## 依据与后果

客户端配置及 Skill 发现参考当日的 OpenAI Docs 和 Claude Code 官方文档，具体链接与配置目标见 [Agent + MCP 主链路](../AGENT-MCP-WORKFLOW.md)。本地 Codex mcp add --help 也已核对命令形式；未修改用户客户端配置，未进行实际 horus 连接，因为程序尚未实现。

这些入口和边界纳入正式 M1 的 MCP/Skill/双客户端任务；不新增 PoC 或常驻平台服务。完整契约见 [技术方案 v0.4](../TECHNICAL-DESIGN.md)，产品要求见 [PRD v0.7](../PRD-horus.md)。
