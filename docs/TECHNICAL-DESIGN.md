# horus 技术方案：业务理解、计划审核与确定性执行

当前范围：本阶段不建设 GitHub Actions 等 CI 流水线，使用本地测试、静态检查和打包验证。原设计中的 CI 部分仅作为后续参考。

方案基线 v0.4，2026-10-15。状态：M1 实施中。下文包含已实现能力与后续计划，当前实现和验收证据见 [实施记录](IMPLEMENTATION.md)。

依据：[PRD v0.7](PRD-horus.md)、[ADR-0003](decisions/0003-autonomous-workflow-and-python312.md) 与 [ADR-0004](decisions/0004-mcp-primary-workflow.md)。主使用路径及两客户端接入示例见 [Agent + MCP 链路](AGENT-MCP-WORKFLOW.md)。首个正式交付 M1 包含 MCP Server、CLI、Skill、自动接入、业务理解与人工审核、完整功能链路、基础并发压测、自动报告和两个后端的通用性验收，不单独建设 PoC。

## 1. 技术选型与运行形态

| 项目 | 决定 |
| --- | --- |
| 语言 | 开发、测试与 CLI/MCP/Worker 统一 Python 3.12 系列；uv、pyproject.toml、uv.lock，发布 wheel |
| MCP | PrefectHQ 的 Python FastMCP 4.1.0；可选依赖 fastmcp-slim[server]==4.1.0，首版 stdio |
| CLI | Typer，命令 horus，与 MCP 调用相同 Python 应用核心 |
| 功能执行 | Hurl 8.0.1，独立进程，不 fork、不依赖内部 Rust API |
| 并发压测 | k6 2.3.0，独立进程，受控脚本模板 |
| 类型和计划 | Pydantic 2 严格模型、PyYAML 安全加载；共享模型导出 JSON Schema |
| OpenAPI | openapi-spec-validator 校验 Swagger 2.0 / OpenAPI 3.0/3.1；horus 适配器建立统一 OperationCatalog，保留原文与方言 |
| 响应结构 | jsonschema 4 与 openapi-schema-validator；显式按 JSON Schema/OAS 方言选择验证器，不修改响应、不补默认值、不强制类型转换 |
| 测试 | pytest、pytest-asyncio、FastMCP Client/官方 MCP Python client，真实 HTTP fixture；Ruff、mypy 严格检查 |
| 存储 | 文件及 JSONL，无数据库、消息队列或向量服务 |
| 平台 | 首版 macOS/Linux，本地验证；Windows、远程 HTTP MCP 和专属插件后续扩展 |

Hurl、k6、FastMCP 是 2026-10-15 核对的发布版本。二进制版本、下载地址及校验信息写入工具清单；Python 依赖及 FastMCP 间接使用的官方 MCP SDK 版本在实施首次安装时写入 uv.lock。CI 不使用 latest、不重复编译引擎。

horus 是已确认的项目名、CLI 名和 Skill 名；Python import 命名空间为 horus，MCP server name 为 horus，工具前缀 horus_。名称不代表已发布或注册软件包。模型由 Codex/Claude 管理，平台没有内置 LLM key、模型 SDK 或 Agent 循环。

### 1.1 MCP 框架选择及复用边界

采用 [PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp)，使用官方示例建立 horus 自己的薄入口模块，不 fork FastMCP、不复制整个平台。FastMCP 负责 stdio、工具注册、输入/输出 Schema 与 MCP 生命周期；horus 负责资料、计划、人工审核、策略、执行、证据和报告。

FastMCP 4.1.0 的官方打包已提供 fastmcp-slim。horus[mcp] 仅依赖其 server extra；开发/测试再安装 client extra。基于同一 fastmcp import API，不建立一套自制协议实现。只有调用 horus mcp 时才加载此依赖；业务 CI 安装 horus 基础 wheel，不安装 MCP、模型 SDK 或应用 UI 的 extras。

| 候选 | 判断 |
| --- | --- |
| Python FastMCP | 当前采用。成熟框架、Apache-2.0；类型化函数/Pydantic 模型直接注册工具，可减少协议和 Schema 接线工作 |
| 直接使用官方 MCP SDK | 可行备选。协议依赖可靠，但本项目仍需自行接工具注册等框架工作；放在薄适配层之后，必要时可替换 |
| Prefect FastMCP TypeScript | 同团队提供的官方 TS 实现，当前比 Python 版年轻；在没有既有 TS 代码的情况下，首版采用 Python 版 |
| punkpeye/fastmcp（npm fastmcp） | 是另一套实现；当前 README 提醒只支持旧协议版本，不作为本次默认底座 |

此前 Node/TypeScript 是未实施的方案。现在统一为 Python 核心、CLI、MCP 和 Worker，避免只为 MCP 再增加一个 Python 服务。k6 的受控 JavaScript 是编译产物，不要求 CI 安装 Node。框架比较依据和版本见文末，不用星数替代实际兼容性验收。

### 1.2 Python 与 uv 约束

- 仓库 .python-version 已固定为 3.12；实施时 pyproject.toml 使用 requires-python = ">=3.12,<3.13"，类型检查与 CI 同样指定 3.12。补丁版本可在 3.12 系列内更新，不自动扩大到其他次版本。
- 项目环境、解释器选择、依赖安装/锁定、运行及构建均使用 uv；提交 uv.lock，CI 用 --frozen，禁止临场升级依赖。uv 自身在实施时锁定发布版本。
- CLI/MCP/Worker 复用同一 Python 环境，Worker 使用当前解释器启动。业务 CI 也通过 uv 安装固定 wheel 与带哈希依赖，省去 MCP 可选依赖。
- 当前没有 pyproject.toml 或 uv.lock，不宣称已初始化可运行工程；这些随 T1 正式实施创建。

### 1.3 用户闭环与接入编排

主要用户通过 Codex / Claude Code 的 horus MCP 完成测试。CLI 是安装诊断、辅助及 CI 接口；空工作区的项目创建、配置草案、资料接入、审核材料和报告必须都有 MCP 能力，不要求 Agent 转用 shell/CLI 写项目文件来补齐流程。

Agent + Skill 自动完成资料注册、接口目录、源码检索、业务理解、身份/凭据绑定建议、链路/数据/断言和操作范围草案。用户提供部署地址、资料、凭据及测试边界，集中处理缺失条件并审核；不要求逐条编辑操作白名单或请求脚本。生成的范围不得超过用户预设边界，最终资产随版本审核。

审核后默认自动运行并出报告。Agent 入口由 Skill 等待完成、呈现报告引用及摘要；关闭 Agent 也不影响 Worker 执行与事实报告。补测作为下一轮独立版本送审，当前运行先在既定范围内完成报告。支持范围是具备资料和访问条件的 HTTP/REST + JSON；能力不足在执行前集中报告，不能扩展成无边界的“所有后端均能全覆盖”。

## 2. 模块边界与数据流

~~~mermaid
flowchart TD
    A[Codex / Claude + horus Skill] --> M[FastMCP 标准 stdio]
    C[CLI / CI] --> K[共享应用核心]
    M --> K
    K --> S[资料快照与项目知识]
    K --> P[含业务理解的不可变计划与校验]
    P --> B[审核材料与版本差异]
    U[人工审核] --> V[绑定版本与范围的审核记录]
    B --> U
    P --> G[核心检查审核与项目策略]
    V --> G
    G --> W[本地运行 Worker]
    W --> H[Hurl 功能执行器]
    W --> L[k6 压测执行器]
    H --> R[脱敏证据与统一报告]
    L --> R
    R --> K
