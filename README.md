# horus

AI-native testing, in the tools you already use to build software.

[简体中文](README.zh-CN.md) · [Setup](docs/usage.md) · [Test workflow](docs/testing.md) · [Architecture](docs/architecture.md)

horus is an AI-native API testing platform that works through coding agents such
as Codex and Claude Code. You can discuss a feature, work on its implementation,
and test it in the same conversation. Describe what should happen, provide an API
definition and the relevant business context, then review the Agent's proposed
plan. Once you confirm it, horus carries out the tests and returns the results.

A useful test starts with an understanding of how the system should behave.
Creating a resource may involve ownership rules, values that must survive later
reads, and data that needs cleaning up after a failure. The Agent connects these
rules to concrete requests and checks, explains its reasoning, and identifies
what it still does not know. You review both its understanding of the system and
how it intends to verify it.

That work remains useful after the conversation ends. The plan keeps the expected
behavior and its basis together, so you can inspect it, revise it as requirements
change, and run it again. The report records what happened, what failed, and what
was left untested. The Agent can use that evidence to investigate the code and
help decide what to check next. horus is built to make the context used to develop
software useful for verifying it, too.

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

The Agent uses horus through MCP. A bundled Skill guides it through understanding
the system, designing the tests, and delivering the report. The plan includes the
business rules and their evidence, requests, test data, assertions, cleanup, load,
and gaps in coverage. After showing the exact plan and target, the Skill asks you
to reply **“我确定”**. Your confirmation applies to that version and scope; changes
need another review.

A plan can switch between declared identities to check ownership and access rules.
Each identity keeps its own authentication values; case data such as resource IDs
can connect their requests.

Once approved, a Worker follows the plan, prepares the data, runs HTTP checks with
Hurl and basic load tests with k6, then cleans up and produces a report. State and
variables are isolated between runs. You can read the report in the conversation
or use its JSON, Markdown, and JUnit files. Reviewed plans can also be exported,
imported, and replayed through the base CLI without MCP or a model.

A run can continue after the host disconnects, and a repeated start request returns
the existing run. If the Worker dies, its engines are stopped. Cancellation and
cleanup have explicit time limits.

After an interrupted run, an explicit cleanup attempt can use recorded resource
IDs and the original approved actions. Lost credentials and uncertain writes stay
visible in the report; recovery cannot promise that every resource was removed.

If resource usage matters to the test, you can authorize the host Agent to collect
CPU, GPU, and memory observations over SSH. A short baseline can inform the proposed
load, and runtime observations can accompany the report. Approved concurrency
stays fixed unless you review a revised plan.

horus runs locally and keeps its state in files. The host Agent reasons with its
existing model, conversation, and source-reading tools. horus executes the
reviewed plan and keeps the results available for inspection.

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

The repository includes permanent HTTP test backends for checking data correctness,
ownership, cleanup, load thresholds, and interrupted runs. The wheel check repeats
normal and failing scenarios in a clean environment without FastMCP or a model.
The [test workflow](docs/testing.md) explains how these regressions differ from
acceptance with an actual Agent and human approval.

## Project status

horus is in early development. API import, plan review, HTTP checks, basic load
testing, cleanup reports, and base-wheel replay have been verified locally. A
second test backend exercises ownership checks and load testing across identities.

The first milestone, M1, is still open. The full workflow in both native clients,
including actual human confirmation, remains to be accepted. Branches, polling,
parallel functional cases, and the second backend's state and asynchronous
workflows are also unfinished. Automated fixture approvals do not count as human
acceptance.

The Chinese [requirements](docs/PRD-horus.md),
[technical design](docs/TECHNICAL-DESIGN.md), and
[implementation notes](docs/IMPLEMENTATION.md) describe that scope and the current
evidence. `horus_get_capabilities` reports what the installed version can do.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow. Contributions
and documentation are welcome in English and Chinese. horus is licensed under
[MIT](LICENSE).
