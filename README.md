# horus

**Agent-planned, human-reviewed HTTP API testing.**

[简体中文](README.zh-CN.md) · [Setup](docs/usage.md) · [Test workflow](docs/testing.md) · [Architecture](docs/architecture.md)

horus turns an API definition and business requirements into a reviewable test
plan, deterministic execution, cleanup, and a factual report. Codex or Claude Code
handles understanding and planning; MCP connects the host to the execution core,
and a packaged Skill guides the complete workflow.

```text
OpenAPI / Swagger → Agent plan → Human confirmation → Hurl + k6 → Report
```

Any HTTP backend can be tested, including Rust, Go, TypeScript, and Python services.
Source code is optional: the host can read it with its existing tools. Relevant
chat material or source findings go directly into plan context.

## What works

- Import OpenAPI/Swagger definition URLs and common Swagger UI links.
- Save typed business understanding, evidence, coverage, requests, assertions,
  captured variables, cleanup, and fixed load in an immutable plan.
- Display the exact plan and scope, then ask the user to reply **“我确定”** before
  recording approval. A general request to test is not approval.
- Execute HTTP checks with Hurl and basic fixed-VU load with k6; validate response
  data as well as status codes and latency.
- Run in a detached Worker with idempotent starts, bounded cancellation/cleanup,
  reconnection, project isolation, and engine shutdown after Worker death.
- Produce JSON, Markdown, JUnit, and MCP-readable reports automatically.
- Export/import reviewed packages and replay them through the base CLI without
  MCP or a model.
- Optionally attach authorized SSH CPU/GPU/memory observations collected by the
  host Agent. Baselines inform proposed load; approved concurrency stays fixed.

## Quick start

Use Python 3.12 and uv 0.9.11. The engine installer also needs GitHub CLI (`gh`).

```sh
git clone https://github.com/xucailiang/horus.git
cd horus
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
uv run horus mcp --workspace /absolute/path/to/test-workspace
```

The installer downloads official **Hurl 8.0.1** and **k6 2.3.0** releases and checks
SHA-256 before installing them locally. macOS and Linux are supported. Existing
matching binaries can be selected with `HORUS_HURL_BIN` / `HORUS_K6_BIN`.

MCP configuration and Skill installation are separate steps. Run
`uv run horus skill` to locate the bundled Skill, then follow the
[Codex / Claude Code setup guide](docs/usage.md).

## Development and verification

```sh
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
uv build
uv run python -m tests.acceptance.verify_wheel
```

The permanent HTTP fixture verifies lifecycle cleanup, data errors, latency
failures, load thresholds, review gates, disconnects, cancellation, and crash
behavior. The wheel check runs normal and failing scenarios in a clean environment
without FastMCP or a model. See [the test workflow](docs/testing.md).

## Project status

**Early development; M1 is not complete.** The first execution slice and base-wheel
replay are locally verified. Complete native two-client generation and actual
human approval, a second workflow backend, identity/branch/poll steps, parallel
functional cases, regular-expression assertions, and explicit repair cleanup
remain open. Automated fixture approvals do not count as human acceptance.

Detailed Chinese [requirements](docs/PRD-horus.md),
[technical design](docs/TECHNICAL-DESIGN.md), and
[implementation notes](docs/IMPLEMENTATION.md) describe the intended milestone
and current evidence. Use `horus_get_capabilities` for implemented capabilities.

## Contributing and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the development and review workflow.
Contributions and documentation improvements are welcome in English and Chinese.
Licensed under [MIT](LICENSE).
