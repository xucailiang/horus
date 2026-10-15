# horus

[简体中文](README.zh-CN.md) · [Setup](docs/usage.md) · [Architecture](docs/architecture.md)

horus is an AI-native API testing platform for Codex and Claude Code, connected
through MCP. It brings business understanding, API call dependencies, functional
checks, and basic load tests into the conversation where you develop your service.

Provide an OpenAPI/Swagger definition and the business behavior you want to verify.
The agent proposes a test plan for you to review. After you confirm it, horus runs
the tests with Hurl and k6, cleans up test data, and returns the report.

```text
OpenAPI / Swagger → Agent plan → Human confirmation → Hurl + k6 → Report
```

## Features

- **Business logic and call chains.** The agent connects requirements and API
  contracts to requests and checks, with optional source context. Plans record
  their evidence, assumptions, and gaps.
- **Functional testing.** Verify response data, ownership rules, state changes,
  conditional paths, and asynchronous workflows, including data preparation and cleanup.
- **Load testing.** Set concurrency, iterations, and limits before execution.
  Check latency, HTTP errors, data errors, and completed iterations. Resource
  observations over SSH are optional and require authorization.
- **Repeatable execution.** Runs keep separate state and results. Reviewed plans
  can be exported and replayed through the CLI without MCP or a model. Interrupted
  runs support a separate cleanup attempt; uncertain outcomes remain visible.

horus runs locally and uses the host agent's existing model and tools. The current
alpha runs on macOS and Linux and tests HTTP/REST APIs with JSON, regardless of
the backend's programming language. See [current limits](docs/architecture.md#current-limits).

## Quick start

Requirements: Python 3.12, uv 0.9.11, and GitHub CLI (`gh`). Install from source:

```sh
git clone https://github.com/xucailiang/horus.git
cd horus
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
```

The installer downloads official Hurl 8.0.1 and k6 2.3.0 binaries and verifies their
SHA-256 checksums. To use matching binaries already installed, set
`HORUS_HURL_BIN` and `HORUS_K6_BIN`.

Follow the [Codex / Claude Code setup guide](docs/usage.md) to configure MCP and
install the bundled Skill. These are separate steps; `uv run horus skill` prints
the Skill's location.

Then ask your coding agent:

```text
Use horus to test <service URL>, with the API definition at <OpenAPI/Swagger URL>.
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
- [Testing and verification](docs/testing.md)
- [Architecture and limits](docs/architecture.md)
- [Requirements](docs/PRD-horus.md) and [technical design](docs/TECHNICAL-DESIGN.md) (Chinese)

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for environment setup and local checks. Keep the English and Chinese READMEs in sync.

## License

[MIT](LICENSE)