~~~

- Agent 理解文档与源码，推断业务流程、规则和数据依赖，选择探测、设计数据与链路、提出预期并解释结果；这些理解写入计划供人工审核，核心提供工具和可靠执行状态。
- MCP/CLI 只负责入口协议；计划、变量、策略、调度、清理和报告只有一套实现。
- 一次计划由 Worker 连续执行，Agent 无需逐条请求调用 MCP，CI 不依赖模型在线。
- 平台计划是权威资产，Hurl/k6 文件是编译产物；不直接接受任意 Hurl、JavaScript 或 shell。
- 业务规则、实体、状态和流程都是被测项目数据。核心只认识规则/流程/依赖/用例/断言等通用结构；测试 fixture 的 records、value 等名称不得出现在核心分支中。
- 人工审核是正式执行的必要条件，MCP 与 CLI 都通过同一个 ReviewGate；FastMCP 不承担业务审核逻辑。
- Worker 按运行启动，结束即退出；首版不建设常驻调度服务。

单 Python 分发包，CLI 核心与 MCP 可选依赖分开，目录按职责划分：

~~~text
pyproject.toml            wheel、horus 命令入口、mcp 可选依赖
.python-version           统一 Python 3.12
uv.lock                   锁定依赖
src/horus/core/           项目、资料、知识、计划、审核、变量、策略、调度、报告
src/horus/adapters/       Hurl、k6、OpenAPI URL 与文件访问
src/horus/cli/            Typer 命令与退出状态
src/horus/mcp/            FastMCP 工具注册与标准结果映射
src/horus/worker/         独立运行、心跳和取消处理
skills/horus/             通用 Skill 与两客户端配置参考
tests/{unit,integration,acceptance}/
tests/fixtures/reference-api/
tests/fixtures/workflow-api/
docs/
~~~

## 3. 项目、资料和执行范围

### 3.1 保存方式

MCP 以 `horus mcp --workspace <absolute-path>` 启动，工作区可为空。
项目身份持久保存，所有业务调用显式携带 projectRef，不依赖共享当前目录。

```text
workspace/projects/<projectRef>/project.json
workspace/projects/<projectRef>/.horus/
  sources/     接口定义与可选文档快照
  configs/     目标、策略、凭据变量名称
  plans/       含业务理解、依据和覆盖的计划
  bundles/     固定审核范围和材料引用
  reviews/     独立人工决定记录
  artifacts/   审核材料和报告
  requests/    幂等启动登记
  runs/<runId>/ 状态、事件、报告和资源账本
```

内容寻址使用规范化 JSON 的 SHA-256；读取时验证哈希与项目归属。
计划修订保存 parentPlanRef/changeReason，原内容不覆盖。哈希验证一致性，
不证明审核人身份。原子写入与文件锁保护资产、启动登记和项目执行互斥。

源码由宿主按需读取，其结论随 supportingContext 保存；不保存或索引源码仓库。
每轮及每个 case 使用独立变量，凭据仅在内存中解析，落盘前脱敏。
本地结果保留至显式删除；不引入后台历史清理服务。

### 3.2 接口导入与可选上下文

主要接入 OpenAPI/Swagger 定义 URL 或常见 Swagger UI URL。受限 HTTP 获取解析 JSON/YAML，
并从 UI 的 url/configUrl、初始化脚本或嵌入配置定位定义，不执行页面 JavaScript。
存在多个定义时返回候选；无法解析时明确要求原始定义 URL，不猜接口或静默成功。
本地规范文件保留为开发、离线及 fixture 的便捷输入。

校验 Swagger 2.0 / OpenAPI 3.0/3.1，建立 OperationCatalog：operationId、method、path、
参数、鉴权、响应约定及来源。缺少 ID 时用 method:path，重复 ID 报错。保留原始方言，
不把 Swagger 2.0 假装转换为 OpenAPI 3.1。外部引用不能由验证器任意联网加载；
不支持的约束或引用显式返回问题，不能忽略后判为通过。

源码由宿主 Codex/Claude Code 使用已有工具按需读取。聊天资料、业务说明及源码结论
直接写入 Plan.supportingContext，包含 id、text、source 和可选 locator，依据用
context:<id> 引用。接口定义的依据仍用 sourceRef#Lx。平台不建设 Git 克隆、源码索引、
语言 AST 或额外知识渠道。未知项和冲突属于计划，随同审核。

执行仅依赖 HTTP 接口约定，和被测后端语言、框架及业务无关；不要求安装或执行被测项目源码。
明确需求/接口约定优先，源码是可选推断材料，不能以错误实现证明预期正确。

### 3.3 策略和凭据

project.yaml 明确服务 origin、可探测只读操作、正式执行操作、清理操作、并发上限、迭代/请求预算和超时。凭据只声明环境变量名，运行时解析。

上述具体配置由 Agent 从资料与用户边界生成草案并集中展示，不把逐接口配置责任转回用户。身份不足、特殊签名不支持、目标不可达等问题在正式执行前集中定位。

测试后端的配置形态如下。限额是样例配置，不替业务系统设定通用容量：

~~~yaml
schemaVersion: "1"
name: reference-api-tests
environments:
  test:
    baseUrlFromEnv: HORUS_BASE_URL
    secretBindings:
      username: HORUS_USERNAME
      password: HORUS_PASSWORD
policy:
  probeAllow: [health]
  runAllow: [login, logout, createRecord, getRecord, listRecords, deleteRecord]
  cleanupAllow: [login, logout, deleteRecord, getRecord]
  loadAllow: [getRecord]
  pollAllow: [getRecord]
  limits:
    maxVus: 10
    maxLoadIterations: 1000
    maxLoadDurationMs: 60000
    maxFunctionalParallelCases: 2
    maxRequestsPerRun: 1500
    maxRunDurationMs: 120000
    maxCleanupDurationMs: 30000
    maxReportDurationMs: 10000
~~~

HORUS_BASE_URL 是访问已部署环境的地址。验证计划时计算功能、负载、清理与已声明轮询的最大请求规模，超过预算则拒绝；关闭重定向和隐式重试，避免请求量脱离计划。结果记录配置快照哈希，不记录 secretBindings 对应的凭据值。

MCP 的 stage_project_config 只保存不可变配置草案并返回 configRef，不直接激活权限。prepare_review 把 configRef、planRef 和 scopeHash 绑定为同一审核材料；执行权限取服务端上限、用户既定边界与批准范围的交集。已有明确只读授权之外的草案不得自行启用探测。核心在保存、运行前及每步执行前检查策略；Skill 不能扩大范围。平台约束自身工具，不声称作为宿主操作系统沙箱。

只读探测必须在明确配置的只读操作集合内；HTTP GET 本身不作为无副作用的证明。登录和写操作进入正式计划。操作 origin 来自环境配置，计划不接受任意绝对目标 URL。

禁止重定向跟随、自定义代理、任意 Hurl Options、任意文件读取、远程 k6 import 或用户脚本。参数按位置编码，body 用 JSON 序列化，header 拒绝换行。

## 4. 计划与结果契约

### 4.1 对象

schemaVersion 固定为字符串 1，共享 Pydantic 严格模型是 CLI、MCP、核心与文档样例的单一契约来源；对外字段统一 camelCase。方案尚未发布，因此本次完善业务理解契约不形成已有格式迁移。

