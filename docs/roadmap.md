# Roadmap

horus is an AI-native API testing workspace for anyone who needs to verify a
service. Its direction is to preserve user judgment while making professional
testing easier to automate, review, and adapt. Tests should follow what users
need to accomplish with a service.

This roadmap proposes concrete additions to support that direction. The delivery
sequence is provisional, with no committed release dates. Available capabilities
and implementation limits are documented in the [architecture](architecture.md).

## Strategic directions and planned capabilities

| Strategic direction | What needs to improve | Planned capabilities |
| --- | --- | --- |
| Test from the user's perspective | Functional workflows are supported, but load tests repeat one API operation at a time. A plan targets one service address per environment. | Concurrent user journeys, task-level performance metrics, and scenarios spanning multiple services. |
| Make testing accessible while retaining expert control | Planning relies on the host agent interpreting the schema and assembling a plan. Users need useful starting points and a clear way to refine details. | Guided scenario design, reference scenarios, focused validation feedback, and progressive access to execution details. |
| Automate work within a reviewable scope | Approved plans can be replayed, but revisions lack a computed diff and some review and execution controls remain incomplete. | Plan revision summaries, complete validation of business references, supported approval revocation, and consistent automation interfaces. |
| Support flexible, professional test design | Load execution has one workload model and small fixed ceilings. Repeated input variations require separate case definitions. | Additional workload models, configurable tested limits, data-driven scenarios, and a documented assertion support matrix. |
| Keep tests useful as services change | Plans and results are saved, but there is no scenario catalog, change impact analysis, or built-in baseline comparison. | Reusable scenario definitions, parameter sets, version and environment context, comparable-run reports, and assisted updates after API changes. |
| Grow through open-source use | Installation and examples must work for people outside the development environment. | Verified setup instructions, examples at several levels of complexity, real-client acceptance checks, and contribution guidance tied to the roadmap. |

## Current baseline and limits

The alpha already includes API imports, planning through the host agent, review
in the conversation, functional workflows, identity profiles, conditional paths,
asynchronous polling, basic load tests, cleanup, and Markdown/JSON/JUnit reports.
Separate projects and CLI replay of approved plans are also implemented. These
capabilities are the starting point for this roadmap.

Current constraints relevant to the planned work:

- Each environment has one base URL. Separate projects can target different
  services, but one scenario cannot explicitly route steps across service targets.
- A load step repeats one operation with fixed virtual users and a shared
  iteration count. It does not execute a complete user journey per iteration.
- Configured ceilings are 10 virtual users, 1,000 load iterations, 60 seconds per
  load step, 1,500 requests and 120 seconds per run, and 30 seconds for cleanup.
  Functional cases run with one or two concurrent consumers.
- Revised plans retain a parent reference and change reason, but review does not
  compute a parent/child diff.
- Business reference validation is incomplete. The core checks revocation, but
  there is no supported revocation command or MCP operation. The configured
  report-generation timeout is not enforced.
- Pre-review probing has no execution entry point; its configuration field is
  unused. This roadmap does not depend on adding exploratory requests before
  approval.

