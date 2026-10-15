"""Readable material derived from the same executable plan that the user approves."""

import json
from typing import Any, cast

from horus.core.models import Plan, ProjectConfig
from horus.core.report_text import labels


def action(step: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], step.get("requestStep", step))


def step_title(step: dict[str, Any], text: dict[str, str]) -> str:
    return (
        step.get("title")
        or action(step).get("title")
        or (text.get(step.get("kind", ""), "") or step.get("operationId") or step["id"])
    )


def check_title(check: dict[str, Any], text: dict[str, str]) -> str:
    if check.get("description"):
        return str(check["description"])
    if check.get("status") is not None:
        return text["status_check"]
    if check.get("jsonSchema") is not None:
        return text["schema_check"]
    if check.get("elapsedMs"):
        return text["time_check"]
    if check.get("header"):
        return text["header_check"] + ": " + str(check["header"])
    return text["value_check"] + ": " + str(check.get("jsonPointer", ""))


def predicate_text(predicate: dict[str, Any]) -> str:
    kind = predicate["kind"]
    if kind in {"all", "any"}:
        return (" AND " if kind == "all" else " OR ").join(
            "(" + predicate_text(p) + ")" for p in predicate["predicates"]
        )
    if kind == "not":
        return "NOT (" + predicate_text(predicate["predicate"]) + ")"

    def operand(value: dict[str, Any]) -> str:
        if value["kind"] == "literal":
            return json.dumps(value["value"], ensure_ascii=False)
        if value["kind"] == "variable":
            return str(value["name"])
        return str(value.get("jsonPointer") or value.get("header") or "HTTP status")

    operator = {"eq": "=", "ne": "≠", "gte": "≥", "lte": "≤"}.get(
        predicate["operator"], predicate["operator"]
    )
    return f"{operand(predicate['left'])} {operator} {operand(predicate['right'])}"


def plan_document(
    plan: Plan, config: ProjectConfig, environment: str, validation: dict[str, Any]
) -> str:
    data = plan.model_dump(mode="json")
    text = labels(plan.report.language, plan.report.labels)
    limits = config.policy.limits
    lines = [
        f"# {text['plan']} · {markdown_text(plan.name)}",
        "",
        markdown_text(plan.businessUnderstanding.summary),
        "",
        f"- {text['target']}: {config.environments[environment].baseUrl}",
        f"- {text['environment']}: {environment}",
        f"- {text['budget']}: {validation['maxRequests']}",
        f"- {text['run_limit']}: {limits.maxRunDurationMs / 1000:g} s",
        f"- {text['cleanup_limit']}: {limits.maxCleanupDurationMs / 1000:g} s",
        "",
        text["limit_note"],
        "",
        f"## {text['logic']}",
        "",
        text["flow_note"],
        "",
    ]

    def describe(steps: list[dict[str, Any]], indent: str = "") -> None:
        for step in steps:
            a = action(step)
            lines.append(indent + "- " + markdown_text(step_title(step, text)))
            if step.get("kind") == "branch":
                lines.append(indent + "  - " + markdown_text(predicate_text(step["predicate"])))
                for side in ["then", "else"]:
                    lines.append(indent + "  - " + text[side] + ":")
                    describe(step[side + "Steps"], indent + "    ")
                continue
            if a.get("identityRef"):
                lines.append(indent + f"  - {text['identity']}: {markdown_text(a['identityRef'])}")
            for check in a.get("assertions", []):
                if check.get("jsonSchema") is not None:
                    detail = ""
                elif check.get("operator") == "exists":
                    detail = text["exists"]
                else:
                    expected = check.get("status") if check.get("status") is not None else (
                        check.get("expected"))
                    detail = text.get(check.get("operator", "eq"), "") + " " + json.dumps(
                        expected, ensure_ascii=False)
                lines.append(indent + "  - " + markdown_text(check_title(check, text)) +
                             (": " + markdown_text(detail) if detail else ""))
            if step.get("kind") == "poll":
                lines.append(
                    indent + f"  - {text['poll']}: {markdown_text(predicate_text(step['until']))}; "
                    f"≤ {step['maxAttempts']} × {step['intervalMs']} ms; "
                    f"≤ {step['timeoutMs']} ms"
                )
            if step.get("kind") == "load":
                profile = step["loadProfile"]
                lines.extend(
                    [
                        indent + f"  - {text['vus']}: {profile['vus']}; "
                        f"{text['completedIterations']}: {profile['iterations']}; "
                        f"≤ {profile['maxDurationMs'] / 1000:g} s"
                    ]
                )
                for key, threshold in step["thresholds"].items():
                    if threshold:
                        lines.append(
                            indent
                            + "  - "
                            + text.get(key, key)
                            + ": "
                            + ("≤ " if "lte" in threshold else "= ")
                            + str(next(iter(threshold.values())))
                            + (" ms" if key.endswith("Ms") else "")
                        )

    if plan.businessUnderstanding.flows:
        lines.extend("- " + markdown_text(flow.description)
                     for flow in plan.businessUnderstanding.flows)
        lines.append("")
    for case in data["cases"]:
        lines.extend(["### " + markdown_text(case.get("title") or case["id"]) +
                      " · " + text[case["kind"]], ""])
        lines.extend([mermaid_diagram([case], text), ""])
        describe(case["steps"])
        lines.extend(["", text["cleanup"] + ":"])
        describe(case["cleanup"])
        lines.append("")
    for key, title in [
        ("rules", "rules"),
        ("assumptions", "unknowns"),
        ("conflicts", "unknowns"),
        ("unknowns", "unknowns"),
    ]:
        entries = data["businessUnderstanding"][key]
        if entries:
            lines.extend(["## " + text[title], ""])
            for entry in entries:
                lines.append(
                    "- "
                    + markdown_text(entry["description"])
                    + (" · " + markdown_text(entry["impact"]) if entry.get("impact") else "")
                )
            lines.append("")
    lines.extend(["## " + text["uncovered"], ""])
    lines.extend(
        "- " + markdown_text(item["itemId"]) + ": " + markdown_text(item["reason"])
        for item in data["coverage"]["uncovered"]
    )
    lines.extend(
        [
            "",
            "## " + text["monitoring"],
            "",
            json.dumps(data["resourceMonitoring"], ensure_ascii=False)
            if plan.resourceMonitoring
            else text["not_monitored"],
            "",
        ]
    )
    # Exact requests, variables, evidence and policies remain available alongside this text.
    return "\n".join(lines)