| 对象 | 必需内容 |
| --- | --- |
| Plan | name、sourceRefs、businessUnderstanding、coverage、cases、可选 supportingContext/identityProfiles；增补含 parentPlanRef/changeReason |
| SupportingContext | id、text、source=conversation/host-source/note、可选 locator；直接随计划保存 |
| BusinessUnderstanding | summary、actors、resources、states、flows、rules、dataDependencies、assumptions、conflicts、unknowns；无项时空列表并说明资料限制 |
| BusinessFlow / Rule | 稳定 id、描述、operationIds/状态关系、basis、evidenceRefs；basis 区分 contract/source-inference/observation/human-requirement |
| DataDependency | 生产接口及字段、消费接口及位置、用途、生命周期、evidenceRefs |
| Coverage | 流程→case、规则→assertion、数据依赖→capture/请求字段；未覆盖项列原因 |
| Case | id、kind= functional/performance、steps、cleanup；steps 由 Http/Branch/Poll/Load 构成 |
| IdentityProfile | id、描述、credentialRefs（别名→环境 secretBindings 名称）、evidenceRefs；不保存凭据值 |
| HttpStep | id、operationId、request、captures、assertions、可选 identityRef/dependsOn；role 可为 authentication/prepare/exercise，默认 exercise |
| BranchStep | id、kind=branch、predicate、thenSteps、elseSteps、声明的输出；执行前冻结两个分支及各自范围 |
| PollStep | id、kind=poll、requestStep、until、intervalMs、maxAttempts、timeoutMs；只允许明确配置为只读的操作 |
| Predicate | 字面值/已捕获变量/当前轮询响应字段的类型化比较及 all/any/not 组合；不接受脚本 |
| LoadStep | id、kind=load、operationId、request、assertions、loadProfile、thresholds、可选 identityRef |
| Request | pathParams、query、headers、JSON body；method/path 从目录获取 |
| Capture | 变量名、status/header/jsonPointer 来源、敏感性和用途 |
| Assertion | id、查询、操作符、预期、evidenceRefs |
| CleanupStep | HttpStep 加 requires 变量列表，在 finally 中按声明顺序执行 |
| ProjectConfigDraft | configRef、环境/目标、凭据绑定名称、资料声明、操作/预算草案、已有边界引用；不直接赋权 |
| ReviewBundle | planRef、configRef、业务理解与依据摘要、覆盖、写操作/清理清单、负载预算、冲突未知项、父版本差异、拟审核范围 |
| ApprovalRecord | reviewRef、reviewBundleRef、planRef、configRef、scopeHash、审核人标识、决定、时间、来源 CLI/conversation、确认消息引用/必要摘录、意见；独立于 Plan |

首版功能检查：状态码/允许集合、header、响应耗时阈值、JSON Pointer 存在性/类型/相等/不等/数量/范围/正则、JSON Schema。跨步骤/接口字段关系使用捕获变量比较，不接受任意表达式。

正则使用 operator=regex、expected 为无斜杠分隔符的模式字符串，采用 ECMAScript Unicode（u）搜索语义；完整格式检查使用 ^ 和 $。模式最长 512 字符，送审前检查语法；只匹配 JSON/header 字符串，缺失或其他类型不通过。HTTP 通过 regress 绑定匹配，同一响应的正则检查放入最多 1s 的受 guardian 保护子进程；超时归为执行错误，仍以捕获变量清理。k6 在初始化时编译 RegExp(u)，匹配受负载执行总时限约束。
固定 k6 运行时对 Unicode 属性转义（\p{...}/\P{...}）匹配不正确，因此负载计划在送审前拒绝此语法，使用显式字符范围或改为功能检查；不能静默执行并产生错误结论。

状态与身份测试沿用相同通用请求/断言：业务规则描述允许或禁止的状态变化，case 执行动作后检查状态和数据；负例用明确错误预期。identityRef 选择计划中声明的凭据绑定，token 仍通过显式鉴权步骤捕获并按身份隔离。缺少所需身份在送审前列缺口，不能借用另一个身份计为通过。

认证捕获使用 purpose=authentication，按用例及 identityRef 保存，始终脱敏且不写入账本值。同名 token 在不同身份下不共享；省略 identityRef 使用独立的默认作用域。其他业务捕获按用例共享；secret=true 只控制脱敏和账本持久化，敏感资源标识仍可按已审核计划传给其他身份请求。HTTP、负载、finally 和修复清理使用相同作用域；恢复鉴权前驱按身份和变量名一起匹配，旧会话残留保留身份。

BranchStep 仅按已存在变量选择一个预先审核的分支，未选分支为 skipped，原因 not_selected，不记为通过；后续使用的输出必须在所有可能路径定义，或有可校验的保护条件。校验拒绝循环依赖、不支持的动态动作和不明确的变量来源。

当前分支契约要求显式 thenSteps、elseSteps 和 outputs；输出形如 {name, identityRef?}，认证变量保留身份，业务变量省略身份。两个路径分别验证、预算取最大值，外部只能直接使用已声明且两边均可提供的输出。其他路径变量通过 HTTP/Poll 的 requires 或 exists 条件保护；缺输入跳过该动作，清理仍列为未解决。分支条件不读取当前响应，需先捕获字段。路径引用采用 case.branch.then.step / case.branch.else.step，dependsOn 省略 case 前缀；嵌套沿用此形式。每 case 最多 2000 个步骤、8 层。运行前持久保存选择，修复沿原路径鉴权，不重新判断条件或使用未选路径；中断报告合并父分支已包含的事件，避免重复统计。

PollStep 的 requestStep 必须同时在 runAllow 和 pollAllow 内；轮询只读性不能仅按 GET 判断。中间响应作为观测，until 满足后执行最终数据断言并导出声明变量；网络/协议/断言错误按计划策略记录，不能把错误统一当成“继续等待”。达到次数或时间上限即失败。分支按最坏路径、轮询按最大次数，加上鉴权/清理计算请求与耗时预算；不提供无限循环或运行时 Agent 生成新步骤。

当前轮询契约：requestStep 是 role=exercise 的 HttpStep，identityRef 放在其中；至少一条非推断的状态断言作为每次响应守卫，其他断言仅在 until 满足后执行。条件使用 kind=compare/all/any/not；比较的左右操作数为 kind=literal/variable/response，分别读取原样 JSON、先前捕获变量和当前响应的 JSON Pointer/header/status。字面值不插值，所有条件输入都会检查；缺字段须显式使用 exists，否则报错。条件树最多 64 个节点、8 层；intervalMs 为 1–10000，maxAttempts 为 1–1000，timeoutMs 为 1–60000。超时包含请求、间隔和最终检查，取消可中断只读轮询后进入 finally。覆盖与账本使用外层 poll.id；中间观测不计为通过，最终检查失败仍保留可用于清理的捕获。

首版 LoadStep 支持状态码、header、JSON Pointer 的上述标量/数量检查；功能响应 JSON Schema 由核心验证器执行。LoadStep 请求尚未支持的断言必须在校验阶段报错，不能静默遗漏 JSON Schema 或其他检查。

动态值采用 ${vars.recordId}、${run.id}、${secrets.password}。完整占位符保留原值类型，嵌入文本时转字符串；占位符前再加一个美元符号表示字面量。仅做字段查找，不执行 JS；缺失变量会阻塞依赖步骤。

功能计划示例形态如下，来源标识由注册工具返回值替换，示例并非现有可执行快照。下面的 Record/value 来自永久 fixture；平台不预置它们。业务理解是此计划的一部分，审核报告由同一结构渲染，不能另外生成一份与执行内容脱节的介绍：

