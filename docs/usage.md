# Using horus with a host Agent

Install from this repository while the project is in early development:

```sh
git clone https://github.com/xucailiang/horus.git
cd horus
uv sync --python 3.12 --frozen --extra mcp --group dev
uv run python scripts/install_engines.py
uv run horus skill
```

Python 3.12, uv, GitHub CLI, and the pinned Hurl/k6 binaries are required for the
full development workflow. The installer verifies official release checksums.
Use `HORUS_HURL_BIN` / `HORUS_K6_BIN` for existing matching binaries.

## Connect MCP and install the Skill

These are separate steps. Replace every absolute path below. Keep the project
checkout separate from the persistent test workspace. Inject credential values
into the MCP process environment; tools receive binding names only.

Codex `config.toml`:

```toml
[mcp_servers.horus]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/horus", "run", "--frozen", "--python", "3.12", "--extra", "mcp", "horus", "mcp", "--workspace", "/absolute/path/to/workspace"]
env_vars = ["HORUS_USERNAME", "HORUS_PASSWORD"]
startup_timeout_sec = 30
tool_timeout_sec = 60
```

Claude Code `.mcp.json`:

```json
{
  "mcpServers": {
    "horus": {
      "type": "stdio",
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/horus", "run", "--frozen", "--python", "3.12", "--extra", "mcp", "horus", "mcp", "--workspace", "/absolute/path/to/workspace"]
    }
  }
}
```

Copy the directory reported by `horus skill` to the host's Skill directory:
`~/.agents/skills/horus` for Codex or `~/.claude/skills/horus` for Claude Code.
For an isolated local setup, use the equivalent project Skill directory. Follow
the host's normal tool authorization; horus approval does not bypass it.

Use `--read-root /absolute/path/to/docs` when the server needs to import local
OpenAPI/Markdown outside its workspace. Optional source reading uses the host's
existing tools and permissions. No repository import or source index is needed.

## The review-to-report workflow

Give the Agent an OpenAPI/Swagger definition or common Swagger UI URL, the target
environment, credential binding names, and the behavior to verify. Chat material
and optional source findings can become `plan.supportingContext`; cite each as
`context:<id>`. Interface evidence uses `sourceRef#Lx` line references.

The Agent opens a project, imports the API, stages a config, reads the plan schema,
generates business understanding and executable cases, and validates the plan.
Dynamic values use `${vars.name}`, `${secrets.binding}`, or `${run.id}`.
Coverage references are fully qualified: `caseId.stepId.assertionId`.

Use `operator: "regex"` to check a JSON Pointer or header string against a plain
ECMAScript regular expression in `expected`, without `/` delimiters. Matching uses
Unicode (`u`) semantics and searches within the string; use `^` and `$` when the
whole value must follow a format, such as `^INV-[0-9]{6}$`. Patterns are limited to
512 characters and checked before review. Missing and nonstring values fail.
Load checks reject Unicode property escapes (`\p{...}` / `\P{...}`) before review
because the pinned k6 runtime cannot match them correctly; use explicit ranges or
a functional check for those patterns. An HTTP response's regex checks have
a one-second matching-process limit; exceeding it reports an execution error and
still enters cleanup. Load matching remains within the approved k6 run limits.

It then prepares and reads the fixed review material, shows the exact target,
actions, cleanup, load, thresholds, and gaps, and asks you to reply **“我确定”**
to approve. Changes require a fresh review. General intent to test, or quoting
the confirmation phrase, does not authorize an undisplayed plan.

After the real decision is recorded, it starts once with a stable request ID,
waits in bounded calls, and retrieves the automatic report. Missing responses do
not justify starting a second run. Reports remain available after reconnection.

## Select identities

The Agent can declare `identityProfiles` when a test needs multiple accounts.
Each profile maps local credential aliases to names in the environment's
`secretBindings`. For example, this partial plan uses the `ownerUsername` and
`ownerPassword` bindings through the aliases `username` and `password`:

```yaml
identityProfiles:
  - id: owner
    description: The account that creates the resource
    credentialRefs:
      username: ownerUsername
      password: ownerPassword
    evidenceRefs: ["<sourceRef>#L1"]
```

Set `identityRef: owner` on its HTTP, load, and cleanup steps. The login body can
use `${secrets.username}` / `${secrets.password}`; subsequent authentication headers
can use `${vars.accessToken}`. Capture tokens and session credentials with
`purpose: authentication`. They stay private to the selected identity, are
redacted, and are omitted from persisted ledger values even if `secret` was false.
Two identities may use the same variable name without sharing its value.

