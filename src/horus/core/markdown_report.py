"""A short decision report and one separate, complete evidence document."""

import json
from typing import Any

from horus.core.models import BranchStep, Plan, Step
from horus.core.presentation import action, check_title, markdown_text, mermaid_diagram, step_title
from horus.core.report_text import labels
from horus.core.steps import walk_steps


def cell(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False)
    return markdown_text(
        serialized if len(serialized) <= 256 else serialized[:256] + "… → report.json"
    )


def result_rows(result: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    def flatten(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for step in steps for row in [step, *flatten(step.get("steps", []))]]

    return {
        (row.get("caseId", case["id"]), row["id"]): row
        for case in result["cases"]
        for row in flatten([*case["steps"], *case["cleanup"]])
        if row.get("kind") not in {"branch-selection", "poll-attempt"}
    }


def report_inputs(
    result: dict[str, Any], raw: dict[str, Any] | None
) -> tuple[
    dict[str, Any],
    dict[str, str],
    Plan | None,
]:
    design = raw or result.get("testDesign", {})
    style = design.get("report", {})
    return (
        design,
        labels(style.get("language", "en"), style.get("labels")),
        (Plan.model_validate(design) if design.get("cases") else None),
    )


def step_status(rows: list[dict[str, Any]]) -> str:
    statuses = [row["status"] for row in rows if row.get("reason") != "not_selected"]
    return next(
        (
            s
            for s in ["error", "failed", "inconclusive", "blocked", "not_run", "skipped"]
            if s in statuses
        ),
        "passed" if "passed" in statuses else "not_run",
    )


def display_metric(key: str, value: Any) -> str:
    if key.endswith("Rate"):
        return "< 0.01%" if 0 < value * 100 < 0.01 else f"{value * 100:.2f}%"
    if key.endswith("Ms"):
        return (
            "< 0.001 ms" if 0 < value < 0.001 else (f"{value:.3f}".rstrip("0").rstrip(".") + " ms")
        )
    return str(value)


def overview_cases(plan: Plan, text: dict[str, str]) -> list[dict[str, Any]]:
    """Keep business goals, dynamic branches and waiting; omit repeated HTTP mechanics."""

    def milestones(steps: list[Step], *, path: bool = False) -> list[dict[str, Any]]:
        result = []
        for step in steps:
            if isinstance(step, BranchStep):
                # Authentication routing is preparation rather than a business decision.
                if all(
                    action(child.model_dump(mode="json")).get("role") == "authentication"
                    for _, child in walk_steps([*step.thenSteps, *step.elseSteps])
                    if not isinstance(child, BranchStep)
                ):
                    continue
                result.append(
                    {
                        **step.model_dump(mode="json"),
                        "thenSteps": milestones(step.thenSteps, path=True),
                        "elseSteps": milestones(step.elseSteps, path=True),
                    }
                )
            elif step.kind == "poll":
                result.append(step.model_dump(mode="json"))
        if path and steps:
            last = steps[-1]
            if last.kind == "http" and last.role == "exercise":
                result.append(last.model_dump(mode="json"))
        return result

    return [
        {
            "id": case.id,
            "title": case.title or case.id,
            "steps": [{"id": "goal", "title": case.title or case.id}, *milestones(case.steps)],
            "cleanup": [{"id": "cleanup", "title": text["cleanup"]}] if case.cleanup else [],
        }
        for case in plan.cases
    ]


def render_markdown(result: dict[str, Any], raw: dict[str, Any] | None) -> str:
    design, text, plan = report_inputs(result, raw)
    records = result_rows(result)
    groups = []
    cleanup_rows: list[dict[str, Any]] = []
    if plan:
        for case in plan.cases:
            planned = [*walk_steps([*case.steps, *case.cleanup])]
            rows = [
                records.get((case.id, path), {"status": "not_run"})
                for path, step in planned
                if not isinstance(step, BranchStep)
            ]
            if result.get("cleanupFrom"):
                rows = [row for (case_id, _), row in records.items() if case_id == case.id]
            groups.append((case, step_status(rows), rows))
            if result.get("cleanupFrom"):
                cleanup_rows.extend(
                    row
                    for recorded in result["cases"]
                    if recorded["id"] == case.id
                    for row in recorded["cleanup"]
                )
            else:
                cleanup_rows.extend(
                    records.get((case.id, step.id), {"status": "not_run"}) for step in case.cleanup
                )
    passed = sum(status == "passed" for _, status, _ in groups)
    lines = [
        f"# {text['report']} · {markdown_text(design.get('name', 'horus'))}",
        "",
        f"**{text.get(result.get('verdict', 'inconclusive'), text['inconclusive'])}**"
        + (f" · {passed}/{len(groups)} {text['groups_passed']}" if groups else ""),
        "",
        f"{text['target']}: {markdown_text(result.get('target', '—'))}",
    ]
    if result.get("cleanupFrom"):
        lines.extend(["", text["recovery"]])
    if cleanup_rows:
        clean_passed = sum(row["status"] == "passed" for row in cleanup_rows)
        lines.extend(
            [
                "",
                f"{text['cleanup']}: {clean_passed}/{len(cleanup_rows)} "
                + text["passed"]
                + " · "
                + (
                    text["unresolved"]
                    if result.get("residue") or clean_passed != len(cleanup_rows)
                    else text["clean"]
                ),
            ]
        )
    elif result.get("residue"):
        lines.extend(["", text["unresolved"]])
    problems = [
        (key, row)
        for key, row in records.items()
        if row["status"] in {"failed", "error", "blocked", "inconclusive"}
        and (row.get("kind") != "branch" or row["status"] in {"error", "blocked"})
    ]
    seen_blocks = set()
    filtered = []
    for record_key, row in problems:
        if row["status"] == "blocked":
            cause = (record_key[0], row.get("reason"))
            if cause in seen_blocks:
                continue
            seen_blocks.add(cause)
        filtered.append((record_key, row))
    problems = filtered
    problems.sort(
        key=lambda item: ["error", "failed", "inconclusive", "blocked"].index(item[1]["status"])
    )
    if problems:
        lines.extend(
            [
                "",
                "## " + text["issues"],
                "",
                "| "
                + " | ".join(text[k] for k in ["steps", "outcome", "expected", "actual"])
                + " |",
                "| --- | --- | --- | --- |",
            ]
        )
        names = (
            {
                (case.id, path): step_title(step.model_dump(mode="json"), text)
                for case in plan.cases
                for path, step in walk_steps([*case.steps, *case.cleanup])
            }
            if plan
            else {}
        )
        for record_key, row in problems[:5]:
            check: dict[str, Any] = next(
                (
                    c
                    for c in [*row.get("assertions", []), *row.get("thresholds", [])]
                    if c["status"] != "passed"
                ),
                {},
            )
            expected = cell(check["expected"]) if "expected" in check else "—"
            actual = (
                cell(check["actual"])
                if "actual" in check
                else markdown_text(text.get(row.get("reason", ""), text["details_reason"]))
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        markdown_text(names.get(record_key, record_key[1])),
                        text.get(row["status"], row["status"]),
                        expected,
                        actual,
                    ]
                )
                + " |"
            )
        if len(problems) > 5:
            lines.append(f"\n{text['more_issues']}: {len(problems) - 5} → [details.md](details.md)")
        if result.get("reportIssues"):
            lines.extend(["", text["context_issue"]])
    if result.get("reportIssues") and not problems:
        lines.extend(["", "## " + text["issues"], "", text["context_issue"]])
    lines.extend(
        [
            "",
            "## " + text["business_results"],
            "",
            "| " + " | ".join(text[k] for k in ["business_goal", "outcome", "key_evidence"]) + " |",
            "| --- | --- | --- |",
        ]
    )
    for case, status, rows in groups:
        selected = [row for row in rows if row.get("reason") != "not_selected"]
        passed_rows = sum(row["status"] == "passed" for row in selected)
        skipped = len(rows) - len(selected)
        evidence = f"{passed_rows}/{len(selected)} {text['actions_passed']}"
        if skipped:
            evidence += f"; {skipped} {text['not_selected']}"
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_text(
                        (text["cleanup"] + " · " if result.get("cleanupFrom") else "")
                        + (case.title or case.id)
                    ),
                    text.get(status, status),
                    evidence,
                ]
            )
            + " |"
        )
    loads = [row for row in records.values() if row.get("metrics")]
    if loads:
        lines.extend(
            [
                "",
                "## " + text["performance"],
                "",
                "| "
                + " | ".join(text[k] for k in ["results", "expected", "actual", "outcome"])
                + " |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in loads:
            thresholds = {item["metric"]: item for item in row.get("thresholds", [])}
            if len(loads) > 1:
                lines.append(f"| **{markdown_text(row['id'])}** | | | |")
            for key in [
                "configuredVus",
                "completedIterations",
                "latencyP95Ms",
                "httpErrorRate",
                "dataCheckFailureRate",
            ]:
                if key not in row["metrics"]:
                    continue
                threshold = thresholds.get(key)
                expected = (
                    (
                        ("≤ " if threshold["operator"] == "lte" else "= ")
                        + display_metric(key, threshold["expected"])
                    )
                    if threshold
                    else "—"
                )
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            text.get("vus" if key == "configuredVus" else key, key),
                            expected,
                            display_metric(key, row["metrics"][key]),
                            text[threshold["status"]] if threshold else "—",
                        ]
                    )
                    + " |"
                )
    if result.get("resourceMetrics"):
        from horus.core.models import ResourceMetrics
        from horus.core.resources import resource_summary

        lines.extend(
            resource_summary(ResourceMetrics.model_validate(result["resourceMetrics"]), text)
        )
    if plan:
        lines.extend(
            [
                "",
                "## " + text["logic"],
                "",
                mermaid_diagram(
                    [
                        {
                            "id": c.id,
                            "steps": [
                                {
                                    "id": "cleanup",
                                    "title": text["cleanup"] + " · " + (c.title or c.id),
                                }
                            ],
                            "cleanup": [],
                        }
                        for c in plan.cases
                    ]
                    if result.get("cleanupFrom")
                    else overview_cases(plan, text),
                    text,
                    brief=True,
                ),
                "",
                text["overview_note"],
            ]
        )
    understanding = design.get("businessUnderstanding", {})
    findings = {
        item["id"]: item
        for key in ["conflicts", "unknowns", "assumptions"]
        for item in understanding.get(key, [])
    }
    limits = [item["description"] for item in understanding.get("conflicts", [])]
    for item in design.get("coverage", {}).get("uncovered", []):
        description = findings.get(item["itemId"], {}).get("description")
        limits.append((description + " · " if description else "") + item["reason"])
    limits = list(dict.fromkeys(limits))
    if limits:
        lines.extend(["", "## " + text["scope"], ""])
        lines.extend("- " + markdown_text(value) for value in limits[:3])
        if len(limits) > 3:
            lines.append(f"- {text['other_limits']}: {len(limits) - 3} → [plan.md](plan.md)")
    lines.extend(
        ["", "[plan.md](plan.md) · [details.md](details.md) · [report.json](report.json)", ""]
    )
    return "\n".join(lines)