~~~yaml
schemaVersion: "1"
name: record-lifecycle
sourceRefs: [spec_snapshot, requirements_snapshot]
businessUnderstanding:
  summary: 测试用户通过鉴权创建独立记录，读取应保留写入值，最后删除记录并注销会话。
  actors: [{id: test-user, description: 使用配置凭据的测试用户}]
  resources: [{id: record, description: 运行内创建并清理的记录}]
  states:
    - {resourceId: record, values: [absent, created, deleted], evidenceRefs: [contract-create, contract-delete]}
  flows:
    - id: record-lifecycle
      description: 鉴权后创建、读取并删除记录，最后撤销 token。
      operationIds: [login, createRecord, getRecord, deleteRecord, logout]
      basis: contract
      evidenceRefs: [contract-login, contract-create, contract-read, contract-delete, contract-logout]
  rules:
    - {id: preserve-value, description: 详情中的 value 等于创建时提交的 value, operationIds: [createRecord, getRecord], basis: contract, evidenceRefs: [contract-value]}
  dataDependencies:
    - {id: record-id, producer: createRecord, sourceJsonPointer: /id, consumers: [getRecord, deleteRecord], purpose: 读取与清理, lifecycle: case, evidenceRefs: [contract-create, contract-read, contract-delete]}
  assumptions: []
  conflicts: []
  unknowns: []
coverage:
  flows: [{flowId: record-lifecycle, caseIds: [lifecycle]}]
  rules: [{ruleId: preserve-value, assertionRefs: [lifecycle.read.value-roundtrip]}]
  dataDependencies: [{dependencyId: record-id, captureRef: lifecycle.create.recordId, consumerStepRefs: [lifecycle.read, lifecycle.delete, lifecycle.verify-deleted]}]
  uncovered: []
cases:
  - id: lifecycle
    kind: functional
    steps:
      - id: login
        operationId: login
        role: authentication
        request:
          body: {username: "${secrets.username}", password: "${secrets.password}"}
        captures:
          accessToken: {jsonPointer: /token, secret: true, purpose: authentication}
        assertions:
          - {id: login-status, status: 200, evidenceRefs: [contract-login]}
      - id: create
        operationId: createRecord
        request:
          headers: {Authorization: "Bearer ${vars.accessToken}"}
          body: {name: "ci-${run.id}", value: 42}
        captures:
          recordId: {jsonPointer: /id, secret: false, purpose: resource-cleanup}
        assertions:
          - {id: create-status, status: 201, evidenceRefs: [contract-create]}
      - id: read
        operationId: getRecord
        request:
          headers: {Authorization: "Bearer ${vars.accessToken}"}
          pathParams: {id: "${vars.recordId}"}
        assertions:
          - {id: read-status, status: 200, evidenceRefs: [contract-read]}
          - {id: value-roundtrip, jsonPointer: /value, operator: eq, expected: 42, evidenceRefs: [contract-value]}
    cleanup:
      - id: delete
        operationId: deleteRecord
        requires: [accessToken, recordId]
        request:
          headers: {Authorization: "Bearer ${vars.accessToken}"}
          pathParams: {id: "${vars.recordId}"}
        assertions:
          - {id: delete-status, status: 204, evidenceRefs: [contract-delete]}
      - id: verify-deleted
        operationId: getRecord
        requires: [accessToken, recordId]
        request:
          headers: {Authorization: "Bearer ${vars.accessToken}"}
          pathParams: {id: "${vars.recordId}"}
        assertions:
          - {id: deleted-status, status: 404, evidenceRefs: [contract-delete]}
      - id: logout
        operationId: logout
        requires: [accessToken]
        request:
          headers: {Authorization: "Bearer ${vars.accessToken}"}
        assertions:
          - {id: logout-status, status: 204, evidenceRefs: [contract-logout]}
~~~

性能 case 单独登录和创建资源，使用相同 request/capture/cleanup 结构，中间放 LoadStep，不能复用已清理的功能资源。LoadStep：

~~~yaml
id: concurrent-read
kind: load
operationId: getRecord
request:
  headers: {Authorization: "Bearer ${vars.accessToken}"}
  pathParams: {id: "${vars.recordId}"}
assertions:
  - {id: read-status, status: 200, evidenceRefs: [contract-read]}
  - {id: value-roundtrip, jsonPointer: /value, operator: eq, expected: 42, evidenceRefs: [contract-value]}
loadProfile:
  executor: shared-iterations
  vus: 5
  iterations: 100
  maxDurationMs: 30000
thresholds:
  latencyP95Ms: {lte: 1000}
  httpErrorRate: {lte: 0}
  dataCheckFailureRate: {lte: 0}
  completedIterations: {eq: 100}
~~~

上述参数仅为工程验收示例，实际业务配置自己的 SLO。有测量无阈值、样本缺失或执行不足不能直接判性能通过。

### 4.2 校验与结果

校验依次检查：Schema/版本/ID → 业务理解、依据与覆盖映射 → 操作与来源 → 参数及变量依赖 → 断言依据 → 操作、负载、清理和预算 → 引擎与凭据。负例须明确 negative 标记及错误预期，不能用正例参数校验把所有负例拒绝掉。

业务理解不是靠关键字存在就算合格：引用的流程、规则、接口、case、step、capture、assertion 必须真实存在且归属当前快照。未覆盖项必须有原因，未知项要说明影响。程序校验结构和引用，不能宣称证明 LLM 的业务推断正确；语义由人工审核。

推断但未确认的预期保留 tentative 标记，CI 中不算已确认通过。校验不静默修正计划或删除检查；不支持的能力返回具体问题。

- Run 状态：queued、running、cleaning、reporting、completed、cancelled、interrupted。
- Step：passed、failed、blocked、skipped、error、inconclusive；清理结果单列。
- 总体 verdict：passed、failed、error、inconclusive；阻塞/跳过不计入通过。
- 结果关联 runId、planRef、sourceRefs、环境和引擎版本、实际请求/响应证据、预期/实际值。
- 结果同时关联 reviewRef、scopeHash、业务流程/规则与用例覆盖；记录实际被测服务版本（可获知时），区别于计划分析时的源码版本。
- 性能统一输出：配置/观测并发、完成迭代、请求数、requests/s、延迟 avg/P50/P95/P99/max（ms）、HTTP 错误率、数据检查失败率、阈值和样本数。内部率为 0～1，显示为百分比。
- 后续通过或清理成功不覆盖先前失败；功能、数据、性能错误同时保留。

### 4.3 人工审核与执行门槛

保存不等于批准，校验通过不等于批准。审核状态是围绕不可变 planRef 的独立记录：validated → awaiting_review → approved / rejected；新版本不会继承父版本的 approved 状态。原版记录与结果保留，人工可撤销后续运行许可。各状态变化记录操作者、时间和原因。

审核材料由已保存计划确定性渲染为 review.md + review.json，包含：

1. LLM 的业务理解、流程图/顺序、规则、状态和数据依赖，以及资料/源码定位。
2. 流程与用例、规则与断言的映射，未覆盖、推断、冲突和未知项。
3. 具体请求链路、数据生成和捕获、写操作、清理动作、失败处理。
4. 鉴权引用、目标环境、最大请求数、并发/迭代规模、阈值。
5. 父版本差异、增补原因及触发证据；审批显示完整 planRef，避免审核后内容被替换。

