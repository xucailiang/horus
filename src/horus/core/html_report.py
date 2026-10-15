"""Offline HTML: approved plan first, measured evidence second. No external assets."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from horus.core.presentation import action, check_title, predicate_text, step_title
from horus.core.report_text import labels


def h(value: Any) -> str:
    return escape(str(value), quote=True)


def json_block(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    if len(serialized) > 8192:
        serialized = json.dumps({"excerpt": serialized[:8192], "truncated": True,
                                 "fullEvidence": "report.json"}, ensure_ascii=False, indent=2)
    return "<pre>" + h(serialized) + "</pre>"


def cell_text(value: str, text: dict[str, str]) -> str:
    if len(value) <= 4096:
        return h(value)
    return h(value[:4096]) + '… <a href="report.json">' + h(text["evidence"]) + '</a>'


def badge(status: str, text: dict[str, str]) -> str:
    category = status if status in {"passed", "failed", "error"} else "neutral"
    return f'<span class="badge {category}">{h(text.get(status, status))}</span>'


def reason_text(reason: str, text: dict[str, str]) -> str:
    key = {
        "timeout": "time_limit_reason", "run_timeout": "time_limit_reason",
        "poll_timeout": "time_limit_reason", "cleanup_timeout": "time_limit_reason",
        "poll_attempts_exhausted": "wait_exhausted", "upstream_blocked": "not_started_reason",
        "case_not_started": "not_started_reason", "missing_cleanup_inputs": "missing_inputs_reason",
        "original_credential_not_persisted": "lost_credential",
        "cancelled_write_outcome_unknown": "unknown_write",
        "original_write_outcome_unknown": "unknown_write",
        "poll_response_rejected": "unexpected_response",
    }.get(reason, reason)
    return text.get(key, text["details_reason"])


def flatten(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for step in steps for entry in [step, *flatten(step.get("steps", []))]]


def planned_steps(
    steps: list[dict[str, Any]], prefix: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for step in steps:
        path = prefix + step["id"]
        rows.append((path, step))
        if step.get("kind") == "branch":
            for side in ["then", "else"]:
                rows.extend(planned_steps(step[side + "Steps"], path + "." + side + "."))
    return rows


def metric_value(key: str, value: Any) -> str:
    if not isinstance(value, (int, float)):
        return h(value)
    if key.endswith("Rate"):
        return f"{value * 100:.2f}%"
    return f"{value:.3g}" + (" ms" if key.endswith("Ms") else "")


def render_html(result: dict[str, Any], plan: dict[str, Any] | None) -> str:
    design = plan or result.get("testDesign", {})
    presentation = design.get("report", {})
    language = presentation.get("language", "en")
    text = labels(language, presentation.get("labels"))
    understanding = design.get("businessUnderstanding", {})
    cases = design.get("cases", [])
    records = {
        (row.get("caseId", case["id"]), row["id"]): row
        for case in result["cases"]
        for row in flatten([*case["steps"], *case["cleanup"]])
        if row.get("kind") not in {"poll-attempt", "branch-selection"}
    }
    # Keep platform errors and other unexpected result rows visible as well.
    expected = {
        (case["id"], path)
        for case in cases
        for path, _ in planned_steps([*case["steps"], *case["cleanup"]])
    }
    extras = [row for key, row in records.items() if key not in expected]
    anchors: dict[tuple[str, str], str] = {}
    for index, case in enumerate(cases):
        for j, (path, _) in enumerate(planned_steps([*case["steps"], *case["cleanup"]])):
            anchors[(case["id"], path)] = f"action-{index}-{j}"

    def record(case_id: str, path: str) -> dict[str, Any]:
        return records.get((case_id, path), {"status": "not_run"})

    arrow = (
        '<svg class="arrow" viewBox="0 0 24 28" aria-hidden="true">'
        '<path d="M12 0v22m-5-5 5 5 5-5"/></svg>'
    )

    def flow(steps: list[dict[str, Any]], case_id: str, prefix: str = "") -> str:
        rows = []
        for step in steps:
            path = prefix + step["id"]
            recorded = record(case_id, path)
            status = ("not_selected" if recorded.get("reason") == "not_selected"
                      else recorded["status"])
            node = (
                f'<a class="flow-node" data-reveal href="#{anchors[(case_id, path)]}">'
                f"<strong>{h(step_title(step, text))}</strong>{badge(status, text)}</a>"
            )
            if step.get("kind") == "branch":
                node += '<div class="condition">' + h(predicate_text(step["predicate"])) + "</div>"
                node += (
                    '<div class="fork">'
                    + "".join(
                        '<div><p class="path-label">'
                        + h(text[side])
                        + "</p>"
                        + flow(step[side + "Steps"], case_id, path + "." + side + ".")
                        + "</div>"
                        for side in ["then", "else"]
                    )
                    + "</div>"
                )
            elif step.get("kind") == "poll":
                node += '<div class="condition">' + h(predicate_text(step["until"])) + "</div>"
            rows.append("<li>" + arrow + node + "</li>")
        return '<ol class="flow-chain">' + "".join(rows) + "</ol>"

    diagrams, case_cards = [], []
    counts = {"passed": 0, "attention": 0, "not_run": 0}
    for index, case in enumerate(cases):
        case_id = case["id"]
        title = case.get("title") or case_id
        rows = planned_steps([*case["steps"], *case["cleanup"]])
        statuses = [record(case_id, path)["status"] for path, _ in rows]
        # Parent branch status is deliberately not used to invent passes for its children.
        status = next(
            (s for s in ["error", "failed", "inconclusive", "blocked", "not_run"] if s in statuses),
            "passed" if "passed" in statuses else "not_run",
        )
        group = (
            "passed" if status == "passed" else "not_run" if status == "not_run" else "attention"
        )
        counts[group] += 1
        diagrams.append(
            f'<details class="diagram"{" open" if index == 0 else ""}><summary>'
            f'<span><span class="eyebrow">{index + 1:02} / {h(text[case["kind"]])}</span>'
            f"<strong>{h(title)}</strong></span>{badge(status, text)}</summary>"
            f'<div class="diagram-body">{flow(case["steps"], case_id)}'
            f'<p class="cleanup-label">{h(text["cleanup"])}</p>'
            f"{flow(case['cleanup'], case_id)}</div></details>"
        )
        actions = []
        cleanup_ids = {step["id"] for step in case["cleanup"]}
        for path, step in rows:
            row = record(case_id, path)
            a = action(step)
            body = []
            if a.get("operationId"):
                body.append(
                    '<p class="technical">'
                    + h(a["operationId"])
                    + (" · " + h(a["identityRef"]) if a.get("identityRef") else "")
                    + "</p>"
                )
            if step.get("kind") == "poll":
                body.append('<p class="muted">' + h(text["threshold"]) + ': ' +
                            h(text["attempts"]) + f" ≤ {step['maxAttempts']}; " +
                            f"{step['intervalMs']} ms ×; ≤ {step['timeoutMs'] / 1000:g} s</p>")
            if step.get("kind") == "load":
                profile = step["loadProfile"]
                body.append('<p class="muted">' + h(text["threshold"]) + ': ' +
                            h(text["vus"]) + f" {profile['vus']}; " +
                            h(text["completedIterations"]) + f" {profile['iterations']}; " +
                            f"≤ {profile['maxDurationMs'] / 1000:g} s</p>")
            if row.get("reason"):
                body.append(
                    '<p class="notice">'
                    + h(text["reason"])
                    + ": "
                    + h(reason_text(row["reason"], text))
                    + "</p>"
                )
            response = row.get("response", {})
            if "status" in response:
                body.append(
                    "<p>" + h(text["request_status"]) + ": <b>" + h(response["status"]) + "</b></p>"
                )
            if "elapsedMs" in response:
                body.append(
                    "<p>"
                    + h(text["duration"])
                    + ": "
                    + metric_value("elapsedMs", response["elapsedMs"])
                    + "</p>"
                )
            if "attempts" in row:
                body.append("<p>" + h(text["attempts"]) + ": " + h(row["attempts"]) + "</p>")
            if "metrics" in row:
                metrics = row["metrics"]
                keys = [
                    "configuredVus",
                    "completedIterations",
                    "sampleCount",
                    "latencyP95Ms",
                    "latencyP99Ms",
                    "httpErrorRate",
                    "dataCheckFailureRate",
                ]
                body.append(
                    '<div class="metrics">'
                    + "".join(
                        "<div><span>"
                        + h(text.get("vus" if k == "configuredVus" else k, k))
                        + "</span><strong>"
                        + metric_value(k, metrics[k])
                        + "</strong></div>"
                        for k in keys
                        if k in metrics
                    )
                    + '</div><p class="muted">'
                    + h(text["latency_help"])
                    + "</p>"
                )
                body.append('<p class="muted">' + h(text["aggregate_only"]) + "</p>")
            checks = {c["id"]: c for c in row.get("assertions", [])}
            assertions = []
            for check in a.get("assertions", []):
                observed = checks.get(check["id"], {})
                check_status = observed.get(
                    "status",
                    "aggregate_only"
                    if "metrics" in row
                    else row["status"]
                    if row["status"] in {"not_run", "blocked", "skipped"}
                    else "not_evaluated",
                )
                # Expected/observed cells use runtime evidence, not unresolved templates.
                expected_value = (
                    text["exists"]
                    if check.get("operator") == "exists"
                    else text.get(check.get("operator", "eq"), "")
                    + " "
                    + json.dumps(observed["expected"], ensure_ascii=False)
                    if "expected" in observed
                    else "—"
                )
                assertions.append(
                    '<tr><th scope="row">'
                    + h(check_title(check, text))
                    + "</th><td>"
                    + badge(check_status, text)
                    + "</td><td>"
                    + cell_text(expected_value, text)
                    + "</td><td>"
                    + (
                        cell_text(json.dumps(observed["actual"], ensure_ascii=False), text)
                        if "actual" in observed
                        else "—"
                    )
                    + "</td></tr>"
                )
            for threshold in row.get("thresholds", []):
                key = threshold["metric"]
                assertions.append(
                    '<tr><th scope="row">'
                    + h(text.get(key, key))
                    + "</th><td>"
                    + badge(threshold["status"], text)
                    + "</td><td>"
                    + ("≤ " if threshold["operator"] == "lte" else "= ")
                    + metric_value(key, threshold["expected"])
                    + "</td><td>"
                    + metric_value(key, threshold["actual"])
                    + "</td></tr>"
                )
            if assertions:
                body.append(
                    '<div class="table-scroll"><table><thead><tr>'
                    + "".join(
                        "<th>" + h(text[key]) + "</th>"
                        for key in ["checks", "outcome", "expected", "actual"]
                    )
                    + "</tr></thead><tbody>"
                    + "".join(assertions)
                    + "</tbody></table></div>"
                )
            if len(row) > 1:
                body.append(
                    '<details class="technical-detail"><summary>'
                    + h(text["details"])
                    + "</summary>"
                    + json_block(row)
                    + "</details>"
                )
            label = (text["cleanup"] + " · " if path in cleanup_ids else "") + step_title(
                step, text
            )
            actions.append(
                f'<details class="action" id="{anchors[(case_id, path)]}">'
                f"<summary><span>{h(label)}</span>{badge(row['status'], text)}</summary>"
                '<div class="action-body">' + "".join(body) + "</div></details>"
            )
        case_cards.append(
            f'<article class="case" data-group="{group}"><header>'
            f'<span class="eyebrow">{index + 1:02} / {h(text[case["kind"]])}</span>'
            f"<h3>{h(title)}</h3>{badge(status, text)}</header>" + "".join(actions) + "</article>"
        )

    def section_items(key: str, title_key: str) -> str:
        entries = understanding.get(key, [])
        if not entries:
            return ""
        return (
            '<div class="scope-block"><h3>'
            + h(text[title_key])
            + "</h3><ul>"
            + "".join(
                "<li>"
                + h(entry.get("description", ""))
                + (
                    ' <span class="muted">' + h(entry["impact"]) + "</span>"
                    if entry.get("impact")
                    else ""
                )
                + "</li>"
                for entry in entries
            )
            + "</ul></div>"
        )

    gaps = design.get("coverage", {}).get("uncovered", [])
    scope = "".join(
        section_items(key, title)
        for key, title in [
            ("rules", "rules"),
            ("assumptions", "unknowns"),
            ("conflicts", "unknowns"),
            ("unknowns", "unknowns"),
        ]
    )
    if gaps:
        scope += (
            '<div class="scope-block"><h3>'
            + h(text["uncovered"])
            + "</h3><ul>"
            + "".join(
                "<li>" + h(item["itemId"]) + ": " + h(item["reason"]) + "</li>" for item in gaps
            )
            + "</ul></div>"
        )
    states = "".join(
        '<span class="state">'
        + h(state["resourceId"])
        + ": "
        + h(" → ".join(state["values"]))
        + "</span>"
        for state in understanding.get("states", [])
    )
    flows = "".join(
        "<p>" + h(item["description"]) + "</p>" for item in understanding.get("flows", [])
    )
    dependencies = "".join(
        "<li>"
        + h(item["purpose"])
        + "<details><summary>"
        + h(text["details"])
        + "</summary>"
        + json_block(item)
        + "</details></li>"
        for item in understanding.get("dataDependencies", [])
    )
    finished = datetime.fromtimestamp(result.get("finishedAt", 0), UTC).isoformat(
        timespec="seconds"
    )
    verdict = result.get("verdict", "inconclusive")
    residue = (
        '<p>' + h(text["unresolved"]) + '</p><details><summary>' +
        h(text["details"]) + '</summary>' + json_block(result["residue"]) + '</details>'
        if result.get("residue")
        else "<p>" + h(text["clean"]) + "</p>"
    )
    resource = (
        json_block(result["resourceMetrics"])
        if result.get("resourceMetrics")
        else "<p>" + h(text["not_monitored"]) + "</p>"
    )
    warning = (
        (
            '<div class="warning">'
            + h(text["context_issue"])
            + json_block(result["reportIssues"])
            + "</div>"
        )
        if result.get("reportIssues")
        else ""
    )
    if extras:
        warning += '<div class="warning">' + h(text["issues"]) + json_block(extras) + "</div>"
    if result.get("cleanupFrom"):
        warning += '<p class="warning">' + h(text["recovery"]) + "</p>"
    css = Path(__file__).with_name("report.css").read_text()
    script = Path(__file__).with_name("report.js").read_text()
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return f'''<!doctype html>
<html lang="{h(language)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{script_hash}';
base-uri 'none'; form-action 'none'">
<title>{h(design.get("name", text["report"]))} · horus</title>
<style>{css}</style>
</head>
<body>
<div class="report-shell">
<nav><a class="brand" href="#top">horus<span> / {h(text["report"])}</span></a><div>
<a href="#logic">{h(text["logic"])}</a><a href="#results">{h(text["results"])}</a>
<button id="print">{h(text["print"])}</button></div></nav>
<main id="top">
<header class="hero"><div class="eyebrow">HORUS / {h(text["run"])} · {h(result["runId"][-8:])}</div>
<div class="hero-title"><h1>{h(design.get("name", text["report"]))}</h1>{badge(verdict, text)}</div>
<p class="lead">{h(understanding.get("summary", ""))}</p>
<p class="muted">{h(text["coverage_note"])}</p>
<div class="meta"><span>{h(text["target"])}<strong>{h(result.get("target", "—"))}</strong></span>
<span>{h(text["environment"])}<strong>{h(result.get("environment", "—"))}</strong></span>
<span>{h(text["finished"])}<strong>{h(finished)}</strong></span></div>
<div class="scoreboard"><div><strong>{len(cases):02}</strong><span>{h(text["all"])}</span></div>
<div><strong>{counts["passed"]:02}</strong><span>{h(text["passed"])}</span></div>
<div><strong>{counts["attention"]:02}</strong><span>{h(text["issues"])}</span></div>
<div><strong>{counts["not_run"]:02}</strong><span>{h(text["not_run"])}</span></div></div>
{warning}</header>
<section id="logic"><div class="section-heading"><span>01</span><h2>{h(text["logic"])}</h2></div>
<p class="muted">{h(text["flow_note"])}</p>{flows}<div>{states}</div>
{'<ul class="dependencies">' + dependencies + "</ul>" if dependencies else ""}
{"".join(diagrams)}</section>
<section id="results"><div class="section-heading"><span>02</span><h2>{
        h(text["results"])
    }</h2></div>
<div class="toolbar"><div class="filters" role="group" aria-label="{h(text["results"])}">
<button data-filter="all" aria-pressed="true">{h(text["all"])}</button>
<button data-filter="attention" aria-pressed="false">{h(text["issues"])}</button>
<button data-filter="passed" aria-pressed="false">{h(text["passed"])}</button></div>
<input id="search" type="search" aria-label="{h(text["search"])}" placeholder="{h(text["search"])}">
<button id="expand">{h(text["expand"])}</button><button id="collapse">{
        h(text["collapse"])
    }</button></div>
<div id="case-list">{"".join(case_cards)}</div><p id="empty" hidden role="status">{
        h(text["empty"])
    }</p>
</section>
<section id="scope"><div class="section-heading"><span>03</span><h2>{h(text["scope"])}</h2></div>
<div class="scope-grid">{scope}<div class="scope-block"><h3>{h(text["residue"])}</h3>{residue}</div>
<div class="scope-block"><h3>{h(text["monitoring"])}</h3>{resource}</div></div></section>
<footer><h2>{h(text["files"])}</h2><p><a href="plan.md">{h(text["approved_plan"])}</a>
<a href="plan.json">plan.json</a><a href="report.json">{h(text["evidence"])} · JSON</a>
<a href="report.md">Markdown</a><a href="junit.xml">JUnit</a></p>
<details><summary>{h(text["details"])}</summary>{
        json_block(
            {
                k: result[k]
                for k in ["runId", "planRef", "reviewRef", "scopeHash", "createdAt", "cleanupFrom"]
                if k in result
            }
        )
    }</details>
</footer></main></div>
<script>{script}</script>
</body>
</html>
'''