def render_details(result: dict[str, Any], raw: dict[str, Any] | None) -> str:
    from html import escape

    design, text, plan = report_inputs(result, raw)
    records = result_rows(result)
    lines = [
        "# " + text["details"],
        "",
        f"{text['run']}: {result['runId']}",
        "",
        "[report.md](report.md) · [plan.md](plan.md) · [report.json](report.json)",
        "",
    ]
    planned = (
        {
            (case.id, path): step.model_dump(mode="json")
            for case in plan.cases
            for path, step in walk_steps(
                [*case.cleanup] if result.get("cleanupFrom") else [*case.steps, *case.cleanup]
            )
        }
        if plan
        else {}
    )
    # Include unplanned platform failures as well as every approved but unexecuted action.
    for record_key in dict.fromkeys([*planned, *records]):
        case_id, path = record_key
        step = planned.get(record_key, {"id": path})
        row = records.get(record_key, {"status": "not_run"})
        status = "not_selected" if row.get("reason") == "not_selected" else row["status"]
        lines.extend(
            [
                "## " + markdown_text(case_id + " · " + step_title(step, text)),
                "",
                text.get(status, status),
                "",
            ]
        )
        action_data = action(step)
        checks = {c["id"]: c for c in row.get("assertions", [])}
        if action_data.get("assertions"):
            lines.extend(
                [
                    "| "
                    + " | ".join(text[k] for k in ["checks", "outcome", "expected", "actual"])
                    + " |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for assertion in action_data["assertions"]:
                found = checks.get(assertion["id"], {})
                check_status = found.get(
                    "status",
                    "aggregate_only"
                    if "metrics" in row
                    else status
                    if status in {"not_run", "skipped", "not_selected", "blocked"}
                    else "not_evaluated",
                )
                expected = (
                    text["exists"]
                    if assertion.get("operator") == "exists"
                    else (cell(found["expected"]) if "expected" in found else "—")
                )
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            markdown_text(check_title(assertion, text))
                            + " · "
                            + markdown_text(case_id + "." + path + "." + assertion["id"]),
                            text.get(check_status, check_status),
                            expected,
                            cell(found["actual"]) if "actual" in found else "—",
                        ]
                    )
                    + " |"
                )
            lines.append("")
        payload = dict(row)
        for field in ["request", "response", "failureSamples", "observations"]:
            if field in payload:
                serialized = json.dumps(payload[field], ensure_ascii=False)
                if len(serialized) > 8192:
                    payload[field] = {
                        "excerpt": serialized[:8192],
                        "truncated": True,
                        "fullEvidence": "report.json",
                    }
        # The same values appear only once in tables; the remainder is the raw execution evidence.
        payload.pop("steps", None)  # Nested branch records have their own sections.
        if action_data.get("assertions"):
            known = {check["id"] for check in action_data["assertions"]}
            extra = [check for check in row.get("assertions", []) if check["id"] not in known]
            if extra:
                payload["assertions"] = extra
            else:
                payload.pop("assertions", None)
        if "failureSamples" in row:
            payload["retainedFailureSamples"] = len(row["failureSamples"])
        lines.extend(
            [
                "<pre>"
                + escape(json.dumps(payload, ensure_ascii=False, indent=2), quote=False)
                + "</pre>",
                "",
            ]
        )
    for key in ["scheduling", "residue", "reportIssues", "resourceMetrics"]:
        if result.get(key):
            lines.extend(
                [
                    "",
                    "## " + text.get(key, key),
                    "",
                    "<pre>"
                    + escape(json.dumps(result[key], ensure_ascii=False, indent=2), quote=False)
                    + "</pre>",
                    "",
                ]
            )
    return "\n".join(lines)
