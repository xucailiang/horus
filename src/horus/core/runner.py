import asyncio
import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from horus.adapters.hurl import MISSING, HurlAdapter, pointer
from horus.adapters.k6 import K6Adapter
from horus.adapters.process import check_engine
from horus.core.models import HttpStep, LoadStep, Plan, ProjectConfig, Request, ResourceMetrics
from horus.core.resources import resource_summary
from horus.core.service import VARIABLE, Service
from horus.core.store import HorusError, atomic_write, file_lock
from horus.core.values import CaseContext, Redactor, resolve


def verdict(cases: list[dict[str, Any]]) -> str:
    steps = [step for case in cases for step in [*case["steps"], *case["cleanup"]]]
    states = [step["status"] for step in steps]
    for status, result in [
        ("error", "error"),
        ("failed", "failed"),
        ("blocked", "inconclusive"),
        ("inconclusive", "inconclusive"),
    ]:
        if status in states:
            return result
    if any(step.get("reason") == "missing_cleanup_inputs" for step in steps):
        return "inconclusive"
    return "passed" if states else "inconclusive"


def write_report(service: Service, root: Path, result: dict[str, Any]) -> dict[str, Any]:
    result["verdict"] = verdict(result["cases"])
    if (result["status"] in {"cancelled", "interrupted"} or result.get("residue")) and result[
        "verdict"
    ] == "passed":
        result["verdict"] = "inconclusive"
    result.setdefault("finishedAt", time.time())
    lines = [
        f"# {result['verdict'].upper()}",
        "",
        f"Run: {result['runId']}",
        f"Plan: {result['planRef']}",
        f"Review: {result['reviewRef']}",
        "",
        "Coverage is limited to the cases and checks below.",
        "",
    ]
    suite = ET.Element("testsuite", name="horus")
    if result.get("cleanupFrom"):
        lines.extend(
            [f"Cleanup of: {result['cleanupFrom']}", "", "Business writes were not replayed.", ""]
        )
    count = 0
    failures = 0
    for case in result["cases"]:
        lines.extend([f"## {case['id']}", ""])
        for step in [*case["steps"], *case["cleanup"]]:
            count += 1
            identity = f" ({step['identityRef']})" if step.get("identityRef") is not None else ""
            lines.append(f"- {step['id']}{identity}: {step['status']}")
            node = ET.SubElement(suite, "testcase", classname=case["id"], name=step["id"])
            if step.get("identityRef") is not None:
                properties = ET.SubElement(node, "properties")
                ET.SubElement(properties, "property", name="identityRef", value=step["identityRef"])
            if step["status"] in {"error", "failed"}:
                failures += 1
                ET.SubElement(node, "failure", message=step["status"]).text = json.dumps(step)
            elif step["status"] in {"blocked", "skipped", "inconclusive"}:
                ET.SubElement(node, "skipped", message=step["status"])
            if "metrics" in step:
                lines.extend(["", "```json", json.dumps(step["metrics"], indent=2), "```", ""])
            if step["status"] in {"failed", "error"}:
                details = {
                    "reason": step.get("reason"),
                    "assertions": [
                        c for c in step.get("assertions", []) if c["status"] != "passed"
                    ],
                    "thresholds": [
                        t for t in step.get("thresholds", []) if t["status"] != "passed"
                    ],
                }
                lines.extend(["", "```json", json.dumps(details, indent=2), "```", ""])
    suite.set("tests", str(count))
    suite.set("failures", str(failures))
    if result.get("residue"):
        lines.extend(
            [
                "## Recovery evidence",
                "",
                "These records include outcomes or resources that could not be fully resolved.",
                "",
                "```json",
                json.dumps(result["residue"], indent=2),
                "```",
                "",
            ]
        )
    if "resourceMetrics" in result:
        lines.extend(resource_summary(ResourceMetrics.model_validate(result["resourceMetrics"])))
    text = "\n".join(lines) + "\n"
    artifact = service.project(result["projectRef"]).put(
        "artifacts", {"kind": "report", "content": text}
    )
    result["artifactRef"] = artifact
    atomic_write(root / "report.json", result)
    (root / "report.md").write_text(text)
    (root / "junit.xml").write_bytes(ET.tostring(suite, encoding="utf-8", xml_declaration=True))
    summary = {k: v for k, v in result.items() if k != "cases"}
    summary["counts"] = {"steps": count, "failures": failures}
    atomic_write(root / "summary.json", summary)
    atomic_write(root / "state.json", summary)
    return summary


