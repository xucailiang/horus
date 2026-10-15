import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from horus.core.models import ResourceMetrics
from horus.core.resources import resource_summary
from horus.core.service import Service
from horus.core.store import atomic_write, file_lock


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
