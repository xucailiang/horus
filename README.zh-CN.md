# horus

<img src="docs/assets/horus-logo.png" alt="horus 标识" width="100%" />

[English](README.md) · [安装与使用](docs/usage.md)

horus 是一个通过 MCP 和通用 Skill 接入编程工具的 AI 原生接口测试平台。
在开发服务的同一段对话里，理解业务规则和接口调用关系，完成功能检查与基础并发测试。

提供 OpenAPI/Swagger 定义和需要验证的业务行为，Agent 据此提出测试计划。
你确认后，horus 用 Hurl 和 k6 执行测试，清理测试数据，再交付报告。

```text
接口定义 → Agent 设计计划 → 人工确认 → Hurl + k6 → 测试报告
```

## 功能

- **业务逻辑与调用链。** Agent 根据需求和接口约定设计请求与检查，必要时参考源码。
  计划记录依据、假设和未覆盖项。
- **功能测试。** 检查返回数据、资源权限、状态变化、条件分支和异步流程，包含数据准备与清理。
- **并发测试。** 执行前确定并发数、迭代次数和限制，检查延迟、请求错误、数据错误及完成次数。
  经授权后，可通过 SSH 补充目标机器的资源观测。
- **重复执行。** 每轮测试独立保存状态和结果。已审核计划可导出，通过 CLI 重跑，无需 MCP 或模型。
  中断后可单独尝试清理，结果不明的操作会保留在报告中。

horus 在本地运行，使用宿主 Agent 已有的模型和工具。目前为 alpha 版本，运行环境为 macOS 或
Linux；被测服务的编程语言不限，接口范围为 HTTP/REST + JSON。具体限制见
[架构说明](docs/architecture.md#current-limits)。

## 快速开始

把下面这段话发给你的编程助手：

```text
请按照 https://github.com/xucailiang/horus/blob/master/docs/usage.md 安装 horus，
在当前编程工具中配置 MCP 和 Skill，并验证连接。
```

Agent 会根据[接入指南](docs/usage.md)完成依赖安装、MCP 配置和 Skill 安装，手动步骤也在同一文档中。

支持本地 MCP 的编程工具均可接入。Skill 可通过客户端原生机制加载，也可让 Agent 读取
`skills/horus/SKILL.md`。Codex 和 Claude Code 已完成验证，其他客户端使用同一套服务和流程。

配置完成后，在编程 Agent 中发起测试：

```text
用 horus 测试这个服务：<服务地址>，接口文档：<OpenAPI/Swagger 链接>。
理解业务规则和接口调用依赖，设计功能测试与基础并发测试，包含数据准备和清理。
先直接在对话里给我看计划和负载范围，确认后再保存执行。
每轮计划和报告放在同一目录，用中文 Markdown 和 Mermaid 图展示业务逻辑与测试数据。
```

Agent 先在对话中展示计划，确认后才保存并执行。确认只适用于当前版本和范围；修改后需要重新审核。

## 测试报告

每轮测试的计划和结果放在同一目录，Markdown 文档的语言跟随当前对话：

| 文件 | 内容 |
| --- | --- |
| `plan.md` | 已确认的计划、业务规则和完整 Mermaid 调用图 |
| `report.md` | 结论、问题、业务结果、关键指标和业务简图 |
| `details.md` | 逐步检查与执行证据 |

同目录还保留可执行计划、完整的脱敏 JSON 结果和 JUnit 报告。
未执行的路径、尚未解决的清理问题会明确列出。

## 文档

- [安装、配置与 CLI 使用](docs/usage.md)
- [架构与限制](docs/architecture.md)

## 参与贡献

欢迎提交问题和 Pull Request。开发环境和本地检查见[贡献指南](CONTRIBUTING.md)。
修改 README 时，请同步中英文版本。

## 许可证

[MIT](LICENSE)
