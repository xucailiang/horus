# Test and acceptance workflow

Tests serve two different purposes: deterministic regressions verify execution;
real host acceptance verifies that an Agent can generate a plan, show it to a
human, and execute only after actual confirmation. Neither substitutes for the other.

## Automated checks

```sh
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
uv build
uv run python -m tests.acceptance.verify_wheel
```

The standard-library HTTP fixture is permanent regression infrastructure. Its
business contract is independent of fault modes. Tests cover:

- Authentication, isolated creation, read/data assertions, deletion, and logout.
- A separate fixed 5-VU/100-read scenario with latency and correctness thresholds.
- HTTP 200 with incorrect data, excessive latency, and HTTP failures.
- Review gates, immutable references, scope mismatch, and cross-project isolation.
- Idempotent starts, disconnect/reconnect, cancellation during cleanup, and engine
  shutdown when a Worker is killed.
- Secret redaction without corrupting execution status or asset references.
- Direct OpenAPI URLs and common Swagger UI discovery patterns.
- Reviewed-package integrity and replay in a clean base-wheel environment.

Regression approvals are explicitly `test-only`. Normal execution rejects them
unless `HORUS_TEST_MODE=1`; the MCP decision tool does not expose this source.
They are never counted as actual human approval.

## Base-wheel / CI replay

`tests.acceptance.verify_wheel` creates a clean Python 3.12 environment, exports
hash-locked base dependencies from `uv.lock`, and installs the built wheel without
MCP extras. It verifies packaged Skill/JavaScript files, imports an existing review
package, and runs normal and data-error scenarios through the CLI.

The expected exit codes are 0 and 1 respectively, and both runs must remove their
records and revoke their sessions. No model API, MCP server, or business source
checkout is needed. This checks the platform; ordinary business CI targets an
already deployed test service and does not have to launch the fixture.

## Real Codex / Claude Code acceptance

1. Launch a persistent sample API:

   ```sh
   uv run python -m tests.acceptance.prepare_agent
   ```

   Keep that process alive. It writes the target URL to `.horus-demo/fixture.json`
   and private fixture credentials to ignored `.horus-demo/credentials.json`.
   Pass credentials into the MCP process environment; never paste them into chat.

2. Configure an isolated native host session using [the setup guide](usage.md).
   The helper copies the packaged Skill into local Codex/Claude skill directories.
   Use a clean horus workspace and the host's existing model authentication.

3. Give the Agent only the API definition URL, business requirements, target,
   credential binding names, and limits. Do not provide `tests/support.py` or a
   regression plan as its answer. Optional source findings may be read with host
   tools and added to `supportingContext`.

4. Ask it to generate, save, validate, prepare, and fully read a review. Verify the
   business understanding, exact references, variable syntax, target, writes,
   cleanup, fixed load, thresholds, and uncovered behavior before showing the plan.

5. The Agent asks the user whether resource monitoring is wanted. If enabled,
   follow the Skill's authorized SSH/baseline workflow. Include that arrangement
   in the reviewed plan. Skipping monitoring does not block HTTP testing.

6. Show the concrete plan and ask for **“我确定”**. Record only an actual response
   approving that version. Before confirmation, assert that no test actions were
   sent to the target. An OpenAPI-document fetch is not a test execution.

7. Execute once with a stable request ID, wait automatically, and present the
   report. Check actual requests, data checks, performance metrics, and remaining
   resources/sessions. Close and reconnect the native client during another
   approved run to validate host lifecycle behavior.

Repeat independently in both clients. Preserve plan/review/run references and
redacted evidence. A client authentication error, an invalid generated draft, or
an SDK-only round trip must be reported as incomplete native acceptance.

## Remaining acceptance work

The first deterministic slice is implemented and locally verified. Complete
two-client native generation and actual human approval, a second workflow-shaped
backend, identity/branch/poll behavior, and explicit repair cleanup before claiming
the full M1 milestone. See [implementation notes](IMPLEMENTATION.md) for current
evidence and gaps.
