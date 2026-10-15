import asyncio
import json
import os
import time
from itertools import groupby
from pathlib import Path
from typing import Any

from horus.adapters.hurl import MISSING, HurlAdapter, Response, pointer
from horus.adapters.k6 import K6Adapter
from horus.adapters.process import check_engine
from horus.core.models import (
    BranchStep,
    Case,
    HttpStep,
    LoadStep,
    Plan,
    PollStep,
    ProjectConfig,
    Request,
    Step,
)
from horus.core.predicates import evaluate
from horus.core.reports import recorded_events, verdict, write_report
from horus.core.service import Service
from horus.core.steps import request_action, selected_steps, walk_steps
from horus.core.store import HorusError, atomic_write
from horus.core.values import VARIABLE, CaseContext, Redactor, resolve


def assertion_status(checks: list[dict[str, Any]]) -> str:
    for status in ("failed", "inconclusive"):
        if any(check["status"] == status for check in checks):
            return status
    return "passed"


class Runner:
    def __init__(self, service: Service, project_ref: str, run_id: str, root: Path) -> None:
        self.service, self.project_ref, self.run_id, self.root = service, project_ref, run_id, root
        self.store = service.project(project_ref)
        self.intent: dict[str, Any] = json.loads((root / "intent.json").read_bytes())
        self.plan = Plan.model_validate(self.store.get("plans", self.intent["planRef"]))
        self.config = ProjectConfig.model_validate(
            self.store.get("configs", self.intent["configRef"])
        )
        env = self.config.environments[self.intent["environment"]]
        self.base_url = env.baseUrl or ""
        self.secrets = {key: os.environ[name] for key, name in env.secretBindings.items()}
        self.redactor = Redactor(list(self.secrets.values()))
        self.operations = service.catalog(self.store, self.plan)
        self.ledger: list[dict[str, Any]] = []
        self.cancelled = False
        self.phase = "queued"
        self.active_load: set[asyncio.Task[Any]] = set()
        self.active_poll: set[asyncio.Task[Any]] = set()
        self.case_phases: dict[str, str] = {}
        self.active_functional_cases = 0
        self.peak_functional_cases = 0
        self.deadline = float("inf")

    def context(
        self, run_id: str | None = None, *, shared: dict[str, Any] | None = None
    ) -> CaseContext:
        return CaseContext(
            run_id=run_id or self.run_id,
            secrets=self.secrets,
            profiles={profile.id: profile for profile in self.plan.identityProfiles},
            shared=shared if shared is not None else {},
        )

    def state(self, status: str) -> None:
        self.phase = status
        atomic_write(self.root / "state.json", {**self.intent, "status": status})

    def request_cancel(self) -> None:
        self.cancelled = True
        for task in self.active_load | self.active_poll:
            task.cancel()

    def refresh_phase(self) -> None:
        self.state("running" if "running" in self.case_phases.values() or not self.case_phases
                   else "cleaning")

    def event(self, event: dict[str, Any]) -> dict[str, Any]:
        # Redact runtime payloads, preserving trusted control fields used for verdicts and refs.
        safe = dict(event)
        if "request" in safe:
            safe["request"] = self.redactor.clean(safe["request"])
        if "response" in safe:
            safe["response"] = {
                **safe["response"],
                "headers": self.redactor.clean(safe["response"]["headers"]),
                "body": self.redactor.clean(safe["response"]["body"]),
            }
        if "assertions" in safe:
            safe["assertions"] = [
                {
                    **check,
                    "actual": self.redactor.clean(check["actual"]),
                    "expected": self.redactor.clean(check["expected"]),
                }
                for check in safe["assertions"]
            ]
        if "failureSamples" in safe:
            safe["failureSamples"] = [
                {**sample, "body": self.redactor.clean(sample["body"])}
                for sample in safe["failureSamples"]
            ]
        if "conditions" in safe:
            safe["conditions"] = [
                {**condition, "left": self.redactor.clean(condition["left"]),
                 "right": self.redactor.clean(condition["right"])}
                for condition in safe["conditions"]
            ]
        with (self.root / "events.jsonl").open("a") as stream:
            os.chmod(self.root / "events.jsonl", 0o600)
            stream.write(json.dumps(safe) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return safe

    def safe_ledger(self) -> list[dict[str, Any]]:
        return [
            {**entry, "variables": self.redactor.clean(entry["variables"])} for entry in self.ledger
        ]

    def persist_ledger(self) -> None:
        atomic_write(self.root / "ledger.json", self.safe_ledger())

    def capture_response(
        self,
        step: HttpStep,
        response: Response,
        context: CaseContext,
        entry: dict[str, Any],
        *,
        commit: bool = True,
    ) -> None:
        for name, capture in step.captures.items():
            if capture.purpose == "resource-cleanup" and not 200 <= response.status < 300:
                continue
            if capture.jsonPointer is not None:
                value = pointer(response.body, capture.jsonPointer)
            elif capture.header is not None:
                value = response.headers.get(capture.header.lower(), MISSING)
            else:
                value = response.status
            if value is MISSING:
                if commit:
                    raise HorusError("capture_missing", name)
                continue
            if capture.secret or capture.private:
                self.redactor.register(value)
            if commit:
                context.capture(step.identityRef, name, capture, value)
                if not (capture.secret or capture.private):
                    entry["variables"][name] = value

    async def step(
        self,
        step: Step,
        context: CaseContext,
        *,
        case_id: str,
        cleanup: bool = False,
    ) -> dict[str, Any]:
        if not cleanup and (self.cancelled or time.monotonic() >= self.deadline):
            return self.skipped_step(step, case_id, step.id, status="blocked",
                                     reason="cancelled" if self.cancelled else "run_timeout")
        if isinstance(step, PollStep):
            return await self.poll(step, context, case_id=case_id)
        if isinstance(step, BranchStep):
            return await self.branch(step, context, case_id=case_id)
        record: dict[str, Any] = {"caseId": case_id, "id": step.id, "status": "error"}
        entry: dict[str, Any] = {
            "caseId": case_id,
            "stepId": step.id,
            "operationId": step.operationId,
            "cleanup": cleanup,
            "state": "intent",
            "variables": {},
        }
        if step.identityRef is not None:
            record["identityRef"] = entry["identityRef"] = step.identityRef
        try:
            values = context.view(step.identityRef)
            if isinstance(step, HttpStep) and not set(step.requires).issubset(values["vars"]):
                return self.event(
                    {**record, "status": "skipped", "reason": "missing_cleanup_inputs"
                     if cleanup else "condition_not_met"}
                )
            request = Request.model_validate(resolve(step.request.model_dump(mode="json"), values))
            operation = self.operations[step.operationId]
            self.ledger.append(entry)
            self.persist_ledger()
            if isinstance(step, LoadStep):
                task = asyncio.current_task()
                assert task is not None
                self.active_load.add(task)
                try:
                    result = await K6Adapter().run(
                        operation,
                        self.base_url,
                        LoadStep.model_validate(
                            {
                                **step.model_dump(mode="json"),
                                "request": request.model_dump(mode="json"),
                                "assertions": resolve(
                                    [a.model_dump(mode="json") for a in step.assertions], values
                                ),
                            }
                        ),
                    )
                finally:
                    self.active_load.discard(task)
                record.update(result)
                entry["state"] = "confirmed"
            else:
                response = await HurlAdapter().request(operation, self.base_url, request)
                self.capture_response(step, response, context, entry)
                values = context.view(step.identityRef)
                entry["state"] = "confirmed" if 200 <= response.status < 300 else "unknown"
                self.persist_ledger()
                assertions = [
                    a.model_validate(resolve(a.model_dump(mode="json"), values))
                    for a in step.assertions
                ]
                checks = await response.check(assertions)
                record.update(
                    {
                        "status": assertion_status(checks),
                        "request": request.model_dump(mode="json"),
                        "response": {
                            "status": response.status,
                            "headers": response.headers,
                            "body": response.body,
                            "elapsedMs": response.elapsed_ms,
                        },
                        "assertions": checks,
                    }
                )
            if cleanup and record["status"] == "passed":
                entry["state"] = "cleaned"
        except asyncio.CancelledError:
            entry["state"] = "unknown"
            self.persist_ledger()
            self.event(
                {**record, "status": "inconclusive", "reason": "cancelled_write_outcome_unknown"}
            )
            raise
        except (HorusError, ValueError, OSError, TimeoutError) as exc:
            entry["state"] = "unknown"
            record.update(
                {
                    "status": "blocked"
                    if isinstance(exc, HorusError) and exc.code == "missing_variable"
                    else "error",
                    "reason": (exc.code if isinstance(exc, HorusError) else type(exc).__name__),
                }
            )
        self.persist_ledger()
        return self.event(record)

    async def poll(
        self, step: PollStep, context: CaseContext, *, case_id: str
    ) -> dict[str, Any]:
        http = step.requestStep
        record: dict[str, Any] = {
            "caseId": case_id, "id": step.id, "kind": "poll", "status": "error",
            "attempts": 0, "observations": [],
        }
        if http.identityRef is not None:
            record["identityRef"] = http.identityRef
        started = time.monotonic()
        task = asyncio.current_task()
        assert task is not None
        self.active_poll.add(task)
        try:
            values = context.view(http.identityRef)
            if not set(http.requires).issubset(values["vars"]):
                return self.event({**record, "status": "skipped", "reason": "condition_not_met"})
            request = Request.model_validate(resolve(http.request.model_dump(mode="json"), values))
            record["request"] = request.model_dump(mode="json")
            async with asyncio.timeout(step.timeoutMs / 1000):
                for attempt in range(1, step.maxAttempts + 1):
                    if self.cancelled:
                        record.update(status="inconclusive", reason="poll_cancelled")
                        break
                    entry: dict[str, Any] = {
                        "caseId": case_id, "stepId": step.id, "operationId": http.operationId,
                        "cleanup": False, "state": "intent", "variables": {}, "attempt": attempt,
                    }
                    if http.identityRef is not None:
                        entry["identityRef"] = http.identityRef
                    self.ledger.append(entry)
                    self.persist_ledger()
                    record["attempts"] = attempt
                    response = await HurlAdapter().request(
                        self.operations[http.operationId], self.base_url, request
                    )
                    entry["state"] = "confirmed"
                    self.persist_ledger()
                    # Protect sensitive observations without publishing provisional captures.
                    self.capture_response(http, response, context, entry, commit=False)
                    record["response"] = {
                        "status": response.status, "headers": response.headers,
                        "body": response.body, "elapsedMs": response.elapsed_ms,
                    }
                    guards = await response.check(
                        [a for a in http.assertions if a.status is not None]
                    )
                    if assertion_status(guards) != "passed":
                        record.update(status=assertion_status(guards),
                                      reason="poll_response_rejected", assertions=guards)
                        break
                    conditions: list[dict[str, Any]] = []
                    matched = evaluate(step.until, values["vars"], response, conditions)
                    record["observations"].append(self.event({
                        "caseId": case_id, "id": step.id, "kind": "poll-attempt",
                        "identityRef": http.identityRef, "attempt": attempt, "status": "observed",
                        "statusCode": response.status, "elapsedMs": response.elapsed_ms,
                        "matched": matched, "conditions": conditions,
                    }))
                    if matched:
                        self.capture_response(http, response, context, entry)
                        self.persist_ledger()
                        values = context.view(http.identityRef)
                        assertions = [a.model_validate(resolve(a.model_dump(mode="json"), values))
                                      for a in http.assertions]
                        checks = await response.check(assertions)
                        record.update(status=assertion_status(checks), assertions=checks)
                        break
                    if attempt == step.maxAttempts:
                        record.update(status="failed", reason="poll_attempts_exhausted")
                        break
                    await asyncio.sleep(step.intervalMs / 1000)
        except TimeoutError:
            record.update(status="failed", reason="poll_timeout")
        except asyncio.CancelledError:
            self.event({**record, "status": "inconclusive", "reason": "poll_cancelled"})
            raise
        except (HorusError, ValueError, OSError) as exc:
            record.update(status="error", reason=exc.code if isinstance(exc, HorusError)
                          else type(exc).__name__)
        finally:
            self.active_poll.discard(task)
        record["pollElapsedMs"] = (time.monotonic() - started) * 1000
        return self.event(record)

    def skipped_step(
        self, step: Step, case_id: str, path: str, *, reason: str = "not_selected",
        status: str = "skipped",
    ) -> dict[str, Any]:
        record: dict[str, Any] = {"caseId": case_id, "id": path, "kind": step.kind,
                                  "status": status, "reason": reason}
        identity = (
            step.identityRef if isinstance(step, BranchStep) else request_action(step).identityRef
        )
        if identity is not None:
            record["identityRef"] = identity
        if isinstance(step, BranchStep):
            record["steps"] = [self.skipped_step(child, case_id, path + "." + side + "." + child.id,
                                                 reason=reason, status=status)
                               for side, children in (
                                   ("then", step.thenSteps), ("else", step.elseSteps))
                               for child in children]
        return self.event(record)

    async def branch(
        self, step: BranchStep, context: CaseContext, *, case_id: str,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {"caseId": case_id, "id": step.id, "kind": "branch",
                                  "status": "error", "conditions": [], "steps": []}
        if step.identityRef is not None:
            record["identityRef"] = step.identityRef
        try:
            matched = evaluate(step.predicate, context.view(step.identityRef)["vars"],
                               observations=record["conditions"])
            selected = "then" if matched else "else"
            record["selected"] = selected
            self.event({**record, "kind": "branch-selection", "status": "observed"})
            children = step.thenSteps if matched else step.elseSteps
            other = step.elseSteps if matched else step.thenSteps
            skipped = [self.skipped_step(child, case_id, step.id + "." +
                                        ("else" if matched else "then") + "." + child.id)
                       for child in other]
            blocked = False
            active_path = step.id
            remaining = iter(children)
            for child in remaining:
                active_path = step.id + "." + selected + "." + child.id
                if blocked or self.cancelled or time.monotonic() >= self.deadline:
                    result = self.skipped_step(child, case_id, active_path,
                                               reason="upstream_blocked", status="blocked")
                else:
                    result = await self.step(child.model_copy(update={"id": active_path}),
                                             context, case_id=case_id)
                record["steps"].append(result)
                blocked = result["status"] in {"failed", "error", "blocked", "inconclusive"}
            if not record["steps"] or all(s["status"] == "skipped" for s in record["steps"]):
                record.update(status="skipped", reason="no_selected_checks")
            else:
                record["status"] = verdict([{"steps": record["steps"], "cleanup": []}])
            record["steps"].extend(skipped)
        except asyncio.CancelledError:
            record["steps"].append(self.event({"caseId": case_id, "id": active_path,
                                                "status": "inconclusive", "reason": "interrupted"}))
            record.update(status="inconclusive",
                          reason="cancelled" if self.cancelled else "timeout")
            record["steps"].extend(self.skipped_step(
                child, case_id, step.id + "." + selected + "." + child.id,
                reason="upstream_blocked", status="blocked")
                for child in remaining)
            record["steps"].extend(skipped)
        except (HorusError, ValueError) as exc:
            record.update(status="error", reason=exc.code if isinstance(exc, HorusError)
                          else type(exc).__name__)
        return self.event(record)

    async def execute(self) -> dict[str, Any]:
        self.service.review_gate(
            self.project_ref,
            self.intent["planRef"],
            self.intent["reviewRef"],
            self.intent["environment"],
        )
        versions = {"hurl": await check_engine("hurl")}
        if not self.intent.get("cleanupFrom") and any(
            isinstance(step, LoadStep)
            for case in self.plan.cases for _, step in walk_steps(case.steps)
        ):
            versions["k6"] = await check_engine("k6")
        result: dict[str, Any] = {
            **self.intent,
            "engineVersions": versions,
            "status": "completed",
            "cases": [],
        }
        deadline = time.monotonic() + self.config.policy.limits.maxRunDurationMs / 1000
        self.deadline = deadline
        if self.intent.get("cleanupFrom"):
            return await self.repair(result, deadline)
        result["cases"] = [{"id": case.id, "steps": [], "cleanup": []} for case in self.plan.cases]
        self.state("running")
        for kind, group in groupby(enumerate(self.plan.cases), key=lambda item: item[1].kind):
            cases = [(case, result["cases"][index]) for index, case in group]
            limit = (
                self.config.policy.limits.maxFunctionalParallelCases if kind == "functional" else 1
            )
            await self.run_cases(cases, limit)
        return self.finish(result)

    async def run_cases(self, cases: list[tuple[Case, dict[str, Any]]], limit: int) -> None:
        pending = iter(cases)

        async def consume() -> None:
            for case, output in pending:
                try:
                    await self.run_case(case, output)
                except BaseException:
                    self.request_cancel()
                    raise

        # Shield sibling writes and cleanup from cancellation of the scheduling task.
        group = asyncio.gather(*(consume() for _ in range(min(limit, len(cases)))),
                               return_exceptions=True)
        try:
            results = await asyncio.shield(group)
        except asyncio.CancelledError:
            self.request_cancel()
            results = await group
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def run_case(self, case: Case, output: dict[str, Any]) -> None:
        if self.cancelled or time.monotonic() >= self.deadline:
            output["steps"] = [self.skipped_step(step, case.id, step.id, status="blocked",
                                                reason="case_not_started") for step in case.steps]
            output["cleanup"] = [self.skipped_step(step, case.id, step.id,
                                                  reason="case_not_started")
                                 for step in case.cleanup]
            return
        output["startedAt"] = time.time()
        self.case_phases[case.id] = "running"
        self.refresh_phase()
        if case.kind == "functional":
            self.active_functional_cases += 1
            self.peak_functional_cases = max(
                self.peak_functional_cases, self.active_functional_cases
            )
        context = self.context()
        blocked = False
        try:
            for step in case.steps:
                if blocked or self.cancelled or time.monotonic() >= self.deadline:
                    output["steps"].append(self.skipped_step(
                        step, case.id, step.id, status="blocked", reason="upstream_blocked"))
                    continue
                result = await asyncio.wait_for(self.step(step, context, case_id=case.id),
                                                max(0.001, self.deadline - time.monotonic()))
                output["steps"].append(result)
                blocked = result["status"] in {"failed", "error", "blocked"}
        except asyncio.CancelledError:
            self.cancelled = True
            output["steps"].append(self.event({"caseId": case.id, "id": "cancelled",
                                                "status": "inconclusive"}))
        except TimeoutError:
            output["steps"].append(self.event({"caseId": case.id, "id": "timeout",
                                                "status": "inconclusive"}))
        finally:
            try:
                output["cleanup"] = await self.cleanup_steps(case.id, case.cleanup, context)
            finally:
                self.case_phases.pop(case.id, None)
                if case.kind == "functional":
                    self.active_functional_cases -= 1
                output["finishedAt"] = time.time()
                self.refresh_phase()

    async def cleanup_steps(
        self,
        case_id: str,
        steps: list[HttpStep],
        context: CaseContext,
    ) -> list[dict[str, Any]]:
        if case_id in self.case_phases:
            self.case_phases[case_id] = "cleaning"
            self.refresh_phase()
        else:
            self.state("cleaning")
        budget = self.config.policy.limits.maxCleanupDurationMs / 1000
        deadline = min(time.monotonic() + budget, self.deadline + budget)
        actions = []
        for step in steps:
            if time.monotonic() >= deadline:
                actions.append(self.event({"id": step.id, "caseId": case_id, "status": "failed",
                                           "reason": "cleanup_timeout"}))
                continue
            try:
                action = await asyncio.wait_for(
                    self.step(step, context, case_id=case_id, cleanup=True),
                    max(0.001, deadline - time.monotonic()),
                )
            except (TimeoutError, asyncio.CancelledError) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    self.cancelled = True
                action = self.event(
                    {
                        "id": step.id,
                        "caseId": case_id,
                        "status": "failed",
                        "reason": "cleanup_cancelled"
                        if isinstance(exc, asyncio.CancelledError)
                        else "cleanup_timeout",
                    }
                )
            actions.append(action)
        return actions

    async def repair(self, result: dict[str, Any], deadline: float) -> dict[str, Any]:
        from horus.core.runs import Runs

        original_id = self.intent["cleanupFrom"]
        root = Runs(self.service).root(self.project_ref, original_id)
        ledger: list[dict[str, Any]] = (
            json.loads((root / "ledger.json").read_bytes())
            if (root / "ledger.json").exists()
            else []
        )
        selections = {
            (step["caseId"], step["id"]): step["selected"]
            for step in recorded_events(root)
            if step.get("kind") == "branch-selection"
            and step.get("selected") in {"then", "else"}
        }
        result["residue"] = [
            {**entry, "reason": "original_write_outcome_unknown"}
            for entry in ledger
            if entry["state"] in {"intent", "unknown"}
            and not entry["cleanup"]
            and self.operations[entry["operationId"]]["method"] not in {"GET", "HEAD"}
        ]
        for case in self.plan.cases:
            output: dict[str, Any] = {"id": case.id, "steps": [], "cleanup": []}
            result["cases"].append(output)
            entries = [entry for entry in ledger if entry["caseId"] == case.id]
            if not entries:
                output["steps"].append(
                    self.event(
                        {
                            "caseId": case.id,
                            "id": "prior-execution",
                            "status": "passed",
                            "reason": "no_recorded_actions",
                        }
                    )
                )
                continue
            cleaned = {entry["stepId"] for entry in entries if entry["state"] == "cleaned"}
            if not case.cleanup or all(step.id in cleaned for step in case.cleanup):
                output["steps"].append(
                    self.event(
                        {
                            "caseId": case.id,
                            "id": "prior-cleanup",
                            "status": "passed" if case.cleanup else "inconclusive",
                            "reason": "cleanup_previously_verified"
                            if case.cleanup
                            else "no_approved_cleanup",
                        }
                    )
                )
                continue
            context = self.context(
                original_id,
                shared={
                    key: value
                    for entry in entries
                    for key, value in entry["variables"].items()
                    if "[REDACTED]" not in json.dumps(value)
                },
            )
            private_names = {
                name
                for _, node in walk_steps([*case.steps, *case.cleanup])
                if not isinstance(node, BranchStep)
                for http in [request_action(node)]
                if isinstance(http, HttpStep)
                for name, capture in http.captures.items()
                if capture.private
            }

            def variable(
                identity: str | None, name: str, names: frozenset[str] = frozenset(private_names)
            ) -> tuple[str | None, str]:
                return (identity if name in names else None, name)

            required = {
                variable(step.identityRef, name)
                for step in case.cleanup
                for namespace, name in VARIABLE.findall(step.model_dump_json())
                if namespace == "vars"
            } | {
                variable(step.identityRef, name) for step in case.cleanup for name in step.requires
            }
            required_steps = {name for step in case.cleanup for name in step.dependsOn}
            authentication = []
            executed = list(selected_steps(case.steps, {
                path: side for (case_id, path), side in selections.items() if case_id == case.id
            }))
            for path, node in reversed(executed):
                if not isinstance(node, HttpStep):
                    continue
                step = node.model_copy(update={"id": path})
                if not (
                    isinstance(step, HttpStep)
                    and step.role == "authentication"
                    and (
                        required.intersection(
                            variable(step.identityRef, name) for name in step.captures
                        )
                        or step.id in required_steps
                    )
                ):
                    continue
                authentication.append(step)
                required.difference_update(
                    variable(step.identityRef, name) for name in step.captures
                )
                required.update(
                    variable(step.identityRef, name)
                    for namespace, name in VARIABLE.findall(step.model_dump_json())
                    if namespace == "vars"
                )
                required.update(variable(step.identityRef, name) for name in step.requires)
                required_steps.update(step.dependsOn)
            self.state("running")
            for step in reversed(authentication):
                if any(e["stepId"] == step.id and e["state"] == "confirmed" for e in entries):
                    result["residue"].append(
                        {
                            "caseId": case.id,
                            "stepId": step.id,
                            "identityRef": step.identityRef,
                            "state": "unknown",
                            "reason": "original_credential_not_persisted",
                        }
                    )
                if self.cancelled or step.operationId not in self.config.policy.cleanupAllow:
                    action = self.event(
                        {
                            "caseId": case.id,
                            "id": step.id,
                            "status": "blocked",
                            "reason": "cancelled"
                            if self.cancelled
                            else "cleanup_authentication_denied",
                        }
                    )
                else:
                    try:
                        action = await asyncio.wait_for(
                            self.step(step, context, case_id=case.id),
                            max(0.001, deadline - time.monotonic()),
                        )
                    except (TimeoutError, asyncio.CancelledError) as exc:
                        if isinstance(exc, asyncio.CancelledError):
                            self.cancelled = True
                        action = self.event(
                            {
                                "caseId": case.id,
                                "id": step.id,
                                "status": "inconclusive",
                                "reason": "cleanup_authentication_interrupted",
                            }
                        )
                output["steps"].append(action)
            output["cleanup"] = await self.cleanup_steps(case.id, case.cleanup, context)
            if any(action["status"] != "passed" for action in output["cleanup"]):
                result["residue"].extend(
                    {**entry, "reason": "cleanup_not_verified"}
                    for entry in entries
                    if not entry["cleanup"]
                    and (
                        entry["variables"]
                        or self.operations[entry["operationId"]]["method"] not in {"GET", "HEAD"}
                    )
                )
                result["residue"].extend(
                    {
                        "caseId": case.id,
                        "stepId": entry["stepId"],
                        "identityRef": entry.get("identityRef"),
                        "runId": self.run_id,
                        "state": "unknown",
                        "reason": "cleanup_credential_revocation_unverified",
                    }
                    for entry in self.ledger
                    if entry["caseId"] == case.id and not entry["cleanup"]
                )
        return self.finish(result)

    def finish(self, result: dict[str, Any]) -> dict[str, Any]:
        if not self.intent.get("cleanupFrom"):
            result["scheduling"] = {
                "functionalCaseLimit": self.config.policy.limits.maxFunctionalParallelCases,
                "peakFunctionalCases": self.peak_functional_cases,
                "performanceCasesExclusive": True,
            }
        result["status"] = "cancelled" if self.cancelled else "completed"
        result["ledger"] = self.safe_ledger()
        self.state("reporting")
        return write_report(self.service, self.root, result)
