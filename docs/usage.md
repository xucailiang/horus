# Using horus with a host Agent

horus exposes a local stdio MCP server and a portable Skill. The workflow is not
tied to a particular coding tool. Codex and Claude Code examples are below; other
MCP clients use the same launch command and Skill instructions.

## Install with your coding agent

Paste this into your coding agent:

```text
Follow https://github.com/xucailiang/horus/blob/master/docs/usage.md to install
horus, configure its MCP server and Skill in this coding tool, and verify the connection.
```

For the installing agent:

1. Inspect the current OS and client configuration. Install or reuse Python 3.12,
   uv, and GitHub CLI. Clone this repository to a persistent local directory, or
   reuse an existing checkout; keep test results in a separate persistent workspace.
2. Run the manual installation commands below. The engine installer verifies the
   pinned Hurl/k6 releases. Reuse existing matching binaries when available.
3. Merge a `horus` stdio MCP entry into the current client's configuration. Use
   absolute paths for uv, the checkout, and the workspace. Preserve unrelated
   settings and servers. Use the client's supported configuration format.
4. Install the complete `skills/horus/` directory, including its references, using
   the client's Skill mechanism. If it has no native Skill loader, instruct the
   agent to read `SKILL.md` and follow its references when needed.
5. Reload the MCP connection and call `horus_get_capabilities`. Report success
   only after that call works. If the client requires a restart, give that one
   remaining step. Do not create a test plan or contact a target service during installation.

## Manual installation

Install the current version from this repository:

```sh
git clone https://github.com/xucailiang/horus.git
cd horus
uv sync --python 3.12 --frozen --extra mcp --no-dev
uv run --no-dev python scripts/install_engines.py
uv run --no-dev horus skill
```

Use Python 3.12 and uv 0.9.11. The installer requires GitHub CLI and downloads
Hurl 8.0.1 and k6 2.3.0, verifying official release checksums. macOS and Linux are supported.
Use `HORUS_HURL_BIN` / `HORUS_K6_BIN` for existing matching binaries.

## Connect MCP and install the Skill

These are separate steps. Replace every absolute path below. Keep the project
checkout separate from the persistent test workspace. Inject credential values
into the MCP process environment; tools receive binding names only.

### Codex

`config.toml`:

```toml
[mcp_servers.horus]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/horus", "run", "--no-dev", "--frozen", "--python", "3.12", "--extra", "mcp", "horus", "mcp", "--workspace", "/absolute/path/to/workspace"]
env_vars = ["HORUS_USERNAME", "HORUS_PASSWORD"]
startup_timeout_sec = 30
tool_timeout_sec = 60
```

### Claude Code

`.mcp.json`:

```json
{
  "mcpServers": {
    "horus": {
      "type": "stdio",
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/horus", "run", "--no-dev", "--frozen", "--python", "3.12", "--extra", "mcp", "horus", "mcp", "--workspace", "/absolute/path/to/workspace"]
    }
  }
}
```

### Skill installation

Copy the directory containing the `SKILL.md` reported by `horus skill` to the host's Skill directory:
`~/.agents/skills/horus` for Codex or `~/.claude/skills/horus` for Claude Code.
For an isolated local setup, use the equivalent project Skill directory. Follow
the host's normal tool authorization; horus approval does not bypass it.

Use `--read-root /absolute/path/to/docs` when the server needs to import local
OpenAPI/Markdown outside its workspace. Optional source reading uses the host's
existing tools and permissions. No repository import or source index is needed.

### Other MCP clients

Register the same local stdio server using your client's MCP configuration format:

```json
{
  "command": "/absolute/path/to/uv",
  "args": ["--directory", "/absolute/path/to/horus", "run", "--no-dev", "--frozen", "--python", "3.12", "--extra", "mcp", "horus", "mcp", "--workspace", "/absolute/path/to/workspace"]
}
```

The enclosing configuration keys and Skill locations belong to the client. Use
its current documentation rather than copying another client's paths. Clients
without MCP support need an MCP adapter; the CLI only replays reviewed plans.
No extra language-model API key is needed by horus itself.

## The review-to-report workflow

Give the Agent your API documentation as an online link or a local file, along
with the target environment, credential binding names, and behavior to verify.
For OpenAPI/Swagger links, use `horus_import_api`; for local OpenAPI or Markdown
files, use `horus_register_source` within a configured read root. The Agent reads
the documentation and maps business workflows and call dependencies before
designing tests. Chat material and optional source findings can become
`plan.supportingContext`; cite each as `context:<id>`. Interface evidence uses
`sourceRef#Lx` line references.

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

