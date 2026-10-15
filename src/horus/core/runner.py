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
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write, file_lock
from horus.core.values import Redactor, resolve


def verdict(cases: list[dict[str, Any]]) -> str:
    states = [step["status"] for case in cases for step in [*case["steps"], *case["cleanup"]]]
    for status, result in [
        ("error", "error"),
        ("failed", "failed"),
        ("blocked", "inconclusive"),
        ("inconclusive", "inconclusive"),
    ]:
        if status in states:
            return result
    return "passed" if states else "inconclusive"


def write_report(service: Service, root: Path, result: dict[str, Any]) -> dict[str, Any]:
    result["verdict"] = verdict(result["cases"])
    if result["status"] in {"cancelled", "interrupted"} and result["verdict"] == "passed":
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
    count = 0
    failures = 0
    for case in result["cases"]:
        lines.extend([f"## {case['id']}", ""])
        for step in [*case["steps"], *case["cleanup"]]:
            count += 1
            lines.append(f"- {step['id']}: {step['status']}")
            node = ET.SubElement(suite, "testcase", classname=case["id"], name=step["id"])
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
            {**entry, "variables": self.redactor.clean(entry["variables"])}
            for entry in self.ledger
        ]

    def persist_ledger(self) -> None:
        atomic_write(self.root / "ledger.json", self.safe_ledger())

    async def step(
        self,
        step: HttpStep | LoadStep,
        context: dict[str, dict[str, Any]],
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
        try:
            if isinstance(step, HttpStep) and not set(step.requires).issubset(context["vars"]):
                return self.event(
                    {**record, "status": "skipped", "reason": "missing_cleanup_inputs"}
                )
            materialized = resolve(step.model_dump(mode="json"), context)
            request = Request.model_validate(materialized["request"])
            operation = self.operations[step.operationId]
            self.ledger.append(entry)
            self.persist_ledger()
            if isinstance(step, LoadStep):
                self.active_load = True
                try:
                    result = await K6Adapter().run(
                        operation, self.base_url, LoadStep.model_validate(materialized)
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
                    context["vars"][name] = value
                    if capture.secret:
                        self.redactor.register(value)
                    else:
                        entry["variables"][name] = value
                entry["state"] = "confirmed" if 200 <= response.status < 300 else "unknown"
                self.persist_ledger()
                assertions = [
                    a.model_validate(resolve(a.model_dump(mode="json"), context))
                    for a in step.assertions
                ]
                checks = response.check(assertions)
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
        if any(isinstance(step, LoadStep) for case in self.plan.cases for step in case.steps):
            versions["k6"] = await check_engine("k6")
        result: dict[str, Any] = {
            **self.intent,
            "engineVersions": versions,
            "status": "completed",
            "cases": [],
        }
        deadline = time.monotonic() + self.config.policy.limits.maxRunDurationMs / 1000
        for case in self.plan.cases:
            output: dict[str, Any] = {"id": case.id, "steps": [], "cleanup": []}
            result["cases"].append(output)
            context = {"vars": {}, "secrets": self.secrets, "run": {"id": self.run_id}}
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
                self.state("cleaning")
                cleanup_deadline = (
                    time.monotonic() + self.config.policy.limits.maxCleanupDurationMs / 1000
                )
                for cleanup in case.cleanup:
                    try:
                        action = await asyncio.wait_for(
                            self.step(cleanup, context, case_id=case.id, cleanup=True),
                            max(0.001, cleanup_deadline - time.monotonic()),
                        )
                    except TimeoutError:
                        action = self.event(
                            {
                                "id": cleanup.id,
                                "caseId": case.id,
                                "status": "failed",
                                "reason": "cleanup_timeout",
                            }
                        )
                    except asyncio.CancelledError:
                        self.cancelled = True
                        action = self.event(
                            {
                                "id": cleanup.id,
                                "caseId": case.id,
                                "status": "failed",
                                "reason": "cleanup_cancelled",
                            }
                        )
                    output["cleanup"].append(action)
        result["status"] = "cancelled" if self.cancelled else "completed"
        result["ledger"] = self.safe_ledger()
        self.state("reporting")
        return write_report(self.service, self.root, result)
