# ADR-0001：共享 MCP/CLI 核心，组合 Hurl 与 k6

日期：2026-10-15。状态：部分由 [ADR-0002](0002-horus-fastmcp-and-plan-review.md) 替代。保留下述原始决策作为历史：TypeScript/Node 选型已改为 Python/FastMCP，计划增加业务理解及人工审核门槛；Hurl + k6、共享核心、文件存储及首条完整交付决定继续有效。

## 背景

平台应由 Codex/Claude 理解接口及业务资料，自动产生可追溯计划，并提供不依赖模型的 CI 回放。首个交付必须包含完整功能链路、数据断言、失败清理和基础并发压测。

目标是使跨业务通用性、执行可靠性和 CI 轻量同时成立，而不是改造完整测试管理平台。

## 决定

1. TypeScript/Node.js 24 单包，MCP stdio 与 CLI 调用同一核心；提供通用 Skill，专属插件包装后置。
2. 核心管理资料快照、计划、变量、权限范围、调度、清理账本和统一报告。计划是权威资产。
3. Hurl 8.0.1 作为功能执行依赖，以受控进程调用，不 fork；k6 2.3.0 作为基础压测执行依赖。
4. 首版功能请求按步骤调用 Hurl，及时提取变量和登记资源；压测在一个 k6 进程内执行，不逐请求产生进程或 MCP 往返。
5. 本地文件保存资料、计划与结果；CI 面向已部署环境，不引入数据库、消息队列、报告服务器或内置 LLM。
6. 首条正式交付使用永久 reference-api，包含正常、数据错误及延迟模式。没有独立 PoC 里程碑；交付代码、测试后端和回归用例全部继续维护。

## 取舍

- 直接 fork Keploy/MeterSphere 会引入当前核心流程不依赖的平台结构，且业务理解和规划部分仍需建设，因此不作为底座。
- Hurl 适合固定 HTTP 流程；完整压测指标、采样和负载应由专门执行器承担，因此选择 Hurl + k6，而不是把链路并行计为性能压测。
- Node + 两个外部二进制增加了安装项，但避免重写网络、断言和负载引擎。通过锁版本、已构建发布包和缓存减少 CI 成本。
- 每步骤 Hurl 进程具有启动与连接开销。它换取明确的状态和失败清理边界；性能测量由 k6 完成，后续有实测瓶颈再优化批处理。
- 文件存储足以支持本地单项目和 CI artifact。多人集中服务与跨机器调度需要后续独立设计，不提前建设。

## 关键实现依据

Hurl 8.0.1 原始 JSON 有意不脱敏，JSON report 还会写出响应 body；隐式状态断言失败可先于 capture 返回。因此执行器必须在受保护目录读取原始证据、独立登记可确认资源、再脱敏输出，不能直接把引擎产物上传或交给 Agent。

k6 v2 的机器 summary 与旧版字段有差异。适配器固定引擎及结果格式，并输出平台自己的稳定指标模型。

## 后果与验收

- CLI/MCP 必须通过同一结果规则，CI 运行已有计划不需要模型 key。
- 权限、只读探测和负载上限在代码中校验，不依赖 Skill 提示。
- HTTP 200 且字段错误时，功能与压测数据检查都应失败。
- 清理成功不能覆盖业务失败；强杀后的未清理资源必须可识别和修复。
- 对原始产物、临时文件和对外报告分别制定处理规则，验收不得出现测试 token/password 原文。
- 首个交付的完整验收与任务顺序见 [技术方案](../TECHNICAL-DESIGN.md)。

## 引用

- [Hurl 原始 JSON](https://github.com/Orange-OpenSource/hurl/blob/8.0.1/packages/hurl/src/output/json.rs)
- [Hurl capture 与断言顺序](https://github.com/Orange-OpenSource/hurl/blob/8.0.1/packages/hurl/src/runner/entry.rs)
- [k6 shared-iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/shared-iterations/)
- [k6 机器结果 Schema](https://github.com/grafana/k6-summary)