Before saving, the Agent calls `horus_preview_plan` to validate the draft in memory.
It shows the readable plan **directly in chat**, including the business goal, call
chain, checks, writes, cleanup, request/time/load limits, and gaps. It does not put
the plan in an Ask or multiple-choice tool card. After your actual confirmation,
`horus_confirm_plan` saves the unchanged draft and records the decision. Its
`previewRef` binds the full plan, config, project and environment; a changed draft
needs a fresh preview and confirmation. Neither preview nor confirmation sends
requests to the target. The existing save/prepare/record tools support saved plans
and CLI replay.

After confirmation, the Agent starts once with a stable request ID and waits in
bounded calls. Missing responses do not justify starting a second run. Each
execution is one test version, including replays and cleanup recovery. The returned
`testDirectory` is the run's directory:

```text
projects/<projectRef>/.horus/runs/<runId>/
  plan.md         confirmed readable plan
  plan.json       exact executable plan snapshot
  config.json     configuration; credential binding names only
  review.json     approval and its scope
  report.json     full redacted result
  report.md       short report: verdict, problems, results and business overview
  details.md      all step checks and bounded execution evidence
  junit.xml       machine-readable result
```

The plan is saved after confirmation; the directory and plan snapshots are created
when execution starts. Reports appear there when it finishes. A retry of the same
start request returns the existing version. New runs never overwrite earlier ones.

Set `plan.report.language` to the current coding-agent conversation language,
for example `zh-CN` or `en`. Use that language for plan names, business descriptions,
case/step `title`, and assertion `description`. English and Chinese UI text is built
in; other languages require translations of all `report.labels` keys listed in the
plan schema's `x-labels`. The report never guesses from the machine locale.

Open the returned `reportPath` (`report.md`). It leads with the verdict, cleanup,
failures or unresolved outcomes, then summarizes each business objective and key
performance metrics. Successful actions are merged. A single Mermaid overview
follows the important data; full call order and conditions remain in `plan.md`.

Step/assertion records and bounded request/response evidence live in `details.md`,
with full redacted data in `report.json`. The main report links them once. It never
embeds a second complete report or generates alternate report versions. Scope
limits are briefly listed; the approved plan retains their full context. Unrun
and unselected paths never count as passed; load checks use aggregate evidence.

`artifactRef` provides the short report via `horus_get_artifact`;
`detailsArtifactRef` provides the separate details in the same way. Follow every
page when reading either artifact. Sibling links require the rest of the directory.

After restoring a project, use `horus_get_plan` to read a saved plan, following
`nextOffset` to the end. Reading an old plan alone does not approve a new test.

Only place independent functional chains in separate cases. Consecutive functional
cases share the reviewed `policy.limits.maxFunctionalParallelCases` limit (one or
two, default two). Each case has its own variables and authentication, and holds
its slot until cleanup finishes. Use one when the chains share external state.
Case steps remain ordered. Performance cases wait for the previous group to
finish, run exclusively through cleanup, then allow the next group to start.
Reports show the actual functional peak separately from k6 VUs.

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

## Choose between reviewed paths

A `kind: branch` step declares a `predicate`, `thenSteps`, `elseSteps`, and `outputs`.
For example, capture a job's state, check whether it is already done, then either
verify the result directly or poll for completion. Both arrays belong to the
reviewed plan; the Agent cannot create a new path during execution. Empty arrays
and an empty output list are valid. Each case is limited to 2000 steps and depth 8.

Branch conditions use the same typed comparisons as polling, with literals and
prior variables only. A branch has no current response. Its `identityRef` selects
the authentication variables it can read; shared business data stays visible.
Inside a path, references include the branch and side, for example
`caseId.completion.then.ready.state`. Nested paths continue the same pattern.
`dependsOn` uses these paths without the case prefix.

An output such as `{name: completedTicketId}` must be available after either path.
Authentication outputs also specify their identity, such as
`{name: accessToken, identityRef: owner}`. Other new captures stay conditional.
To use them later, declare `requires` on an HTTP step (or a poll's `requestStep`),
or check them with an `exists` branch condition. An existence condition proves
availability on the appropriate path; `not`, `all` when true, and `any` when false
can preserve that proof. Conditions still evaluate all their inputs.

Missing required inputs skip the guarded action. Cleanup records missing inputs
as unresolved, so a conditional cleanup skip cannot silently turn into a pass.
Requests and declared durations use the larger branch budget. Reports retain the
selected path and conditions; other paths are `skipped` with reason `not_selected`,
including their JUnit entries. A routing decision is not an additional passing HTTP
check. After interruption, cleanup uses the recorded path and approved actions,
without reevaluating the condition or authenticating through an unselected path.

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
[CONTRIBUTING.md](../CONTRIBUTING.md#verification) for local and client verification.

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