ReviewGate 在任何正式请求发出前检查：计划哈希一致 → 审核决定有效 → 目标环境与 scopeHash 匹配 → 项目策略仍允许 → 运行参数与预算未改变。MCP start_run 与 CLI run 共用此入口，无审核时返回 review_required，不启动业务请求或负载进程。只读探测沿用独立 probeAllow，不借此执行正式计划中的写操作。

scopeHash 包含项目身份、环境标识及解析后的目标 origin/base path、允许操作和负载/超时预算、相关执行策略。凭据值、运行 UUID、响应提取变量不在审批哈希中，允许凭据轮换和每次生成隔离数据。修改计划内容、目标地址或相关策略需要重新送审；不能通过 CLI 临时覆盖并发或参数绕过审核。单次运行使用固定快照，启动后策略变化不能扩大它的权限；取消仍可生效。

审核一个版本后，允许它在同一审核范围内多次运行，包括 CI。部署了新代码本身不会使回归计划失效：计划引用的是分析时的源码快照，每轮记录当前被测版本，发现资料/契约漂移写入报告。重新分析或更新计划引用的资料则产生新版本，重新审核。人工批准未知项表示接受本次测试范围限制，不自动把源码推断变成需求事实或把 tentative 断言变成已确认通过；人工补充需求须登记为新依据并更新计划。

审核入口并列支持 CLI 与 Agent 对话。先展示 reviewBundleRef 对应的业务摘要、版本、范围、写操作、负载和未知项；用户明确确认该材料后，Agent 可调用 horus_record_review_decision 记录已发生的人工决定，并携带 planRef/scopeHash、决定来源和确认消息引用/必要摘录。此工具用于落盘人工决定，不授权 Agent 自行作出决定。明确版本的对话确认无需再执行 CLI 批准；一般测试意图、模糊确认或旧版批准均不能作为新版依据。CLI 保留直接人工批准、拒绝和撤销入口。业务 CI 只消费已有记录，不产生新批准。

首版的信任边界是用户控制的本地项目、按 Skill 行事的宿主 Agent 和受信 CI 输入，不是同一操作系统中人与 Agent 的强隔离。对话记录由宿主/Agent 提交，首版只校验版本、范围与记录完整性，不能凭消息引用或姓名独立证明人工身份，来源标记 local_recorded。CI 从受保护分支或受控 artifact 取得审核包，不接受本轮 CI 自动产生的批准文件；文件哈希仅用于一致性。若需抵御恶意同权进程或强制验证身份，需独立审核服务或签名授权，属于后续团队能力。

取消或失败后的清理沿用该次运行开始时的已审核 cleanup 快照和清理账本，仍受当前项目策略约束；无需批准一个新的业务计划才能处理已有资源。不得借 cleanup_run 创建新业务资源或补跑未批准动作。

## 5. 执行与证据策略

### 5.1 Hurl

首版每个 HTTP 步骤调用一个 Hurl 进程，核心管理变量与 finally，使资源 ID 能及时登记、中间失败能清理。接受进程启动开销；功能总耗时与 HTTP 耗时分别记录，不用功能执行吞吐量冒充压测结果。只有测量出瓶颈才考虑批量执行优化。

使用 asyncio.create_subprocess_exec 和参数数组，不使用 shell。编译器生成受控 Hurl 和断言 source map；JSON Pointer 转换为受控 JSONPath。步骤：

1. 解析变量和凭据、检查范围，生成受保护临时文件。
2. 用 --json 读取机器结果，用私有 --report-json 目录读取原始响应 body。
3. 从实际响应执行声明的提取，登记确认创建的资源，再归并断言结果。Hurl 在隐式状态断言失败时可能不执行 captures，不能只靠它决定是否清理。
4. 资源登记须来自声明创建步骤、实际成功响应与已解析标识。写入超时或非成功响应归为结果不明，不猜 ID、不自动重复写入。
5. 脱敏后保存证据，删除原始临时产物；敏感运行变量仅保留在 Worker 内存。

源码已确认：Hurl 8.0.1 的 --json 有意保留未脱敏值；其 JSON report 会保存原始 body。二者均不能直接输出到 MCP、CI 日志或公开 artifact。

临时目录 0700、文件 0600。凭据不进入 argv。stdout/stderr 有界采集后脱敏；单份证据默认上限 2 MiB，截断影响判断时标证据不足。连接默认 5s、请求默认 15s，可在项目上限内调整。

引擎退出码、实际响应和断言一起归类；语法/调用异常与被测系统不符合预期分开。请求 body 从已解析、序列化的实际输入保存，不能假设 Hurl JSON 自带完整请求 body。

### 5.2 k6

- 用 Hurl 在负载前完成该 case 鉴权和资源准备；负载后由核心 finally 清理。
- 首版 shared-iterations，固定 VU、固定迭代总数、最大时长；每迭代一个指定请求及相应断言。
- 一次 LoadStep 使用一个 k6 进程，请求不经 MCP 往返，不逐请求启动 Hurl。
- 受控模板仅导入内置模块；运行输入用受限环境传递，禁止任意用户 JS/远程 import。
- 预期状态按计划定义，HTTP 错误与数据检查失败分别统计。200 但字段错误仍须使数据阈值失败。
- 不自动重试、redirects=0；准备、清理和启动耗时不进入被测请求延迟。
- handleSummary 输出官方机器格式，以固定 k6 2.3.0 及官方 summary Schema 归一化；不用旧版 v1 字段路径，不解析终端文字。
- 指标包含 P50/P95/P99，缺样本/未完成迭代/超时不能通过。阈值失败与引擎故障分开。
- 每个 VU 最多保留前 5 个必要失败样本，经核心脱敏；不保存全部压测响应，不声称采样即全量证据。
- 性能 case 独占本项目负载时段，不同时跑本项目其他 case。基础压测不承诺容量上限、长期稳定性或不同硬件间可比性。

### 5.3 调度、取消与清理

case 内按依赖执行，失败阻塞后续依赖，始终进入 cleanup；独立功能 case 默认并行 2 个，受项目上限约束。性能 case 独占。

cleanup 按声明顺序执行，某步失败不阻止其他具备条件的清理。缺少变量解释跳过原因。只读轮询可配置有界重试；写操作不自动重试。

运行使用已审核的总体时限；到期停止新增业务动作，进入独立有界 cleanup，再进入 reporting。测试失败、部分阻塞、引擎异常和用户取消均保留原判定并自动生成 summary.json/report.json/report.md/junit.xml 及报告引用，不要求额外执行 report 命令。文件存储故障须返回可定位的报告写入错误，不能假报报告已生成。

修复清理需要有效鉴权；原 token 不再可用时，仅重新执行计划中显式标记 role=authentication 且被 cleanupAllow 允许的鉴权步骤，不重跑 prepare/exercise。鉴权失败则报告清理阻塞。

MCP start_run 必须带 reviewRef 和 requestId，由审核记录取得固定 configRef，不隐式选取“最近批准”的配置；原子登记该项目的请求、参数摘要、runId 后由独占认领的独立 Worker 执行，完成启动握手再返回。相同 requestId/参数重试返回原 runId，参数不同返回 request_conflict；客户端收不到响应时不能新开一轮写入。CLI 同样通过核心的幂等启动入口。

