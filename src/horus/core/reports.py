import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from horus.core.markdown_report import render_details, render_markdown
from horus.core.models import BranchStep, LoadStep, Plan, ProjectConfig, Step
from horus.core.presentation import mermaid_diagram, plan_document
from horus.core.report_text import labels
from horus.core.service import Service
from horus.core.steps import request_action, walk_steps
from horus.core.store import HorusError, atomic_write, file_lock


def result_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for step in steps for entry in [step, *result_steps(step.get("steps", []))]]


def collapse_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    covered = {(parent["caseId"], child["id"])
               for parent in events if parent.get("kind") == "branch"
               for child in result_steps(parent.get("steps", []))}
    completed = {(event["caseId"], event["id"])
                 for event in events if event.get("kind") == "branch"}
    return [event for event in events if (event.get("caseId"), event["id"]) not in covered
            and not (event.get("kind") == "branch-selection" and
                     (event["caseId"], event["id"]) in completed)]


def recorded_events(root: Path) -> list[dict[str, Any]]:
    events = []
    if (root / "events.jsonl").exists():
        for line in (root / "events.jsonl").read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                break  # A trailing partial record is possible after a hard kill.
    return events


def verdict(cases: list[dict[str, Any]]) -> str:
    steps = [step for case in cases for step in result_steps([*case["steps"], *case["cleanup"]])]
    states = [step["status"] for step in steps if step["status"] != "observed"]
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
    return "passed" if "passed" in states else "inconclusive"


