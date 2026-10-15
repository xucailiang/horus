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

The standard-library HTTP fixtures provide permanent regressions. Their business
contracts are independent of fault modes. Tests cover:

- Authentication, isolated creation, read/data assertions, deletion, and logout.
- A second permanent workflow API verifies owner/outsider HTTP requests, load and
  cleanup with separate credentials. Its permission fault must fail the same plan.
  Crash recovery reauthenticates both identities without recreating tickets, revokes
  new sessions, and reports the old lost sessions by identity.
- A separate fixed 5-VU/100-read scenario with latency and correctness thresholds.
- A submitted job moves from pending to done through real Hurl polling. The same
  plan fails when the job stays pending or the service permits editing a submitted
  ticket. Cancellation stops polling and still deletes jobs/tickets and revokes sessions.
- Poll input scopes, status guards, attempt/time budgets, final capture export,
  predicate types/composition, and redaction of intermediate conditions.
- The workflow chooses direct verification or polling from an observed job state.
  Both paths, stuck jobs, illegal edits, cancellation, and crash recovery are covered;
  recovery must not run the alternative authentication path or resubmit work.
- Branch output guarantees, identity scopes, existence guards, nested references,
  unselected Poll reports, and a timeout that must block later writes while cleaning up.
- Functional case limits of one/two, independent credentials and IDs, cleanup holding
  a slot, ordinary failures, and performance barriers, observed by a real HTTP backend.
  Cancellation stops a poll while a sibling write finishes and cleans up; deadlines
  clean both active cases, and crash recovery does not start queued cases.
- HTTP 200 with incorrect data, excessive latency, and HTTP failures.
- Review gates, immutable references, scope mismatch, and cross-project isolation.
- Idempotent starts, disconnect/reconnect, cancellation during cleanup, and engine
  shutdown when a Worker is killed.
- Explicit cleanup after a Worker crash, denied reauthentication, missing resource
  IDs, authentication chains, and cleanup after an authentication assertion fails.
- Secret redaction without corrupting execution status or asset references.
- Reports retain fixed business context and coverage, map interrupted events to
  their original cases, and distinguish aggregate load evidence from individual
  assertion results. Step/assertion references cannot overwrite each other, and
  paged MCP reports include bounded failure evidence and sampling limits.
- ECMAScript regex assertions over headers and JSON strings, matching the same
  corpus in functional and real k6 runs, plus timeout followed by finally cleanup.
- Direct OpenAPI URLs and common Swagger UI discovery patterns.
- Reviewed-package integrity and replay in a clean base-wheel environment.

Regression approvals are explicitly `test-only`. Normal execution rejects them
unless `HORUS_TEST_MODE=1`; the MCP decision tool does not expose this source.
They are never counted as actual human approval.

## Base-wheel replay

`tests.acceptance.verify_wheel` creates a clean Python 3.12 environment, exports
hash-locked base dependencies from `uv.lock`, and installs the built wheel without
MCP extras. It verifies packaged Skill/JavaScript files, imports an existing review
package, and runs normal, data-error, both conditional paths of a two-identity
workflow, and parallel functional cases through the CLI. The workflow includes
polling, state checks, and independent load. The parallel fixture observes actual
overlap, the case limit, isolated credentials, and the barrier around load.

The expected exit codes are 0, 1, 0, 0, and 0 respectively. Every run must remove its
records/tickets and revoke its sessions. No model API, MCP server, or business source
checkout is needed. The same CLI can replay a reviewed plan against an already
deployed test service. The project uses local verification and has no hosted CI
pipeline.

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

The deterministic execution paths are implemented and locally verified. A sample
plan has passed after actual human approval, with MCP disconnect/reconnect during
the run. Native acceptance in both clients and a separately reviewed follow-up
plan remain open. See [implementation notes](IMPLEMENTATION.md) for the evidence
and gaps; the MCP round trip does not complete native client acceptance.