Worker 每 2s 更新心跳，独立进程会话并脱离 MCP 的 stdio/父进程生命周期；macOS/Linux 正常关闭宿主会话后的持续执行须实测。wait_run 超时或等待调用被取消不等于取消运行；只有 cancel_run 写入取消标志、停止新增业务动作并终止负载。已发送的有界 HTTP 请求先完成捕获，再执行独立有界清理。

引擎 guardian 通过管道监测 Worker 生存，Worker 消失即停止并回收引擎；guardian 继承项目锁，旧引擎退出前不允许同项目新运行进入。超过 30s 无心跳且进程已不存在，查询标记 interrupted。强杀、断电或网络不可达时不保证清理成功；账本列出残留/不明资源。cleanup_run 使用已有账本及当前环境鉴权，不重新创建资源，不自动恢复可能重复写入的中间步骤。负载的 gracefulStop 设为 0，Worker 硬超时为配置 maxDuration 加 5s，然后进入清理；取消信号及非预期进程退出仍分别记录。

意外终止后的下一次查询或清理入口从事件与账本重建中断报告，不自动恢复写操作。reporting 不等待 LLM，AI 解释作为可选追加内容；新补测的待审核状态不影响本次运行完成或报告交付。

## 6. CLI、MCP 和 Skill 接口

### 6.1 CLI

当前 CLI 与核心共用审核和运行逻辑，输出 JSON：

```sh
horus version
horus skill
horus mcp --workspace /absolute/path/to/workspace --read-root /absolute/path/to/docs
horus plan approve --workspace <workspace> --project-ref <projectRef> --bundle-ref <reviewBundleRef>
horus plan export --workspace <workspace> --project-ref <projectRef> --review-ref <reviewRef> --out reviewed.json
horus plan import --workspace <ci-workspace> --bundle reviewed.json
horus run --workspace <workspace> --project-ref <projectRef> --plan-ref <planRef> --review-ref <reviewRef> --env test
horus status --workspace <workspace> --project-ref <projectRef> --run-id <runId>
horus cleanup --workspace <workspace> --project-ref <projectRef> --run-id <runId> --request-id <cleanupRequestId>
```

CLI approve 完整展示材料后，等待用户输入“我确定”。日常 Agent 流程通过 MCP
记录实际对话确认，无需再运行 CLI。run 默认等待，`--no-wait` 返回 runId，
两者均使用固定审核范围；不允许命令行临时改目标、参数或并发。

资料接入、计划生成和取消目前走 MCP；修复清理共用原审核及账本，doctor 入口仍待实现。
仅安装基础 wheel 即可回放审核包，不需要 FastMCP 或模型客户端。

退出码：0 全部要求检查通过；1 功能/数据/性能阈值或清理失败；2 计划/配置/策略/依赖/引擎错误；3 只有阻塞/待判定、审核缺失/无效，或取消/中断。并存时优先级 2、1、3、0，报告仍保留全部结果。

### 6.2 MCP 工具

| 名称 | 输入 | 返回 |
| --- | --- | --- |
| horus_get_capabilities | 无 | 版本、资料/执行能力、引擎就绪状态、工作区限制与流程指引 |
| horus_list_projects | query、cursor、limit | projectRef、摘要及分页 |
| horus_open_project | 项目名称或 projectRef、createIfMissing | projectRef、持久上下文摘要、已有配置/资料/计划/审核/运行引用 |
| horus_stage_project_config | 环境/目标、凭据绑定名称、资料声明、边界与预算草案 | configRef、缺口、范围摘要；不会直接启用权限 |
| horus_import_api | URL | sourceRef、定义与操作目录；多定义时返回候选 |
| horus_register_source | kind=openapi/markdown、uri | 本地允许根目录中的文档快照 |
| horus_get_context | sourceRef、可选 operationId/path、offset、limit | 准备状态/摘要；就绪后接口/说明/源码片段与依据 |
| horus_save_plan | 含 businessUnderstanding/coverage 的 plan、parentPlanRef、changeReason | planRef、校验及差异；不会批准 |
| horus_get_plan | planRef、offset、limit<=200 | 项目内固定计划的 JSON 分页；校验内容哈希，读取不代表展示审核包或批准 |
| horus_validate_plan | planRef | 结构/引用问题、覆盖缺口、凭据变量名称、执行规模 |
| horus_prepare_review | planRef、configRef、environment | reviewBundleRef、材料 artifactRef、scopeHash、awaiting_review |
| horus_record_review_decision | reviewBundleRef、planRef、scopeHash、决定、来源、用户确认引用/必要摘录 | 已记录的人工决定、reviewRef 与 local_recorded 来源；不能代表 Agent 自行批准 |
| horus_start_run | planRef、reviewRef、environment、requestId | 审核通过时 runId/状态，重复请求返回同一运行；否则阻塞/冲突原因 |
| horus_get_run | runId | 持久状态；结束时含报告引用 |
| horus_wait_run | runId、waitMs<=30000 | 完成或有界超时；完成时含摘要和报告引用 |
| horus_get_artifact | artifactRef、offset、limit | 计划审核材料或运行证据的脱敏内容/片段 |
| horus_cancel_run | runId | 取消请求状态 |
| horus_cleanup_run | 原 runId、requestId | 独立清理运行；沿用原审核、账本和 cleanupAllow，不重跑业务准备；缺失凭据或不明结果保留在报告中 |
| horus_attach_resource_metrics | runId、metrics | 附加已审核的可选 SSH 资源观测，保留 HTTP 判定 |

除能力查询、项目列表和 open_project 外，业务工具均显式带 projectRef，表内省略重复字段。项目不由宿主当前目录隐式推断；读文件仅限工作区或启动时用 --read-root 注册的只读根目录，资料/artifact 引用验证项目归属。工具不接受明文凭据或任意本机路径。默认列表 20 条，上限 100，大内容分页。save_plan 不接受审核字段；人工决定只能对已展示、待审核且版本/范围匹配的材料独立记录。对话确认引用是审计记录，不宣称是平台独立验证的身份凭据。

正常测试失败和等待审核作为业务结果，不能使用 isError 冒充工具损坏；参数、非法操作、对象读取错误才是工具错误。简短 content 加 structuredContent，读写提示正确标注，范围和审核由核心执行。

共用返回字段 schemaVersion/projectRef/status/summary/refs/issues/nextActions/payload，无项目的查询使用 projectRef=null。问题含机器 code、字段位置、原因、修复建议及是否需用户补充；nextActions 是建议，不授予批准。测试失败不触发盲目重试。审核/报告 artifact 可经 MCP 读取，不能只返回客户端打不开的服务端路径。

只依赖普通 tools/list、tools/call，不要求 sampling、专属 UI 或客户端长任务扩展。通过两客户端实测确认基本协议兼容性，不能用标准标签替代验收。

实现时以 FastMCP("horus") 注册类型化 async 工具，通过 Pydantic 返回模型生成 outputSchema/structuredContent，函数只做参数转换和调用 core 服务，不包含业务判断。stdio 的 stdout 仅用于协议，日志进入 stderr；不启动 Web 服务、数据库或模型客户端。大任务返回 runId，由 Worker 处理，避免长工具调用被宿主超时中断。

初始化 instructions 及 get_capabilities 提供自包含的简明主流程和约束，工具说明保留必要上下文；不能依赖 MCP 客户端自动安装/激活 Skill。引擎缺失由能力检查返回具体问题，MCP 仍可握手、接入资料与规划；首次依赖安装预热在一次性接入阶段完成，不占握手等待。

### 6.3 Skill 与 CI

