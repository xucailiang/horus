# horus

<img src="docs/assets/horus-logo.png" alt="horus logo" width="100%" />

[简体中文](README.zh-CN.md) · [Setup](docs/usage.md)

**Turn your coding agent into a testing workspace.**

horus is an AI-native API testing platform. Testers can use natural language in
their existing coding tools to understand business behavior, design tests, run
functional and basic load checks, and assess the results.

- **Lightweight.** Use your coding agent's existing model and tools, without
  deploying a separate large testing platform.
- **Local.** Run the MCP server and test engines on your machine. Plans, execution
  records, and reports are saved locally.
- **Portable.** Connect any coding tool with local MCP support through the standard
  protocol. Load the Skill natively or have the agent read it directly.

Import your API documentation from a URL or a local file. The agent reads it,
maps the business workflows and call dependencies, and builds a test plan around
the risks and expected results you describe. After you confirm its scope and
checks, horus runs the tests, cleans up test data, and returns results with evidence.

```text
API docs + Business goal → Understand call flows → Test plan → Your confirmation → Tests + cleanup → Results
```

## Features

- **Understand business workflows from API documentation.** Map operations and
  call dependencies, then use requirements and optional source context to design
  checks. Plans make their evidence, assumptions, and gaps visible.
- **Verify complete workflows.** Prepare isolated data, pass values between calls,
  check permissions, state changes, conditional paths, and asynchronous results,
  then clean up. Hurl checks response data as well as HTTP status.
- **Check speed and correctness under load.** Run k6 with reviewed concurrency,
  iterations, time limits, and thresholds. Measure latency, request errors, data
  errors, and completed work. Authorized SSH resource observations are optional.
- **See what was tested and what failed.** Read a short report organized by business
  goal, with key metrics and Mermaid diagrams. Follow a failure to its checks and
  request/response evidence. Unrun paths and unresolved cleanup remain visible.
- **Keep and repeat each test.** Each run stores its approved plan and results
  together. Export and replay reviewed plans through the CLI without a model.
  Interrupted runs support a separate cleanup attempt.

The current alpha runs on macOS and Linux and tests HTTP/REST APIs with JSON,
regardless of the backend's programming language. See
[current limits](docs/architecture.md#current-limits).

## Quick start

Copy this into your coding agent:

```text
Follow https://github.com/xucailiang/horus/blob/master/docs/usage.md to install
horus, configure its MCP server and Skill in this coding tool, and verify the connection.
```

The agent handles dependencies, MCP configuration, and Skill installation using
the [setup guide](docs/usage.md). Manual steps are in the same guide.

Codex and Claude Code have been verified; other MCP clients use the same server
and workflow.

Once installed, start a test:

```text
Use horus to test <service URL>. Import the API documentation from <URL or local file path>.
Understand the business rules and call dependencies, then propose functional
and basic concurrency tests, including data preparation and cleanup.
Show me the plan and load limits first. After I confirm, run it and return the report.
```

The agent shows the plan directly in the conversation before saving or executing
it. Confirmation applies to that version and scope; changes require another review.

## Reports

Each run has one directory containing its plan and results. Markdown documents
follow the conversation language:

| File | Contents |
| --- | --- |
| `plan.md` | Confirmed plan, business rules, and full Mermaid call graph |
| `report.md` | Verdict, problems, business results, key metrics, and a compact business diagram |
| `details.md` | Step checks and execution evidence |

The directory also includes the executable plan, full redacted results in JSON,
and a JUnit report. Unrun paths and unresolved cleanup are reported explicitly.

## Documentation

- [Setup, configuration, and CLI usage](docs/usage.md)
- [Architecture and limits](docs/architecture.md)

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for environment setup and local checks. Keep the English and Chinese READMEs in sync.

## License

[MIT](LICENSE)