Other captured data belongs to the case, so an outsider request can use an ID
created by the owner to check access restrictions. Mark sensitive business data
`secret: true` to redact it and omit its ledger value while preserving the planned
data flow. Cases keep separate data. A step without `identityRef` uses the default
scope, and does not inherit the previous step's identity or its credentials.

Unknown identities, aliases, bindings, and missing variable sources are rejected
before review. Changes to profiles or identity selections require another review.
Reports identify the selected identity for each step. Cleanup and recovery use
the same selections; unrecoverable old sessions are reported with their identities.

## Wait for asynchronous results

Use a poll step when an operation returns a job whose result arrives later.
Declare the observation operation in both `runAllow` and `pollAllow`. The latter
is an explicit statement that it is read-only and safe to repeat; HTTP method
alone cannot establish that. The reviewed limits cover every possible attempt.

This partial plan waits for a job already created by an earlier step:

```yaml
kind: poll
id: completion
requestStep:
  id: observe-job
  operationId: getJob
  identityRef: owner
  request:
    pathParams: {id: "${vars.jobId}"}
    headers: {Authorization: "Bearer ${vars.accessToken}"}
  captures:
    completedState: {jsonPointer: /state, secret: false, purpose: case-data}
  assertions:
    - {id: status, status: 200, evidenceRefs: ["<sourceRef>#L12"]}
    - {id: state, jsonPointer: /state, expected: done, evidenceRefs: ["<sourceRef>#L12"]}
until:
  kind: compare
  left: {kind: response, jsonPointer: /state}
  operator: eq
  right: {kind: literal, value: done}
  evidenceRefs: ["<sourceRef>#L12"]
intervalMs: 100
maxAttempts: 20
timeoutMs: 5000
```

Status assertions check every response. A rejected status or transport error ends
the poll immediately. Other assertions run after `until` becomes true, and only
that response exports captures. A final assertion failure still preserves captures
needed for cleanup. Coverage and dependencies use the outer id, such as
`caseId.completion.state`, rather than `observe-job`.

Predicates compare typed literals, prior case variables (`kind: variable`, `name`),
and current response JSON/header/status fields. They support `eq`, `ne`, `exists`,
`type`, `length`, `gte`, and `lte`, combined with `all`, `any`, or `not`. Literals
are exact values; they do not interpolate `${...}`. All inputs are evaluated.
Missing fields are errors unless explicitly checked with `exists`; invalid numeric
or length operands are errors too. A predicate cannot consume its poll's final
captures. Trees are limited to 64 nodes and depth 8.

The poll's timeout includes requests, intervals, and final checks. Exhausting either
the time or attempt limit fails the step, then enters normal cleanup. Declared
poll and load durations must fit the run time limit; other HTTP actions also share
that limit, while finally cleanup has its own budget. Reports retain the attempts
and observed conditions without counting pending responses as passing tests.

## CLI replay

The CLI uses the same core and review gate:

```sh
horus plan export --workspace <workspace> --project-ref <projectRef> --review-ref <reviewRef> --out reviewed.json
horus plan import --workspace <replay-workspace> --bundle reviewed.json
horus run --workspace <replay-workspace> --project-ref <projectRef> --plan-ref <planRef> --review-ref <reviewRef> --env test
horus status --workspace <replay-workspace> --project-ref <projectRef> --run-id <runId>
```

`run` waits by default; `--no-wait` returns a run ID. Export/import preserves the
approved target and scope, and excludes credential values. Inject credentials at
runtime. Changing the target or plan requires another review.

| Exit code | Meaning |
| --- | --- |
| 0 | Required checks passed |
| 1 | Functional, data, threshold, or cleanup failure |
| 2 | Input, policy, dependency, or engine error |
| 3 | Missing review, blocked/inconclusive result, cancellation, or interruption |

The base wheel can replay a reviewed package without MCP or a model. See
[testing](testing.md) for reproducible checks and honest acceptance criteria.

## Recover cleanup after interruption

Wait until the original run has ended, then call `horus_cleanup_run` with its
`projectRef`, `runId`, and a stable `requestId`. The CLI offers the same operation:

```sh
horus cleanup --workspace <workspace> --project-ref <projectRef> --run-id <runId> --request-id <cleanupRequestId>
```

The new run uses the original approval, recorded nonsecret values, and original
`run.id`. It executes cleanup and any required authentication steps that are also
allowed by `cleanupAllow`. It does not repeat business preparation or load tests.
Runtime credentials must still be available. Reuse the same request ID after a
lost response, then wait and read the new report.

Secret resource IDs and old sessions cannot be recovered from disk. The report
retains uncertain writes and unresolved cleanup, including actions skipped for
missing inputs. A recovery attempt does not change the original result or expand
its approved scope.
