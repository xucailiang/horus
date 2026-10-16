# horus：Agent + MCP 主使用链路

当前范围：本阶段不建设 GitHub Actions 等 CI 流水线，使用本地测试、静态检查和打包验证。原设计中的 CI 部分仅作为后续参考。

2026-10-15。首版核心流程已交付：MCP + Skill 引导业务与调用链分析，人工确认后执行功能/基础并发测试、清理并返回报告。具体证据和限制见 IMPLEMENTATION.md。

## 1. 产品入口

用户在支持本地 MCP 的编程工具内使用 horus，全程以自然语言描述目标、审核计划和查看报告。宿主 Agent 的 LLM 负责理解、推理、生成计划和解释；horus 负责资料、资产、审核检查、执行和事实报告。CLI 用于一次性安装、辅助操作和已审核计划回放。

MCP 配置和 Skill 安装是不同步骤，具体位置由宿主决定。没有原生 Skill 加载器时，也可让 Agent 阅读 SKILL.md 及其引用。MCP 初始化 instructions、工具说明及 get_capabilities 提供基本工作流。Codex 和 Claude Code 已完成验证，下方配置作为接入示例。

## 2. 一次性接入

优先把[安装说明](usage.md#install-with-your-coding-agent)交给当前编程助手，由它完成依赖安装、MCP 配置、Skill 安装，并调用 horus_get_capabilities 验证连接。安装不包含对目标服务发起测试。

1. 在运行 MCP 的机器上用 Python 3.12 + uv 安装锁定依赖和固定 Hurl/k6，完成预热；MCP 启动握手不承担首次下载、构建或业务仓库克隆。
2. 配置客户端以 stdio 启动 horus mcp --workspace <absolute-workspace>。允许空工作区，不要求先运行 horus init 或手写 project.yaml。
3. 提供凭据的运行环境或既有受控凭据来源。工具只收绑定名称，避免把密码放入聊天或 MCP 参数。首次设置属于环境接入，后续通过 MCP 检查是否就绪；环境变量在进程启动时继承，变更后可能需要重连。
4. 分别安装 horus Skill 到宿主支持的位置，并检查是否能发现。宿主工具授权与 horus 计划审核是两层机制；不自动修改用户的客户端安全设置，也不声称 horus 的批准能绕过宿主授权。

Codex 的 MCP 使用 config.toml，Claude Code 可用 .mcp.json；其真实配置说明见文末官方来源。以下为当前源码模式示例，绝对路径需要替换，运行前须先完成 uv sync。正式发布再提供锁定 wheel 对应的安装方式；不能让未发布的同名包被误装。

Codex：

~~~toml
[mcp_servers.horus]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/horus", "run", "--no-dev", "--frozen", "--python", "3.12", "--extra", "mcp", "horus", "mcp", "--workspace", "/absolute/path/to/horus-workspace"]
env_vars = ["HORUS_USERNAME", "HORUS_PASSWORD"]
startup_timeout_sec = 30
tool_timeout_sec = 60
~~~

Claude Code：

~~~json
{
  "mcpServers": {
    "horus": {
      "type": "stdio",
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/horus", "run", "--no-dev", "--frozen", "--python", "3.12", "--extra", "mcp", "horus", "mcp", "--workspace", "/absolute/path/to/horus-workspace"]
    }
  }
}
~~~

两者都需要确保所需凭据在服务端实际可见；Claude Code 的启动环境、变量传递和组织策略按其官方配置处理，示例不内嵌密码。uv 使用绝对路径，避免 GUI/终端 PATH 不同；平台目录与持久工作区显式指定，避免依赖宿主当前目录。

如需读取工作区以外的本地文档/checkout，首次配置服务端 --read-root <absolute-path>；测试验收可预先注册 fixture 所在只读根目录。后续接口接入使用 OpenAPI/Swagger URL；可选源码由宿主已有权限读取。

Skill 的共享正文来自随包分发的 skills/horus。Codex 可安装到 ~/.agents/skills/horus，Claude Code 可安装到 ~/.claude/skills/horus；安装与发现需分别验收。首版不要求专属插件、MCP sampling、elicitation 或自定义审批 UI。

## 3. 首次测试的完整链路

| 步骤 | 用户/Agent 行为 | MCP 能力与输出 |
| --- | --- | --- |
| 连接检查 | Agent 查看 horus 能力和环境问题 | get_capabilities 返回版本、引擎状态、资料能力、范围上限和下一步 |
| 创建或恢复项目 | 从自然语言目标选择已有项目或新建 | list_projects / open_project 返回 projectRef 与上下文摘要，不要求 CLI 初始化 |
| 提交接入草案 | Agent 整理目标地址、环境、凭据绑定和边界 | stage_project_config 保存不可变 configRef，列缺口；草案不自动扩大权限 |
| 理解接口与源码 | 导入 OpenAPI/Swagger 链接；按需使用宿主源码能力或聊天资料 | import_api / get_context；可选源码由宿主阅读，聊天/源码依据随 Plan.supportingContext 保存 |
| 生成并校验草稿 | Agent 产出业务理解、链路、检查和清理，说明语言跟随当前对话 | preview_plan 在内存中校验并生成可读文本，返回 previewRef，不保存计划 |
| 在对话中展示 | 直接回复可读计划、范围、负载上限和未覆盖项，不使用 Ask 卡片 | 用户先在对话里读计划并提出修改，此时不创建测试目录 |
| 确认后保存 | 用户确认刚展示的草稿 | confirm_plan 校验 previewRef，保存同一计划并记录真实确认；修改后需重新展示 |
| 执行 | Agent 一次提交 planRef、reviewRef、环境及 requestId | start_run 返回 runId；由 reviewRef 固定配置，独立 Worker 执行，不逐 HTTP 请求往返 Agent |
| 等待 | Agent 自动有界等待并按需显示进展 | wait_run / get_run 返回状态、摘要；断开重连后可恢复查询 |
| 报告 | 打开与当前对话同语言的 Markdown，先看结论、问题和关键数据，再看业务简图 | 返回 testDirectory、reportPath 和 artifactRef；get_artifact 可分页读取 Markdown |

每次执行作为一轮版本测试，计划和报告同放在 `projects/<projectRef>/.horus/runs/<runId>/`，新一轮不覆盖上一轮。目录含 `plan.md`、`plan.json`、`config.json`、`review.json`、`report.md`、`details.md`、`report.json` 和 `junit.xml`。计划使用完整 Mermaid 调用图，报告先给结论、问题和关键数据，再给业务简图。逐步记录和证据放在 details.md，可通过 detailsArtifactRef 分页读取。

工具在协议中使用 horus_ 前缀。业务工具显式携带 projectRef，引用跨项目校验；同一个 MCP 服务处理不同项目时不依赖可被别的会话覆盖的全局“当前项目”。

用户只需完成必要资料/凭据接入、说明边界、确认计划；Agent 自行编排上述调用。测试失败、部分阻塞和超时也必须自动清理并交付事实报告。补测单独送审，不阻塞本轮报告。

## 4. 配置草案与审核边界

stage_project_config 只保存草案，不直接激活执行权限。ReviewBundle/ApprovalRecord 绑定 configRef、planRef 与 scopeHash；配置和计划作为同一次人工审核材料展示。执行权限取服务端上限、用户既定边界和批准范围的交集，Agent 不能通过草案突破上限。

首次项目在批准前若没有已经明确允许的只读探测范围，继续依据文档和源码规划即可；不能通过配置草案自行授权探测。MCP 不接受任意宿主路径，读取使用注册资料、服务端工作区相对路径或一次性配置的只读根目录。客户端目录不被当作服务端目录。

对话确认采用可信宿主/Agent 的本地记录方式，校验版本和范围，保留必要确认依据。它不构成对恶意 Agent 的强身份隔离；不能用普通消息引用声称独立验证了人工身份。

## 5. 长任务、重试和恢复

- start_run 必须携带 requestId。相同项目、requestId 和参数重试返回同一 runId；参数不同返回冲突，不能再次创建业务资源或启动一轮负载。
- requestId 的占位、runId 和运行意图先原子落盘；Worker 使用独占认领及启动握手，避免客户端收不到响应后重试造成双重执行。无法确认的写入结果保留未知，不靠重发解决。
- start_run 快速返回；wait_run 每次最多 30s，并低于宿主实际工具超时。等待超时/取消只结束等待，不隐式取消运行；停止测试使用 cancel_run。
- Worker 与 MCP 的 stdio、父进程退出和会话生命周期解耦，macOS/Linux 使用独立进程会话并正确处理脱离父进程和日志句柄。真实客户端关闭会话的存活行为必须实测，不以“已创建子进程”替代证明。
- 重连后 open_project 返回最近资料、计划、审核和运行引用；Agent 可按原 runId 继续等待或查看报告，不重新启动写操作。上下文压缩后也不依赖原聊天里的临时变量。
- 异常终止时，后续查询从心跳、事件和资源账本恢复 interrupted 摘要，报告残留资源及修复入口。不得把实际中断显示成仍在正常执行。

## 6. 返回结果与 Agent 可用性

返回稳定结构化字段：schemaVersion、projectRef、status、summary、refs、issues、nextActions；无项目查询的 projectRef 为 null，具体工具另有类型化 payload。失败问题至少包含 code、位置、原因、可采取的下一步和是否需要用户补充。短 content 与 structuredContent 保持一致。

nextActions 只提供建议，不能替代用户批准。测试失败仍是有效业务结果，不能误当 MCP 协议故障触发重试。资料与日志分页、有界返回，避免客户端截断导致计划或证据丢失。报告引用必须可以通过 get_artifact 读取，不仅返回可能无法在客户端打开的服务端文件路径。

## 7. 主链路验收

Codex 和 Claude Code 均已完成 reference-api 的原生计划生成、实际人工确认、执行和报告读取；Codex 另完成 workflow-api。以下保留复测方法，当前交付不再追加客户端/场景组合。单元测试和通用 MCP 客户端的结果与实际 Agent 验收分别记录。

1. 空持久工作区启动 MCP，发现工具；Agent 经 MCP 创建项目、提交配置、接入资料，全程不调用 horus CLI、shell 或手写测试文件来补足业务流程。
2. 用自然语言测试目标生成包含业务理解的计划；对话中确认该版本后执行，未确认或更改版本必须阻塞。
3. 自动等待到报告返回；正常、数据/权限/状态错误、异步超时、清理失败均有证据与报告，不要求用户手动推进。
4. 模拟 start_run 响应丢失后重试，仍只有一个 runId 和一次实际执行；等待超时不重启运行。
5. 正常关闭/重连真实客户端，Worker 持续执行，查询同一运行与报告；另行强杀 Worker 时如实恢复中断状态和账本。跨项目并行查询不串项目。
6. Skill 已安装和未安装都检查 MCP 基础可用性；不得假设接入 MCP 就自动安装 Skill。宿主工具权限提示单独记录，不冒充 horus 的业务审批。
7. 首条交付先完成 reference-api 链路，随后同一 M1 经两客户端接入 workflow-api，平台核心不增加业务特例。

## 8. 官方接入依据

2026-10-15 核对。客户端行为会演进，实施验收须记录实际版本。

- [OpenAI Docs：Codex MCP](https://developers.openai.com/codex/mcp/)
- [OpenAI Docs：Codex Skills](https://developers.openai.com/codex/skills/)
- [Claude Code：MCP](https://code.claude.com/docs/en/mcp)
- [Claude Code：Skills](https://code.claude.com/docs/en/skills)
