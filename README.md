# horus

<img src="docs/assets/horus-logo.png" alt="horus logo" width="100%" />

[简体中文](README.zh-CN.md) · [Setup](docs/usage.md)

**Turn your everyday coding tool into an AI testing workspace.**

horus is a lightweight, AI-native API testing platform that works inside your coding
tool. Import your API documentation and describe what you want to verify in plain
language. AI helps you understand business workflows and design tests; after your
approval, horus runs functional and basic load tests, cleans up test data, and
delivers a clear report.

You focus on business rules, risks, and what should count as a pass. Your coding
agent helps turn those decisions into tests, all in the same conversation.

- **Lightweight.** Reuse your coding agent and its model. Get started without
  deploying a separate large testing platform.
- **Local.** Run tests from your machine and keep plans, execution records, and
  reports in your own workspace.
- **Works across tools.** Use horus in any coding tool with local MCP support,
  and keep working in the environment you already know.

```text
Import docs → Understand business call flows → Review the plan → Run tests → Cleanup + report
```

## Features

- **Turn API documentation into a business test plan.** Share an online link or a
  local file. The agent connects calls and their dependencies, uses your requirements
  and optional source context, and shows what to test, why, and what still needs
  clarification.
- **Test the whole workflow.** Connect login, data creation, state changes, and
  asynchronous results. Check who can access or change data, whether the returned
  values are correct, and whether each business rule holds. Preparation and cleanup
  are part of the plan.
- **Check correctness as well as speed under load.** Agree on concurrency and load
  limits, then see how many requests completed, how long they took, and whether
  requests failed or returned incorrect data.
- **Get results you can act on.** See which business checks passed, where they
  failed, and how the service performed. Business-flow diagrams show what was
  tested; detailed checks and request/response records help you investigate.
- **Keep each run ready for review and regression testing.** Plans and results stay
  together in a separate directory for each run. Repeat an approved plan within
  its original scope and compare the saved results.

The current alpha runs on macOS and Linux and tests HTTP/REST APIs with JSON,
regardless of the backend's programming language. See
[current limits](docs/architecture.md#current-limits).

## Quick start

Copy this into your coding agent:

```text
Follow https://github.com/xucailiang/horus/blob/master/docs/usage.md to install
horus in this coding tool and verify the connection.
```

Let the agent handle installation and setup. Manual installation and other client
configurations are in the [setup guide](docs/usage.md). Codex and Claude Code have
been verified; other MCP clients use the same workflow.

Then give it your documentation and a testing goal:

```text
Test <service URL> with horus. Import the API documentation from <URL or local file path>.
Map the business call flows and propose functional and basic load tests.
Show me what you will check, what counts as a pass, and the load limits. Wait for my confirmation.
Then run the tests, clean up the data, and report the business results, failures, and key metrics.
```

Review the plan directly in the conversation before it is saved or run. If the
plan or load changes, you review it again.

## Reports

Start with a short report: what was tested, what passed, what failed, how the
service performed, and whether cleanup finished. A business-flow diagram connects
the results to the original plan. Unrun checks and unresolved cleanup are stated
explicitly.

Every run keeps its plan and results together. The documents follow your
conversation language:

| File | Contents |
| --- | --- |
| `plan.md` | What you agreed to test, expected results, limits, and the business-flow diagram |
| `report.md` | Business results, problems, key metrics, and a compact business-flow diagram |
| `details.md` | Detailed checks and request/response records for investigation |

## Documentation

- [Setup, configuration, and CLI usage](docs/usage.md)
- [Architecture and limits](docs/architecture.md)

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for development setup and local checks.

## License

[MIT](LICENSE)