通用 Skill 名称 horus，随包分发于 skills/horus/。流程为：自动接入资料/源码/边界 → 业务理解、链路、数据与配置草案 → 必要只读探测 → 保存、校验、集中展示审核材料 → 用户确认具体版本 → 记录人工决定并检查 → 启动、自动等待 → 呈现事实报告与解释 → 可选新版单独送审。共享正文及 Codex/Claude 配置，不写死业务。对话已有明确确认时 Agent 可落盘该决定，不能自行决定、伪造确认或把一般测试意图当成未展示版本的批准。

Skill 在当前会话可用时调用 wait_run，以每次最多 30s 的有界等待获取进展，完成后自动取摘要与报告；用户无需手动轮询 runId 或导出。会话关闭后 Worker 仍产出报告，重连可检索。准备不足在送审前集中说明；补测不让当前已批准任务无限延长。

交付 Codex 和 Claude Code 各自的 MCP 配置与 Skill 安装/发现说明，Python 3.12、uv 和工作区使用明确路径，凭据通过配置好的运行环境传递。宿主工具超时需大于 wait_run 的单次等待；宿主授权和 horus 业务审核分别处理，不修改客户端安全设置来掩盖链路问题。具体示例见 Agent + MCP 链路文档。

业务 CI 使用 Python 3.12 与 uv 安装固定版本基础 wheel、恢复 Hurl/k6 二进制缓存、导入已审核包、注入测试凭据、执行该版本、始终上传脱敏产物并使用退出码。目标为已部署测试环境，内网用自托管 Runner；不要求安装 FastMCP extras、启动 MCP/LLM、克隆业务源码或启动后端/数据库。Python 依赖用带哈希的发布约束锁定；缓存键包含系统、架构、Python 3.12 和锁定版本。

plan export 包含计划、所需 OperationCatalog/Schema 快照、依据摘要、审核记录和完整性 manifest，不包含凭据或完整源码仓库。CI 从受信来源取得包，import 验证哈希及引用闭包，保留已审核目标和配置。配置或审核不匹配立即阻塞，不从聊天重建步骤，也不要求 CI 重新分析代码。撤销通过受信版本库/产物更新传播，首版离线 CI 不承诺在线即时撤销。

按用户确认，本阶段在本地启动永久 fixture 验证平台，不配置 CI 流水线。产物为 summary.json、report.json、report.md、junit.xml；失败证据保存在报告内，首条交付不强制 HTML/报告服务。已审核计划仍可通过无模型的 CLI 回放。

## 7. M1 首个正式交付及验收

### 7.1 永久 reference-api

用 Python 标准库 ThreadingHTTPServer 实现真实 HTTP fixture，资源/token 存内存并对并发访问加锁，无额外数据库或 Web 框架。提供 OpenAPI 3.1、明确业务约定和源码，永久保留于 tests/fixtures/reference-api。

- GET /healthz：可只读探测，200。
- POST /auth/token：用环境提供的测试凭据获得 token；POST /auth/logout：撤销 token。
- POST /records：写入 name、value，返回随机 id 和原值，201。
- GET /records/{id}：返回写入数据，200；不存在 404。
- GET /records：列表保留真实值，供后续一致性增补测试。
- DELETE /records/{id}：删除测试资源，204。

明确约定 value 必须保持写入值。数据故障模式使详情仍返回 200、value 却加 1；列表保留正确值。延迟模式只对被测读取操作注入固定延迟。模式由 fixture 启动参数设定，不进入平台核心。

### 7.1.1 第二个永久 workflow-api

在 tests/fixtures/workflow-api 提供独立的工单后端、OpenAPI、源码与明确业务规则，同样用 Python 3.12 标准库实现真实 HTTP。它使用不同的字段与流程，不共享 reference-api 的业务配置或计划。

- 两个测试身份 owner 和 outsider 分别登录，token 隔离；owner 可管理自己的工单，outsider 读取/修改该工单应返回 403。
- 创建工单得到 draft 状态；提交返回 202 和 jobId，读取 job 状态从 pending 到 done，工单最终为 submitted。
- 读取状态后可按预声明条件分支进入轮询或最终验证；轮询有次数/时间上限，不靠任意固定长 sleep 计为通过。
- draft 允许更新；submitted 禁止再次提交或修改受限字段，返回 409，随后读取确认状态与字段没有非法变化。
- 提供经约定的工单/关联 job 删除和会话注销操作；测试资源按本轮变量和账本清理。
- 固定故障模式包括越权返回 200、非法状态修改被接受、job 一直 pending；预期依据来自业务约定，不从故障实现反向生成“正确”预期。

Agent 从该后端自身资料生成身份、分支、轮询、状态与关系断言。平台核心、执行器和 Skill 不得出现按工单/record 名称分支的特例。

### 7.2 首条完整交付

1. 真正的 Codex/Claude 经 horus Skill/FastMCP 读取文档与源码，将业务流程、规则、数据依赖、依据和覆盖映射写入计划，包含功能 case 和性能 case。
2. 保存并校验计划，展示审核材料。先证明未审核版本不能正式执行，再由人工审核具体版本与功能/负载范围。
3. 功能：登录 → 创建 → 查询/数据断言 → finally 删除 → 查询确认 404 → 注销。
4. 性能：独立登录/创建 → 5 VU、100 次迭代读取及检查 → 汇总阈值 → 独立清理/确认/注销。
5. 自动清理并输出带业务覆盖与 reviewRef 的报告，Agent 自动等待、呈现；关闭 Agent，通过 CLI/CI 重跑同一审核版本，仍自动出报告，不需要模型 key 或 MCP 在线。
6. 首轮报告完成后，Agent 可基于列表/详情冲突保存有 parentPlanRef 的补充计划，展示差异并再次人工审核，再开启新运行；不使用已删除 ID、不修改预期以规避错误。
7. M1 内接入 workflow-api，仅更换资料、环境、凭据与配置，自动生成并执行状态/异步/权限链路，覆盖分支和有界轮询；平台核心不修改。此项是正式通用性验收，不另建 PoC。

Agent 验收使用干净项目，仅提供被测资料/源码/策略，不向 Agent 提供黄金计划。确定性回归另保留已核对计划，CI 不每轮依赖模型。分别验证“会生成”“能审核”“能稳定执行”。自动回归可构造标记为测试专用的审核记录以覆盖分支，不能冒充真实人工审核验收；M1 的 Agent 验收必须保存实际审核过程及批准版本。

### 7.3 验收矩阵

