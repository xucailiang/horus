# horus

<img src="docs/assets/horus-logo.png" alt="horus logo" width="100%" />

[简体中文](README.zh-CN.md) · [Setup](docs/usage.md) · [Roadmap](docs/roadmap.md)

**Test services from the user's perspective, within your coding tool.**

horus is a lightweight, AI-native API testing workspace for anyone who needs to
verify a service. Describe the user's goal and expected outcome. Your coding
agent uses API documentation, requirements, and optional source context to draft
a test plan. Once you approve it, horus runs functional checks and basic load
tests, cleans up test data, and produces a report.

You define the test goals, expected behavior, scope, and execution limits. Start
in plain language and refine the plan in more detail as needed. horus aims to
provide a professional testing workflow you can automate, audit, and adapt.

- **Lightweight.** Reuse your coding agent and its model, without deploying a
  separate large testing platform.
- **Local.** Run tests from your machine and keep plans, execution records, and
  reports in your own workspace.
- **Works across tools.** Connect horus to a coding tool with local MCP support
  and work in your usual environment.

```text
Describe a user goal + share docs → Review the scenario and limits → Run tests → Cleanup + evidence
```

## Features

- **Test planning.** Provide API documentation as a URL or local file. The agent
  maps API calls and dependencies using your requirements and optional source
  context, then proposes tests with their rationale and any questions to resolve.
- **Business workflow testing.** Combine login, data creation, state transitions,
  and asynchronous results. Verify access control, response data, and business
  rules, with data preparation and cleanup included in the plan.
- **Load testing with response validation.** Set concurrency and load limits,
  then check completed requests, response times, HTTP errors, and data correctness.
- **Reports and execution evidence.** Review business results, failures, and
  performance metrics. Workflow diagrams show the test scope; detailed checks
  and request/response records support investigation.
- **Repeatable execution.** Each run keeps its plan and results in a separate
  directory. Replay approved plans within their original scope and compare the
  saved results.

The current alpha runs on macOS and Linux and tests HTTP/REST APIs with JSON,
regardless of the backend's programming language. See
[current limits](docs/architecture.md#current-limits).

## Quick start

Copy this into your coding agent:

```text
Follow https://github.com/xucailiang/horus/blob/master/docs/usage.md to install
horus in this coding tool and verify the connection.
```

The agent can handle installation and setup. For manual installation and client
configuration, see the [setup guide](docs/usage.md). Codex and Claude Code have
been verified; other MCP clients use the same workflow.

Then give it your documentation and a testing goal:

```text
Test <service URL> with horus. Import the API documentation from <URL or local file path>.
Map the business call flows and propose functional and basic load tests.
Focus on what a user should accomplish, expected results, and relevant failure paths.
Show me what you will check, what counts as a pass, and the load limits. Wait for my confirmation.
Then run the tests, clean up the data, and report the business results, failures, and key metrics.
```

Review the plan directly in the conversation before it is saved or run. If the
plan or load changes, you review it again.

## Reports

The summary report covers the test scope, passed and failed checks, performance,
and cleanup status. A business-flow diagram connects results to the approved plan.
Checks that did not run and unresolved cleanup are listed explicitly.

Each run stores its plan and results together, with documents written in the
conversation language:

| File | Contents |
| --- | --- |
| `plan.md` | What you agreed to test, expected results, limits, and the business-flow diagram |
| `report.md` | Business results, problems, key metrics, and a compact business-flow diagram |
| `details.md` | Detailed checks and request/response records for investigation |

## Documentation

- [Setup, configuration, and CLI usage](docs/usage.md)
- [Architecture and limits](docs/architecture.md)
- [Product direction and roadmap](docs/roadmap.md)

## Direction

horus aims to make professional testing accessible to everyone, with user control
over test objectives and execution details. The [roadmap](docs/roadmap.md) focuses
on translating user goals into test designs, automating more complex scenarios,
and maintaining reusable tests as services evolve. User adoption and
maintainability guide the project.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for development setup and local checks.

## License

[MIT](LICENSE)
