"""Readable Markdown results, with the same planned Mermaid diagrams as plan.md."""

import json
from typing import Any

from horus.core.models import Plan, Step
from horus.core.presentation import (
    action,
    check_title,
    markdown_text,
    mermaid_diagram,
    step_title,
)
from horus.core.report_text import labels
from horus.core.steps import walk_steps


def cell(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False)
    return markdown_text(
        serialized if len(serialized) <= 512 else serialized[:512] + "… → report.json"
    )


def render_markdown(result: dict[str, Any], plan: dict[str, Any] | None, evidence: str = "") -> str:
    design = plan or result.get("testDesign", {})
    style = design.get("report", {})
    text = labels(style.get("language", "en"), style.get("labels"))
    m = markdown_text
    understanding = design.get("businessUnderstanding", {})
    cases = design.get("cases", [])

    def flatten(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for step in steps for row in [step, *flatten(step.get("steps", []))]]

    records = {
        (step.get("caseId", case["id"]), step["id"]): step
        for case in result["cases"]
        for step in flatten([*case["steps"], *case["cleanup"]])
    }
    lines = [
        f"# {text['report']} · {m(design.get('name', 'horus'))}",
        "",
        f"**{text.get(result.get('verdict', 'inconclusive'), text['inconclusive'])}**",
        "",
        m(understanding.get("summary", "")),
        "",
        f"- {text['target']}: {m(result.get('target', '—'))}",
        f"- {text['environment']}: {m(result.get('environment', '—'))}",
        f"- {text['run']}: {m(result['runId'])}",
        "",
        text["coverage_note"],
        "",
        f"## {text['logic']}",
        "",
        text["flow_note"],
        "",
    ]
    for flow in understanding.get("flows", []):
        lines.append("- " + m(flow["description"]))
    if understanding.get("flows"):
        lines.append("")
    for case in cases:
        lines.extend(
            ["### " + m(case.get("title") or case["id"]), "", mermaid_diagram([case], text), ""]
        )
    model = Plan.model_validate(design) if cases else None
    lines.extend(["## " + text["results"], ""])
    for case in cases:
        lines.extend(["### " + m(case.get("title") or case["id"]) + " · " + text[case["kind"]], ""])
        # Use the canonical step walker so nested branch paths match execution records.
        assert model is not None
        planned_case = next(c for c in model.cases if c.id == case["id"])
        for cleanup in (False, True):
            steps: list[Step] = [*planned_case.cleanup] if cleanup else planned_case.steps
            if cleanup and steps:
                lines.extend(["#### " + text["cleanup"], ""])
            for path, step_model in walk_steps(steps):
                step = step_model.model_dump(mode="json")
                row = records.get((case["id"], path), {"status": "not_run"})
                status = "not_selected" if row.get("reason") == "not_selected" else row["status"]
                lines.extend([f"**{m(step_title(step, text))}** · {text.get(status, status)}", ""])
                if step.get("kind") == "branch":
                    continue
                a = action(step)
                if response := row.get("response"):
                    observations = []
                    if "status" in response:
                        observations.append(text["request_status"] + ": " + str(response["status"]))
                    if "elapsedMs" in response:
                        observations.append(text["duration"] + f": {response['elapsedMs']:.3g} ms")
                    lines.extend([" · ".join(observations), ""])
                observed = {check["id"]: check for check in row.get("assertions", [])}
                lines.extend(
                    [
                        "| "
                        + " | ".join(text[k] for k in ["checks", "outcome", "expected", "actual"])
                        + " |",
                        "| --- | --- | --- | --- |",
                    ]
                )
                for check in a.get("assertions", []):
                    found = observed.get(check["id"], {})
                    check_status = found.get(
                        "status",
                        "aggregate_only"
                        if "metrics" in row
                        else status
                        if status in {"not_run", "skipped", "blocked", "not_selected"}
                        else "not_evaluated",
                    )
                    expected = (
                        text["exists"]
                        if check.get("operator") == "exists"
                        else text.get(check.get("operator", "eq"), "")
                        + " "
                        + cell(found["expected"])
                        if "expected" in found
                        else "—"
                    )
                    actual = cell(found["actual"]) if "actual" in found else "—"
                    lines.append(
                        "| "
                        + " | ".join(
                            [
                                m(check_title(check, text)),
                                text.get(check_status, check_status),
                                expected,
                                actual,
                            ]
                        )
                        + " |"
                    )
                lines.append("")
                if step.get("kind") == "poll":
                    lines.extend(
                        [
                            f"{text['attempts']}: ≤ {step['maxAttempts']}; "
                            f"{step['intervalMs']} ms ×; ≤ {step['timeoutMs'] / 1000:g} s",
                            "",
                        ]
                    )
                if metrics := row.get("metrics"):
                    lines.extend(
                        ["| " + text["results"] + " | " + text["actual"] + " |", "| --- | --- |"]
                    )
                    for key in [
                        "configuredVus",
                        "completedIterations",
                        "sampleCount",
                        "latencyP95Ms",
                        "latencyP99Ms",
                        "httpErrorRate",
                        "dataCheckFailureRate",
                    ]:
                        if key not in metrics:
                            continue
                        value = metrics[key]
                        display = (
                            f"{value * 100:.2f}%"
                            if key.endswith("Rate")
                            else f"{value:.3g} ms"
                            if key.endswith("Ms")
                            else str(value)
                        )
                        title = text.get("vus" if key == "configuredVus" else key, key)
                        lines.append(f"| {title} | {m(display)} |")
                    lines.extend(["", text["latency_help"], "", text["aggregate_only"], ""])
                for threshold in row.get("thresholds", []):
                    lines.extend(
                        [
                            f"- {text['threshold']}: "
                            f"{text.get(threshold['metric'], threshold['metric'])} "
                            f"{'≤' if threshold['operator'] == 'lte' else '='} "
                            f"{threshold['expected']}; {text['actual']}: "
                            f"{threshold['actual']} · {text[threshold['status']]}",
                            "",
                        ]
                    )
    lines.extend(["## " + text["scope"], ""])
    for key, heading in [
        ("rules", "rules"),
        ("assumptions", "unknowns"),
        ("conflicts", "unknowns"),
        ("unknowns", "unknowns"),
    ]:
        if entries := understanding.get(key):
            lines.extend(["### " + text[heading], ""])
            for entry in entries:
                lines.append(
                    "- "
                    + m(entry["description"])
                    + (" · " + m(entry["impact"]) if entry.get("impact") else "")
                )
            lines.append("")
    if gaps := design.get("coverage", {}).get("uncovered"):
        lines.extend(["### " + text["uncovered"], ""])
        lines.extend("- " + m(gap["itemId"]) + ": " + m(gap["reason"]) for gap in gaps)
        lines.append("")
    lines.extend(
        [
            "### " + text["residue"],
            "",
            text["unresolved"] if result.get("residue") else text["clean"],
            "",
            "## " + text["files"],
            "",
            "[plan.md](plan.md) · [plan.json](plan.json) · "
            "[report.json](report.json) · [junit.xml](junit.xml)",
            "",
        ]
    )
    if evidence:
        # External evidence stays plain text inside the appendix.
        from html import escape

        lines.extend(
            [
                "<details>",
                "<summary>" + text["details"] + "</summary>",
                "",
                "<pre>" + escape(evidence, quote=False) + "</pre>",
                "</details>",
                "",
            ]
        )
    return "\n".join(lines)