| 情况 | 通过要求 |
| --- | --- |
| 业务理解与覆盖 | 计划包含来源明确的流程/规则/数据依赖，能定位用例和断言；人工可查看并提出修改 |
| 自动接入 | 仅提供资料、地址、凭据与边界；Agent 生成请求、链路、捕获、断言和范围建议，用户不手写脚本 |
| 对话审核 | 当前对话明确确认展示版本后能执行，无需 CLI 再次批准；保留决定来源和版本引用 |
| MCP 首次闭环 | Codex/Claude Code 从空工作区经 MCP 建项目、配置草案、资料、规划到报告，正常测试不借助 CLI/shell 补缺 |
| MCP 连接与发现 | 两客户端真实发现工具和已安装 Skill；未安装 Skill 也能靠 MCP 指引完成基本流程 |
| 重试与恢复 | 启动响应丢失后同 requestId 只对应一个实际运行；等待超时不重启，正常会话关闭后 Worker 继续，重连恢复相同引用 |
| 项目隔离 | 多会话经同一服务操作不同项目，配置、批准、变量和报告引用不串用 |
| 未审核/拒绝/撤销 | MCP/CLI/CI 发正式请求前阻塞，返回原因及退出 3；只读探测仍按原策略处理 |
| 审核后内容或范围变化 | 修改业务理解/断言/参数/负载、目标环境或策略，旧批准不适用；不能静默执行 |
| 正常 | 功能/压测通过、100 迭代完成、指标存在、资源/token 清理、退出 0 |
| 200 但数据错 | 功能失败，HTTP 错误率可为 0，但数据检查失败率大于 0；退出 1、清理完成 |
| 延迟超标 | 专用阈值明确低于注入延迟；记录 P95/样本/阈值失败，退出 1、清理完成 |
| 越范围/负载超限 | 发请求前拒绝、明确原因、退出 2 |
| 缺鉴权/来源 | 运行前定位缺失，不创建资源、不假报通过 |
| 中途失败 | 依赖阻塞、独立 case 继续、finally 清理、原失败不丢失 |
| 自动收尾 | 失败/部分阻塞/超时仍自动清理和生成报告，返回报告引用；LLM 解释失败不阻塞事实报告 |
| 状态与权限 | workflow-api 的越权 200、非法状态动作检出；关联断言有契约依据，其他身份不能复用 owner token |
| 条件与等待 | 执行已审核分支，未选项不算通过；pending 到 done 正确轮询，一直 pending 有界失败并出报告 |
| 跨业务 | 两个后端使用独立资料/计划，仅改接入资产；核心没有按业务名称或字段写的特殊逻辑 |
| 取消/强杀 | 正常取消尽力清理，强杀留 interrupted/账本，无自动重复写 |
| 敏感数据 | CLI/MCP/JSON/Markdown/JUnit/artifact 无测试密码/token 原值，不上传原始引擎目录 |
| 跨入口 | 同一计划 MCP/CLI 判定规则和字段一致，动态 ID 可不同 |
| 增补 | 当前运行先出报告；新版有原因及依据，单独待审核，新批准不覆盖旧记录，新资源重新准备 |

## 8. 实施任务与工程检查

所有任务属于正式 M1，不另设 PoC 阶段：

| 任务 | 交付及验收 | 依赖 |
| --- | --- | --- |
| T1 工程和契约 | Python 3.12/uv/wheel、严格模型；业务理解/覆盖/身份/分支/轮询/审核契约及引用错误定位 | 无 |
| T2 永久 fixtures | reference-api 与 workflow-api 的资料/源码/HTTP、正常及数据/权限/状态/等待/延迟故障 | T1 |
| T3 接口导入和上下文 | OpenAPI/Swagger URL 与 UI 解析、三种规范目录、可选宿主源码/聊天依据、范围草案和集中缺口 | T1 |
| T4 Hurl 适配器 | 编译/机器结果/脱敏/source map，真实响应与资源 ID 可取 | T1–T3 |
| T5 功能执行核心 | ReviewGate、身份隔离、分支/轮询/预算、变量/依赖/finally/账本；未批准零正式请求，失败能清理 | T4 |
| T6 k6 适配器 | 基础负载/官方结果/阈值/采样，数据错和延迟检出 | T2、T3、T5 |
| T7 自动报告和 CLI | 审核材料/人工入口/包导入导出、自动报告和退出码；异常/超时也出结果，无 Agent 重跑 | T5、T6 |
| T8 MCP | 空工作区启动/项目与配置草案、FastMCP 工具/分页/错误/指引、幂等启动/恢复、人工决定记录/有界等待；client 经进程实测 | T3、T7 |
| T9 Skill 和双客户端 | 各自配置及 Skill 安装发现；真实 Codex/Claude Code 不借助 CLI/shell 补缺，完成两后端 MCP 全链路并留证 | T8 |
| T10 本地集成验证 | Python 3.12 + uv、基础 wheel/引擎缓存、审核包回放、两后端故障矩阵与脱敏产物、增补独立审核；不配置 CI | T7–T9 |

检查点 A：T1–T5 先接通 reference-api 首条链路、审核门槛和失败清理，再接入控制步骤；资产继续用于交付。检查点 B：T6–T8 功能/压测、CLI/MCP 同源，人工决定记录及自动报告可用。M1 完成：T9–T10 两后端真实生成、实际人工审核、批准后自动收尾、无 Agent 回放、压测阈值、新版独立审核和本地集成验证均有证据。仅有 MCP、CLI 或 Skill 任意一个不算完成。

实施时建立命令：

~~~sh
uv python install 3.12
uv sync --python 3.12 --frozen --extra mcp --group dev
uv build
uv run ruff check .
uv run mypy src
uv run pytest tests/unit
uv run pytest tests/integration
uv run pytest tests/acceptance -m 'not agent'
uv run python -m tests.acceptance.prepare_agent
~~~

build 产出 wheel；Ruff/mypy 检查静态质量，mypy 的 python_version 为 3.12；unit 验证解析/作用域/编码/编译/分支轮询预算/身份隔离/审核范围/归类；integration 启动两个真实 fixtures、Hurl、k6 和 stdio MCP client；acceptance 跑自动接入、审核、数据/权限/状态/等待/延迟、自动报告、清理与脱敏矩阵。prepare_agent 只准备真实客户端任务与证据清单，不能把准备脚本成功当成模型验收或人工审核成功。uv.lock 首次在 T1 解析生成，之后上述 --frozen 才可执行。

优先覆盖错误判定、重复写入、数据残留、凭据泄漏分支。对固定引擎输出保留小型脱敏样本作适配回归，不支持的新格式明确报错。禁止 eval/shell 拼接；边界校验外部输入，内部用已校验类型。核心没有针对 fixture 字段的特例。

首个交付后再扩展阶梯加压、长稳、并发写竞争、多协议、远程 MCP、团队服务和插件分发。

## 9. 核实依据

- [Hurl 8.0.1](https://github.com/Orange-OpenSource/hurl/releases/tag/8.0.1)
- [Hurl JSON 未脱敏行为](https://github.com/Orange-OpenSource/hurl/blob/8.0.1/packages/hurl/src/output/json.rs)
- [Hurl JSON 字段和响应文件](https://github.com/Orange-OpenSource/hurl/blob/8.0.1/packages/hurl/src/json/result.rs)
- [状态断言/capture 顺序](https://github.com/Orange-OpenSource/hurl/blob/8.0.1/packages/hurl/src/runner/entry.rs)
- [Python FastMCP 4.1.0](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.0)
- [FastMCP slim 服务端依赖与许可证声明](https://github.com/PrefectHQ/fastmcp/blob/v4.1.0/fastmcp_slim/pyproject.toml)
- [FastMCP 类型化工具](https://gofastmcp.com/servers/tools)
- [Prefect FastMCP TypeScript](https://github.com/PrefectHQ/fastmcp-ts)
- [另一实现 punkpeye/fastmcp 的协议提示](https://github.com/punkpeye/fastmcp)
- [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [OpenAPI Spec Validator](https://github.com/python-openapi/openapi-spec-validator)
- [OpenAPI Schema Validator](https://github.com/python-openapi/openapi-schema-validator)
- [k6 2.3.0](https://github.com/grafana/k6/releases/tag/v2.3.0)
- [k6 shared-iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/shared-iterations/)
- [k6 官方机器结果 Schema](https://github.com/grafana/k6-summary)
- [k6 阈值](https://grafana.com/docs/k6/latest/using-k6/thresholds/)