def interrupted_report(service: Service, project_ref: str, run_id: str) -> dict[str, Any]:
    from horus.core.runs import Runs

    root = Runs(service).root(project_ref, run_id)
    with file_lock(root / ".recover.lock"):
        intent = json.loads((root / "intent.json").read_bytes())
        events = []
        if (root / "events.jsonl").exists():
            for line in (root / "events.jsonl").read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    break  # A trailing partial record is possible after a hard kill.
        return write_report(
            service,
            root,
            {
                **intent,
                "status": "interrupted",
                "cases": [
                    {
                        "id": "interrupted",
                        "steps": [
                            *events,
                            {
                                "id": "interruption",
                                "status": "inconclusive",
                                "reason": "Worker disappeared; writes were not replayed",
                            },
                        ],
                        "cleanup": [],
                    }
                ],
                "residue": json.loads((root / "ledger.json").read_bytes())
                if (root / "ledger.json").exists()
                else [],
            },
        )


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
        self.active_load = False

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

    async def step(
        self,
        step: HttpStep | LoadStep,
        context: CaseContext,
        *,
        case_id: str,
        cleanup: bool = False,
    ) -> dict[str, Any]:
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
                    {**record, "status": "skipped", "reason": "missing_cleanup_inputs"}
                )
            request = Request.model_validate(resolve(step.request.model_dump(mode="json"), values))
            operation = self.operations[step.operationId]
            self.ledger.append(entry)
            self.persist_ledger()
            if isinstance(step, LoadStep):
                self.active_load = True
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
                    self.active_load = False
                record.update(result)
                entry["state"] = "confirmed"
            else:
                response = await HurlAdapter().request(operation, self.base_url, request)
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
                        raise HorusError("capture_missing", name)
                    context.capture(step.identityRef, name, capture, value)
                    values["vars"][name] = value
                    if capture.secret or capture.private:
                        self.redactor.register(value)
                    else:
                        entry["variables"][name] = value
                entry["state"] = "confirmed" if 200 <= response.status < 300 else "unknown"
                self.persist_ledger()
                assertions = [
                    a.model_validate(resolve(a.model_dump(mode="json"), values))
                    for a in step.assertions
                ]
                checks = await response.check(assertions)
                status = (
                    "failed"
                    if any(c["status"] == "failed" for c in checks)
                    else "inconclusive"
                    if any(c["status"] == "inconclusive" for c in checks)
                    else "passed"
                )
                record.update(
                    {
                        "status": status,
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

    async def execute(self) -> dict[str, Any]:
        self.service.review_gate(
            self.project_ref,
            self.intent["planRef"],
            self.intent["reviewRef"],
            self.intent["environment"],
        )
        versions = {"hurl": await check_engine("hurl")}
        if not self.intent.get("cleanupFrom") and any(
            isinstance(step, LoadStep) for case in self.plan.cases for step in case.steps
        ):
            versions["k6"] = await check_engine("k6")
        result: dict[str, Any] = {
            **self.intent,
            "engineVersions": versions,
            "status": "completed",
            "cases": [],
        }
        deadline = time.monotonic() + self.config.policy.limits.maxRunDurationMs / 1000
        if self.intent.get("cleanupFrom"):
            return await self.repair(result, deadline)
        for case in self.plan.cases:
            output: dict[str, Any] = {"id": case.id, "steps": [], "cleanup": []}
            result["cases"].append(output)
            context = self.context()
            blocked = self.cancelled or time.monotonic() >= deadline
            self.state("running")
            try:
                for step in case.steps:
                    if blocked or self.cancelled:
                        output["steps"].append(
                            self.event({"caseId": case.id, "id": step.id, "status": "blocked"})
                        )
                        continue
                    action = await asyncio.wait_for(
                        self.step(step, context, case_id=case.id),
                        max(0.001, deadline - time.monotonic()),
                    )
                    output["steps"].append(action)
                    blocked = action["status"] in {"failed", "error", "blocked"}
            except asyncio.CancelledError:
                self.cancelled = True
                output["steps"].append(
                    self.event({"caseId": case.id, "id": "cancelled", "status": "inconclusive"})
                )
            except TimeoutError:
                output["steps"].append(
                    self.event({"caseId": case.id, "id": "timeout", "status": "inconclusive"})
                )
            finally:
                output["cleanup"] = await self.cleanup_steps(case.id, case.cleanup, context)
        return self.finish(result)

    async def cleanup_steps(
        self,
        case_id: str,
        steps: list[HttpStep],
        context: CaseContext,
    ) -> list[dict[str, Any]]:
        self.state("cleaning")
        deadline = time.monotonic() + self.config.policy.limits.maxCleanupDurationMs / 1000
        actions = []
        for step in steps:
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
                for step in [*case.steps, *case.cleanup]
                if isinstance(step, HttpStep)
                for name, capture in step.captures.items()
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
            for step in reversed(case.steps):
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
        result["status"] = "cancelled" if self.cancelled else "completed"
        result["ledger"] = self.safe_ledger()
        self.state("reporting")
        return write_report(self.service, self.root, result)
