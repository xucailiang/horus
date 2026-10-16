# Roadmap

## Purpose and positioning

horus aims to make professional API testing accessible to anyone who needs to
verify a service. Tests follow real user scenarios. Users retain control over
test goals, acceptance criteria, and execution scope, with workflows they can
automate, audit, and adapt.

Users should be able to start by describing a goal in plain language, then inspect
and refine scenarios, test data, assertions, load profiles, and execution details
as needed. The workflow should accommodate both people new to testing and
experienced practitioners working on complex scenarios.

The stages below describe planned outcomes, with no committed release dates. For
available features and limitations, see the [architecture](architecture.md). The
[usage guide](usage.md) covers installation and operation.

## The unit of value: a user scenario

A test scenario describes a user's goal, the conditions under which they use the
service, and the expected outcome. API requests reproduce the steps in that
scenario; request-level metrics help explain the user's experience.

For example, a user submits a job, waits for processing, and retrieves a result.
The test should check the result and access rules, measure the time to complete
the task, and examine failure behavior and concurrent use. Request latency alone
does not describe the complete task.

Functional correctness, performance, and reliability should all be evaluated in
the context of the user scenario. Performance testing is an important part of
verifying a service as people use it.

A confirmed test scenario preserves the user's intent, expected behavior,
execution conditions, checks, and supporting evidence. Reusing and maintaining
these scenarios should improve test quality and reduce manual work on later runs.

## Product principles

Users decide what to test, the expected behavior, execution limits, and acceptable
trade-offs. The host agent helps turn those decisions into a test plan. horus
executes the plan and evaluates its checks. The user must be able to review
missing requirements, conflicts, assumptions, and inconclusive results.

Users can describe their intent naturally. Before execution, the agent must
translate that intent into a reviewable plan with explicit conditions and expected
results. Simple scenarios should require little setup, with more detailed control
available for complex scenarios. Changes to the plan must remain visible and
require approval under the same rules as the original plan.

| Principle | What it requires |
| --- | --- |
| Automatable | A defined test goal can become a complete, repeatable workflow with explicit limits, including preparation, checks, cleanup, and reporting. |
| Auditable | Users can review the plan, inspect what actually ran, and trace conclusions to expectations and execution evidence. |
| Flexible | Users can refine and reuse scenarios, adjust execution details, and choose how to invoke the workflow. |
| Professional | Test design, workload modeling, execution, and reporting follow established testing practices and clearly state their limitations. |

Automate request sequencing and data preparation while keeping the test scope,
acceptance criteria, and coverage gaps clear to the user.

## Current foundation

The alpha supports API imports, test planning with the host agent, plan review in
the conversation, functional tests with execution limits, basic load tests, data
cleanup, and saved execution records. It also supports identity profiles,
conditional paths, asynchronous polling, separate local projects, and replay of
approved plans through the CLI.

Current load steps repeat a single API operation with a fixed number of virtual
users and iterations. Concurrent execution of complete user journeys, scenarios
spanning multiple service targets, automated change impact analysis, and built-in
baseline comparisons are planned. Users can inspect saved results manually, but
must still establish whether the test conditions allow a meaningful comparison.

## Outcome roadmap

Each stage builds on the previous one. Ease of use, user control, traceability,
and professional testing practices apply throughout. Existing features may
support several stages; progress depends on demonstrated results in use.

### 1. Express intent and complete a trustworthy scenario

Users can describe a real user goal, review the proposed test design, and delegate
a complete test run without manually configuring each request.

Priorities:

- Design tests around user goals, expected behavior, and relevant risks, using
  API documentation, requirements, and optional source context as evidence.
- Make missing information and assumptions clear before execution, and let the
  user inspect and refine the proposed scenario at the depth they need.
- Complete data preparation, business operations, checks, cleanup, and reporting
  within the agreed scope.
- Report whether users can complete their tasks as expected, with detailed
  request and response records available for investigation.

Assess progress by having people with different levels of testing experience run
representative scenarios on real services and explain the results and coverage
limits. Track preparation effort and manual steps during execution. Use known
faults to check that the tests detect incorrect behavior and the reports identify
incomplete verification.

### 2. Expand the complexity one person can handle

Users can delegate more complex scenarios while retaining control over test
objectives and execution conditions.

Priorities:

- Extend scenario design to cover interactions between identities, data
  dependencies, branches, asynchronous behavior, and failure paths as needed.
- Support concurrent user journeys and measure task-level outcomes, using load
  profiles that answer a specific testing question.
- Support scenarios across multiple service targets, with explicit identities,
  dependencies, execution limits, and cleanup responsibilities.
- Let users inspect and adjust execution details while retaining the rest of
  the scenario and its business purpose.

Assess progress through scenarios that previously required substantial manual
coordination or custom scripts. As scenarios become more complex, preparation and
manual intervention should grow more slowly. Reports should trace task success,
completion time, and failure behavior to execution evidence.

### 3. Accumulate verification capability as services evolve

Users can reuse confirmed scenarios and evidence across test runs, service
updates, and multiple projects, reducing the effort required for subsequent tests.

Priorities:

- Make confirmed scenarios easy to find, reuse, modify, and combine while
  preserving their intent, sources, and scope.
- Identify scenarios affected by changes to API contracts or requirements, and
  explain proposed updates for the user to review.
- Record service versions, environments, test data, scenario versions, and load
  profiles in enough detail to determine whether results are comparable.
- Compare results with suitable baselines, explaining changes in test conditions
  and uncertainty in the comparison.
- Keep scenarios and execution evidence accessible to the host agent and the
  user's external automation tools.

Assess progress through repeat use after service changes: users should be able
to reuse earlier work and spend less time reconstructing context and maintaining
tests. They should understand changes to scenarios and results, and which
conclusions the evidence supports.

## Investment and scope

Prioritize realistic scenarios, more complete test automation, and better control
and reuse of test assets. Build on the existing general-purpose design, adding
protocols, engines, and integrations when real scenarios require them.

Keep the following boundaries:

- Focus on what individuals can accomplish. Enterprise administration,
  organizational permissions, and approval-management systems are outside the
  current scope.
- Let users choose orchestration through conversation or external pipelines.
  horus will not provide its own offline task platform, scheduler, persistent job
  queue, or always-on task service in this roadmap. Local run records and recovery
  support reliable execution; callers manage longer-running workflows.
- Keep model reasoning with the host agent and use established execution tools.
  A dedicated chat application, hosted model service, and custom load engine are
  outside the current scope.
- Incorporate external resource measurements when useful. Building a full
  monitoring platform or promising automatic root-cause diagnosis is outside
  the current scope.
- Reuse confirmed business context and scenarios. Keep review focused on decisions
  and changes while preserving the exact execution scope and supporting evidence.

## Open-source adoption and roadmap decisions

User adoption and maintainability guide the project. Revenue is not a project
objective. Every stage should include clear documentation, reliable installation,
representative examples, and feedback from people using horus on real services.

Measure whether users complete their first scenario, return to run tests again,
spend less time on manual work, and handle more complex tests. Also assess whether
the evidence helps them make informed decisions. Test counts and the number of
features alone are insufficient measures.

Continue to test whether people can describe useful test goals without specialist
terminology, whether scenario reuse saves more effort than maintenance requires,
and whether greater flexibility preserves reviewability and reliable execution.

For each milestone, define the expected user benefit, the assumption to validate,
the smallest useful change, and the evidence needed to decide what follows.
Revisit priorities when users encounter different constraints. Each addition
should help users describe tests, execute them, or interpret the results.