See [current limits](architecture.md#current-limits) for the maintained description
of these constraints.

## 1. User scenarios: complete workflows under load

The next step beyond single-operation load tests is to run the sequence of actions
that a user actually performs, including asynchronous completion and interactions
between services.

Planned additions:

- Execute a reviewed sequence of requests, captures, assertions, branches, and
  bounded polling as one load-test iteration. Preserve the semantics of the
  corresponding functional scenario.
- Isolate variables, authentication state, and created resources for each simulated
  user or iteration. Declare which preparation is shared and which runs for each
  user or iteration.
- Report attempted, completed, successful, and failed journeys; completed journeys
  per second; and journey-duration percentiles. Keep request-level measurements
  available for investigation.
- Define the measured journey boundaries in the plan. Include declared waits and
  asynchronous completion in task duration; report preparation and cleanup
  separately when they are outside those boundaries.
- Add named service targets with their own API definitions and credential
  bindings. Route each scenario step to an explicit target and capture data
  dependencies across services.

Constraints: all targets, identities, measured steps, load limits, and cleanup
actions must appear in the reviewed plan. Concurrency must preserve case data
isolation. Unsupported combinations must fail validation before approval.
Cross-service execution does not imply distributed load generation or automatic
service discovery.

Acceptance: one reviewed scenario can create a resource, submit work, wait for
completion, and verify the result under concurrent users. A second fixture must
exercise a workflow across two service targets. Reports must distinguish failed,
unfinished, and successful journeys and account for their cleanup.

## 2. Accessible planning with progressively deeper control

Users should be able to begin with a business goal and refine the resulting test
without learning the complete plan schema first.

Planned additions:

- Add guided planning steps for actors, user goals, expected behavior, test data,
  load expectations, and missing inputs. Ask for information that affects the
  proposed test, and reuse confirmed context.
- Provide reference scenarios for common patterns: a resource lifecycle,
  access-control checks, an asynchronous task, and a concurrent user journey.
  Keep simple examples usable without advanced configuration.
- Present validation errors with the affected scenario or step, why the plan is
  invalid, and what the user or host agent needs to resolve.
- Let users request a focused change to a scenario, assertion, dataset, or load
  profile while preserving unaffected parts of the plan.

Constraints: these improvements use the existing host agent, Skill, and MCP
workflow. A new chat application or horus-hosted model is outside scope.
Defaults and inferred expectations must be visible in the plan; missing business
requirements must remain explicit.

Acceptance: a new user can complete a reference scenario from a plain-language
goal. An experienced user can change a specific test condition without rebuilding
the plan. Both can inspect the resulting scope and acceptance criteria.

## 3. Automation and auditability: repeatable execution, reviewable changes

Automation should reduce repeated work while retaining the user's decisions about
what may run.

Planned additions:

- Compute a revision summary showing changes to business expectations, requests,
  identities, service targets, load, cleanup, and coverage. Display it alongside
  the full revised plan.
- Complete validation of business IDs, state/resource references, and dependency
  producers and consumers.
- Expose approval revocation through supported interfaces and enforce the report
  timeout, so advertised controls have an operational effect.
- Extend MCP and CLI access to new scenario-selection, review, comparison, and
  reporting capabilities as they are added. Preserve machine-readable results
  and exit codes for external automation.
- Document an external pipeline example that replays an approved scope and
  publishes its artifacts without requiring a model for each run.

Constraints: changing the plan, target, configuration, or execution scope requires
a new review under the existing approval rules. Approval records remain local
records, not independent authentication of a reviewer. Local execution state and
recovery remain necessary; a managed offline task system, scheduler, persistent
task queue, or always-on orchestration service is outside scope.

Acceptance: a user can inspect a concrete revision, approve that exact version,
replay it through the CLI, and trace results to the approved plan. A revoked
approval blocks new execution. Regression tests verify business reference
validation, approval revocation, and enforcement of report-generation deadlines.

## 4. Flexibility and professional execution: workloads and test data

Additional execution options should answer specific testing questions and retain
the same review and reporting guarantees.

Planned additions:

- Extend fixed-iteration execution to complete scenarios, then add fixed-duration
  concurrency and staged increases in concurrency. Each profile must declare its
  limits and completion conditions.
- Replace the current small fixed ceilings with configurable limits backed by
  engine and cancellation tests. Publish supported bounds; increasing a number
  alone does not establish that the load generator can sustain it.
- Add named datasets and parameterized scenarios so boundary values, invalid
  inputs, and identity combinations can reuse one scenario definition. Record
  the dataset and parameter values used by each run, with secret redaction.
- Document which assertions are supported by functional and load execution.
  Extend support where real scenarios require it, with equivalent semantics
  and explicit rejection of unsupported checks.

Constraints: scenario logic, input data, load configuration, and acceptance
criteria must remain distinguishable in review. Do not silently omit checks,
change expected results, or adjust reviewed load parameters. Fixed arrival-rate,
spike, soak, distributed-load, and arbitrary-script extensions are candidates for
later evaluation, not commitments in this sequence.

Acceptance: the same scenario can run against multiple datasets and supported
load profiles. Reports state the actual workload, completed work, data failures,
and incomplete execution. Cancellation and cleanup remain bounded as load grows.

## 5. Reuse and service evolution: maintain scenarios and compare results

Saved runs should become a practical regression workflow across multiple projects
and service versions.

Planned additions:

- Add a scenario catalog with names, descriptions, revisions, source references,
  and project associations. Let users find, select, and reuse confirmed scenarios.
- Separate reusable scenario logic from environment-specific values and approved
  parameter sets. Resolve inputs before review and preserve the existing approval
  requirements when a binding changes the execution scope.
- Record caller-supplied service version identifiers, environment details,
  scenario and dataset versions, and load profiles with each run.
- Let users select a baseline run and produce a comparison of correctness,
  completed work, task duration, request metrics, and available resource
  observations. Identify changed conditions and missing context before presenting
  a performance conclusion.
- Compare imported API definitions, identify affected scenario steps, and let the
  host agent propose a revised plan. Include the reasons and coverage changes in
  review.

Constraints: a changed schema does not define the intended business behavior by
itself. Updates must preserve user-confirmed expectations until the user changes
them. Comparisons must state uncertainty when environments, data, or workloads
differ. Source inspection stays with the host agent; horus does not need its own
repository crawler or source index.

Acceptance: after a service update, a user can find the previous scenario, review
an affected-step summary, approve a revision, rerun it, and compare results with a
chosen baseline. Incompatible or poorly documented runs cannot yield an
unqualified regression verdict.

## Delivery sequence

| Priority | Deliverables | Dependency or release condition |
| --- | --- | --- |
| Next | Guided reference scenarios, actionable validation errors, plan revision summaries, and the incomplete review/execution controls listed above. | Establish a reliable plan and review contract before expanding execution. |
| Then | Complete user journeys under load, journey metrics, and explicit multi-service targets. | Reuse the reviewed scenario contract. Functional multi-target routing can ship independently; combined load support requires acceptance of both capabilities. |
| Following | Parameterized datasets, additional concurrency profiles, and tested configurable limits. | Functional parameterization can ship independently; larger workloads require isolated journey execution and bounded cleanup. |
| Later | Scenario catalog, reusable environment bindings, baseline comparison, and API-change assistance. | Scenario identity and recorded run context must exist before reliable historical comparison. |
| Every stage | Setup checks, examples, real-client acceptance, and updated capability documentation. | Shipped features must be usable outside the development checkout and distinguishable from planned work. |

## Project-wide boundaries

- Keep the product centered on what an individual can accomplish. Enterprise
  administration, organizational permissions, and approval-management systems
  remain outside this roadmap.
- Keep model reasoning in the host agent and execution on established tools.
  Additional protocols and integrations require demonstrated scenario needs.
- Use resource observations as supporting evidence. A full monitoring platform
  and guaranteed automatic root-cause diagnosis are outside scope.
- Keep plans, reports, and detailed evidence usable in conversation and through
  external automation. Users choose orchestration.
- Prioritize open-source adoption and maintainability. Revenue is not a project
  objective.

Implementation proposals should identify the strategic direction they serve, the
capability being added, its review and execution constraints, and the acceptance
scenario. A feature is complete when that scenario works and its limitations are
documented.