def markdown_text(value: Any) -> str:
    """Keep external content as text, including inside tables and headings."""
    from html import escape

    encoded = escape(str(value), quote=False)
    for char in '\\`*_{}[]()|!':
        encoded = encoded.replace(char, '\\' + char)
    return encoded.replace('\r', ' ').replace('\n', ' ')


def mermaid_diagram(
    cases: list[dict[str, Any]], text: dict[str, str], *, brief: bool = False,
) -> str:
    """A fixed graph grammar; plan strings can only enter quoted node labels."""
    lines = ['flowchart TD']
    index = 0

    def label(value: str) -> str:
        escaped = {'"': '#34;', '#': '#35;', '`': '#96;', '&': '#38;',
                   '<': '#60;', '>': '#62;', '\\': '#92;'}
        return ''.join(escaped.get(char, char) for char in
                       value.replace('\n', ' ').replace('\r', ' '))

    def node(title: str, *, decision: bool = False) -> str:
        nonlocal index
        ref = 'n' + str(index)
        index += 1
        shape = '{"' + label(title) + '"}' if decision else '["' + label(title) + '"]'
        lines.append('    ' + ref + shape)
        return ref

    def edge(left: str, right: str, title: str = '', *, cleanup: bool = False) -> None:
        arrow = '-.->' if cleanup else '-->'
        lines.append(f'    {left} {arrow}' + (f'|"{label(title)}"|' if title else '') +
                     ' ' + right)

    def chain(steps: list[dict[str, Any]],
              incoming: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
        tails = incoming or []
        for step in steps:
            title = step_title(step, text)
            if step.get("kind") == "branch" and (not brief or not step.get("title")):
                title += " · " + predicate_text(step["predicate"])
            elif step.get("kind") == "poll" and not brief:
                title += (" · " + predicate_text(step["until"]) +
                          f" · ≤ {step['maxAttempts']} × {step['intervalMs']} ms"
                          f" · ≤ {step['timeoutMs'] / 1000:g} s")
            elif step.get("kind") == "load":
                profile = step["loadProfile"]
                title += (f" · {text['vus']}: {profile['vus']}"
                          f" · {text['completedIterations']}: {profile['iterations']}")
            current = node(title, decision=step.get("kind") == "branch")
            for previous, caption in tails:
                edge(previous, current, caption)
            if step.get("kind") == "branch":
                tails = [tail for side in ["then", "else"]
                         for tail in chain(step[side + "Steps"], [(current, text[side])])]
            else:
                tails = [(current, "")]
        return tails

    for case_index, case in enumerate(cases):
        if not brief:
            lines.append(f'    subgraph c{case_index}["{label(case.get("title") or case["id"])}"]')
        tails = chain(case["steps"])
        if case["cleanup"]:
            cleanup_start = node(text["cleanup"])
            for tail, caption in tails:
                edge(tail, cleanup_start, caption, cleanup=True)
            if not brief:
                chain(case["cleanup"], [(cleanup_start, "")])
        if not brief:
            lines.append("    end")
    return '```mermaid\n' + '\n'.join(lines) + '\n```'