def execution_coverage(
    plan: Plan, cases: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    records = {
        (step.get("caseId", case["id"]), step["id"]): step
        for case in cases for step in result_steps([*case["steps"], *case["cleanup"]])
        if step.get("kind") not in {"poll-attempt", "branch-selection"}
    }
    step_results: dict[str, dict[str, Any]] = {}
    assertion_results: dict[str, dict[str, Any]] = {}
    for case in plan.cases:
        for cleanup in (False, True):
            steps: list[Step] = [*case.cleanup] if cleanup else case.steps
            for path, step in walk_steps(steps):
                ref = case.id + "." + path
                record = records.get((case.id, path), {})
                status = record.get("status", "not_run")
                entry = {"kind": step.kind, "status": status, "cleanup": cleanup}
                if "reason" in record:
                    entry["reason"] = record["reason"]
                step_results[ref] = entry
                if isinstance(step, BranchStep):
                    continue
                action = request_action(step)
                entry["operationId"] = action.operationId
                checks = {check["id"]: check for check in record.get("assertions", [])}
                for assertion in action.assertions:
                    check = checks.get(assertion.id)
                    if check is not None:
                        observed = check["status"]
                    elif isinstance(step, LoadStep) and "metrics" in record:
                        observed = "aggregate_only"
                    elif status in {"not_run", "blocked", "skipped"}:
                        observed = status
                    else:
                        observed = "not_evaluated"
                    assertion_results[ref + "." + assertion.id] = {
                        "kind": "assertion", "status": observed, "stepRef": ref,
                        "stepStatus": status, "evidenceRefs": assertion.evidenceRefs,
                        **({"reason": record["reason"]} if "reason" in record else {}),
                    }
    return {"steps": step_results, "assertions": assertion_results}


def report_context(service: Service, result: dict[str, Any]) -> Plan | None:
    store = service.project(result["projectRef"])
    issues = []
    plan = None
    try:
        plan = Plan.model_validate(store.get("plans", result["planRef"]))
        result["testDesign"] = {
            key: value
            for key, value in plan.model_dump(mode="json").items()
            if key
            in {
                "name",
                "sourceRefs",
                "businessUnderstanding",
                "coverage",
                "supportingContext",
                "identityProfiles",
                "parentPlanRef",
                "changeReason",
                "report",
            }
        }
        result["executionCoverage"] = execution_coverage(plan, result["cases"])
    except (HorusError, ValidationError) as exc:
        issues.append(
            {
                "code": exc.code if isinstance(exc, HorusError) else "invalid_plan",
                "location": "testDesign",
            }
        )
    try:
        if "configRef" in result and "environment" in result:
            config = store.get("configs", result["configRef"])
            result["target"] = config["environments"][result["environment"]]["baseUrl"]
        else:
            review = store.get("reviews", result["reviewRef"])
            result["target"] = review["scope"]["target"]
    except (HorusError, KeyError) as exc:
        issues.append(
            {
                "code": exc.code if isinstance(exc, HorusError) else "missing_target",
                "location": "target",
            }
        )
    if issues:
        result["reportIssues"] = issues
        if result["verdict"] == "passed":
            result["verdict"] = "inconclusive"

    return plan



def snapshot_test(service: Service, root: Path, intent: dict[str, Any]) -> dict[str, str]:
    """One run is one test version: its plan and reports live in this same directory."""
    store = service.project(intent["projectRef"])
    raw = store.get("plans", intent["planRef"])
    plan = Plan.model_validate(raw)
    review = store.get("reviews", intent["reviewRef"])
    config = ProjectConfig.model_validate(store.get("configs", intent["configRef"]))
    content = review.get("previewContent") if review["planRef"] == intent["planRef"] else None
    if not content:
        validation = service._validate(store, plan, config, intent["environment"])
        content = plan_document(plan, config, intent["environment"], validation)
    if "```mermaid\n" not in content:
        text = labels(plan.report.language, plan.report.labels)
        content += "\n\n## " + text["logic"] + "\n\n" + "\n\n".join(
            mermaid_diagram([case], text) for case in plan.model_dump(mode="json")["cases"])
    atomic_write(root / "plan.json", raw)
    atomic_write(root / "config.json", config.model_dump(mode="json"))
    atomic_write(root / "review.json", review)
    (root / "plan.md").write_text(content, encoding="utf-8")
    (root / "plan.md").chmod(0o600)
    return {"testDirectory": str(root), "reportPath": str(root / "report.md")}


def write_report(service: Service, root: Path, result: dict[str, Any]) -> dict[str, Any]:
    result["verdict"] = verdict(result["cases"])
    if (result["status"] in {"cancelled", "interrupted"} or result.get("residue")) and result[
        "verdict"
    ] == "passed":
        result["verdict"] = "inconclusive"
    plan = report_context(service, result)
    if plan is not None:
        try:
            result.update(snapshot_test(service, root, result))
        except (HorusError, ValidationError, KeyError) as exc:
            result.setdefault("reportIssues", []).append(
                {
                    "code": exc.code if isinstance(exc, HorusError) else "snapshot_unavailable",
                    "location": "planSnapshot",
                }
            )
            if result["verdict"] == "passed":
                result["verdict"] = "inconclusive"
    result.setdefault("finishedAt", time.time())
    suite = ET.Element("testsuite", name="horus")
    count = 0
    failures = 0
    for case in result["cases"]:
        for step in result_steps([*case["steps"], *case["cleanup"]]):
            if step.get("kind") in {"poll-attempt", "branch-selection"}:
                continue
            if step.get("kind") == "branch" and step["status"] not in {"error", "blocked"}:
                continue
            count += 1
            node = ET.SubElement(suite, "testcase", classname=case["id"], name=step["id"])
            if step.get("identityRef") is not None:
                properties = ET.SubElement(node, "properties")
                ET.SubElement(properties, "property", name="identityRef", value=step["identityRef"])
            if step["status"] in {"error", "failed"}:
                failures += 1
                ET.SubElement(node, "failure", message=step["status"]).text = json.dumps(step)
            elif step["status"] in {"blocked", "skipped", "inconclusive"}:
                ET.SubElement(node, "skipped", message=step["status"])
            if step.get("kind") == "poll" and "observations" in step:
                ET.SubElement(node, "system-out").text = json.dumps(step["observations"])
    suite.set("tests", str(count))
    suite.set("failures", str(failures))
    raw = plan.model_dump(mode="json") if plan else None
    text = render_markdown(result, raw)
    details = render_details(result, raw)
    result["detailsArtifactRef"] = service.project(result["projectRef"]).put(
        "artifacts", {"kind": "report-details", "mediaType": "text/markdown", "content": details})
    result["detailsPath"] = str(root / "details.md")
    (root / "details.md").write_text(details, encoding="utf-8")
    (root / "details.md").chmod(0o600)
    artifact = service.project(result["projectRef"]).put(
        "artifacts", {"kind": "report", "mediaType": "text/markdown", "content": text}
    )
    result["artifactRef"] = artifact
    result.update(testDirectory=str(root), reportPath=str(root / "report.md"))
    result.pop("htmlArtifactRef", None)
    atomic_write(root / "report.json", result)
    (root / "report.md").write_text(text, encoding="utf-8")
    (root / "report.md").chmod(0o600)
    (root / "junit.xml").write_bytes(ET.tostring(suite, encoding="utf-8", xml_declaration=True))
    summary = {
        k: v for k, v in result.items() if k not in {"cases", "testDesign", "executionCoverage"}
    }
    summary["counts"] = {"steps": count, "failures": failures}
    atomic_write(root / "summary.json", summary)
    atomic_write(root / "state.json", summary)
    return summary


def interrupted_report(service: Service, project_ref: str, run_id: str) -> dict[str, Any]:
    from horus.core.runs import Runs

    root = Runs(service).root(project_ref, run_id)
    with file_lock(root / ".recover.lock"):
        intent = json.loads((root / "intent.json").read_bytes())
        events = recorded_events(root)
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
                            *collapse_events(events),
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
