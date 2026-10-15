# horus

AI-native testing, in the tools you already use to build software.

[简体中文](README.zh-CN.md) · [Setup](docs/usage.md) · [Test workflow](docs/testing.md) · [Architecture](docs/architecture.md)

Codex, Claude Code, and other coding agents give us a place to read code, discuss
requirements, and change how a system works. horus brings testing into that same
conversation. Describe the behavior you want to verify, give the Agent an API
definition and the context it needs, then review the tests it proposes. After you
confirm the plan, horus runs it and brings back the results.

Much of the work in a useful test happens before the first request. Which
operations belong together? Where does the next request get its ID? Which values
must stay consistent? What needs cleaning up if a check fails? horus asks the Agent
to make those decisions explicit, with their reasons and uncertainties, so you
can check its understanding before it acts.

That understanding becomes an executable plan. The plan can be inspected and run
again after the conversation has ended. The report records what was actually
checked and gives the Agent evidence to investigate alongside the code. This is
the working relationship horus is built around: people set the intent and approve
the scope, agents reason about the system, and the execution tools check the
agreed behavior.

```text
OpenAPI / Swagger → Agent plan → Human confirmation → Hurl + k6 → Report
```

## What works

Start with an OpenAPI/Swagger definition URL or a common Swagger UI link. Add
business requirements in the conversation; source code is optional and can be read
with the host's existing tools. Expectations come from requirements and interface
contracts, with source findings recorded as supporting evidence. The backend can
be written in Rust, Go, TypeScript, Python, or another language: tests operate over
HTTP.

The Agent writes a plan that connects business rules to requests, data, assertions,
and cleanup. It also sets the load and explains what remains untested. The Skill
guides this process and asks you to reply **“我确定”** after showing the exact plan
and target. That decision applies to the version you reviewed; changes need
another review.

horus provides the tools through MCP and uses Hurl for HTTP checks and k6 for basic
load testing. A Worker follows the approved plan, keeps state and variables within
each run, and produces JSON, Markdown, JUnit, and MCP-readable reports. It can keep running
through a host disconnect, recognize a repeated start request, and stop its
engines if the Worker dies. Cancellation and cleanup have explicit time limits.
Reviewed plans can also be exported, imported, and replayed through the base CLI
without MCP or a model.

After an interrupted run, an explicit cleanup attempt can use recorded resource
IDs and the original approved actions. Lost credentials and uncertain writes stay
visible in the report; recovery cannot promise that every resource was removed.

If resource usage matters to the test, you can authorize the host Agent to collect
CPU, GPU, and memory observations over SSH. A short baseline can inform the proposed
load, and runtime observations can accompany the report. Approved concurrency
stays fixed unless you review a revised plan.

The project runs locally and keeps its state in files. It uses the host's model
and context, a packaged Skill for the workflow, and existing execution engines.

## Quick start

Use Python 3.12 and uv 0.9.11. The engine installer also needs GitHub CLI (`gh`).

```sh
git clone https://github.com/xucailiang/horus.git
cd horus
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
uv run horus mcp --workspace /absolute/path/to/test-workspace
```

The installer downloads official Hurl 8.0.1 and k6 2.3.0 releases and verifies their
SHA-256 checksums. macOS and Linux are supported. To use matching binaries already
on your machine, set `HORUS_HURL_BIN` / `HORUS_K6_BIN`.

Configure MCP and install the Skill separately. Run `uv run horus skill` to find
the bundled Skill, then follow the [Codex / Claude Code setup guide](docs/usage.md).

## Development and verification

Checks run locally; the project has no hosted CI pipeline.

```sh
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
uv build
uv run python -m tests.acceptance.verify_wheel
```

The permanent HTTP fixture exercises data correctness, cleanup, load thresholds,
and interrupted runs. The wheel check repeats normal and failing scenarios in a
clean environment without FastMCP or a model. The [test workflow](docs/testing.md)
distinguishes these regressions from actual Agent generation and human approval.

## Project status

horus is in early development. The first execution flow and base-wheel replay have
been verified locally; the full M1 milestone is still open. Native acceptance in
both clients, including actual human confirmation, and a second workflow backend
remain to be completed. Identity profiles, branches, polling, parallel functional
cases and regular-expression assertions are also pending.
Automated fixture approvals do not count as human acceptance.

The Chinese [requirements](docs/PRD-horus.md),
[technical design](docs/TECHNICAL-DESIGN.md), and
[implementation notes](docs/IMPLEMENTATION.md) describe that scope and the current
evidence. `horus_get_capabilities` reports what the installed version can do.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow. Contributions
and documentation are welcome in English and Chinese. horus is licensed under
[MIT](LICENSE).
