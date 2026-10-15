import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from horus.core.models import BranchStep, LoadStep, Plan, ResourceMetrics, Step
from horus.core.resources import resource_summary
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


def report_context(service: Service, result: dict[str, Any]) -> None:
    store = service.project(result["projectRef"])
    issues = []
    try:
        plan = Plan.model_validate(store.get("plans", result["planRef"]))
        result["testDesign"] = {
            key: value for key, value in plan.model_dump(mode="json").items()
            if key in {"name", "sourceRefs", "businessUnderstanding", "coverage",
                       "supportingContext", "identityProfiles", "parentPlanRef", "changeReason"}
        }
        result["executionCoverage"] = execution_coverage(plan, result["cases"])
    except (HorusError, ValidationError) as exc:
        issues.append({"code": exc.code if isinstance(exc, HorusError) else "invalid_plan",
                       "location": "testDesign"})
    try:
        if "configRef" in result and "environment" in result:
            config = store.get("configs", result["configRef"])
            result["target"] = config["environments"][result["environment"]]["baseUrl"]
        else:
            review = store.get("reviews", result["reviewRef"])
            result["target"] = review["scope"]["target"]
    except (HorusError, KeyError) as exc:
        issues.append({"code": exc.code if isinstance(exc, HorusError) else "missing_target",
                       "location": "target"})
    if issues:
        result["reportIssues"] = issues
        if result["verdict"] == "passed":
            result["verdict"] = "inconclusive"


def design_lines(result: dict[str, Any]) -> list[str]:
    lines = [f"Target: {result.get('target', 'unavailable')}",
             f"Environment: {result.get('environment', 'unavailable')}", ""]
    if issues := result.get("reportIssues"):
        lines.extend(["Report context could not be fully verified:", "",
                      "```json", json.dumps(issues, indent=2), "```", ""])
    if not (design := result.get("testDesign")):
        return lines
    understanding = design["businessUnderstanding"]
    lines.extend(["## Test design", "", design["name"], "", understanding["summary"], "",
                  "Sources: " + ", ".join(design["sourceRefs"]), ""])
    if design.get("parentPlanRef"):
        lines.extend([f"Parent plan: {design['parentPlanRef']}",
                      f"Change reason: {design['changeReason']}", ""])
    for key in ["actors", "resources", "states", "flows", "rules", "dataDependencies",
                "assumptions", "conflicts", "unknowns"]:
        if items := understanding[key]:
            lines.extend(["### " + key, ""])
            for item in items:
                label = item.get("id", item.get("resourceId", ""))
                detail = item.get("description", json.dumps(item, ensure_ascii=False))
                lines.append(f"- {label}: {detail}")
                if item.get("impact"):
                    lines.append(f"  Impact: {item['impact']}")
                if item.get("evidenceRefs"):
                    lines.append("  Evidence: " + ", ".join(item["evidenceRefs"]))
            lines.append("")
    lines.extend(["## Planned and observed coverage", "",
                  "not_run and not_evaluated do not count as passes. Load assertions have "
                  "aggregate evidence only; see their step metrics, not individual pass counts.",
                  ""])
    observed = result["executionCoverage"]["assertions"]
    for mapping in design["coverage"]["flows"]:
        lines.append(f"- Flow {mapping['flowId']}: " + ", ".join(mapping["caseIds"]))
    for mapping in design["coverage"]["rules"]:
        lines.append(f"- Rule {mapping['ruleId']}: " + "; ".join(
            f"{ref}: {observed[ref]['status']}" for ref in mapping["assertionRefs"]
            if ref in observed))
    for mapping in design["coverage"]["dataDependencies"]:
        lines.append(f"- Dependency {mapping['dependencyId']}: {mapping['captureRef']} → "
                     + ", ".join(mapping["consumerStepRefs"]))
    for item in design["coverage"]["uncovered"]:
        lines.append(f"- Uncovered {item['itemId']}: {item['reason']}")
    for item in design["supportingContext"]:
        lines.extend(["", f"Supporting context {item['id']} ({item['source']}): {item['text']}"])
    return [*lines, ""]


def evidence_excerpt(value: Any) -> Any:
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) <= 8192:
        return value
    return {"excerpt": serialized[:8192], "truncated": True,
            "totalCharacters": len(serialized), "fullEvidence": "report.json"}


def write_report(service: Service, root: Path, result: dict[str, Any]) -> dict[str, Any]:
    result["verdict"] = verdict(result["cases"])
    if (result["status"] in {"cancelled", "interrupted"} or result.get("residue")) and result[
        "verdict"
    ] == "passed":
        result["verdict"] = "inconclusive"
    report_context(service, result)
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
    lines.extend(design_lines(result))
    if scheduling := result.get("scheduling"):
        lines.extend([
            f"Functional cases: peak {scheduling['peakFunctionalCases']}, "
            f"approved limit {scheduling['functionalCaseLimit']} (including cleanup).",
            "Performance cases run exclusively; functional concurrency is not load evidence.",
            "",
        ])
    suite = ET.Element("testsuite", name="horus")
    if result.get("cleanupFrom"):
        lines.extend(
            [f"Cleanup of: {result['cleanupFrom']}", "", "Business writes were not replayed.", ""]
        )
    count = 0
    failures = 0
    for case in result["cases"]:
        lines.extend([f"## {case['id']}", ""])
        for step in result_steps([*case["steps"], *case["cleanup"]]):
            if step.get("kind") in {"poll-attempt", "branch-selection"}:
                lines.append(f"- {step['id']}: observation")
                continue
            if step.get("kind") == "branch" and step["status"] not in {"error", "blocked"}:
                lines.extend([f"- {step['id']}: {step.get('selected', step.get('reason'))}", "",
                              "```json", json.dumps(step.get("conditions", []), indent=2),
                              "```", ""])
                continue
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
            if step.get("kind") == "poll" and "observations" in step:
                observations = json.dumps(step["observations"], indent=2)
                lines.extend(["", f"Polling: {step['attempts']} attempts", "",
                              "```json", observations, "```", ""])
                ET.SubElement(node, "system-out").text = observations
            if step["status"] != "passed" or step.get("failureSamples"):
                details = {
                    "reason": step.get("reason"),
                    "assertions": [
                        c for c in step.get("assertions", []) if c["status"] != "passed"
                    ],
                    "thresholds": [
                        t for t in step.get("thresholds", []) if t["status"] != "passed"
                    ],
                }
                for field in ("request", "response", "failureSamples"):
                    if field in step:
                        details[field] = evidence_excerpt(step[field])
                if "failureSamples" in step:
                    details["retainedFailureSamples"] = len(step["failureSamples"])
                    details["samplingLimitPerVu"] = step.get("samplingLimitPerVu")
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
    summary = {k: v for k, v in result.items()
               if k not in {"cases", "testDesign", "executionCoverage"}}
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
