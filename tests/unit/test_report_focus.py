import json

from horus.core.reports import write_report
from tests.unit.test_execution_safety import prepared_runner


def test_success_report_summarizes_business_cases_and_keeps_evidence_separate(
    tmp_path, monkeypatch
):
    runner = prepared_runner(tmp_path, monkeypatch)
    from horus.core.steps import request_action

    cases = []
    for case in runner.plan.cases:
        rows = []
        for step in case.steps:
            a = request_action(step)
            row = {
                "id": step.id,
                "status": "passed",
                "assertions": [
                    {"id": check.id, "status": "passed", "actual": 42, "expected": 42}
                    for check in a.assertions
                ],
                "response": {"status": 200, "body": {"id": "resource-id-only-in-details"}},
            }
            if step.kind == "load":
                row["metrics"] = {
                    "configuredVus": 5,
                    "completedIterations": 100,
                    "sampleCount": 100,
                    "latencyP95Ms": 0.759,
                    "httpErrorRate": 0,
                    "dataCheckFailureRate": 0,
                }
                row["thresholds"] = [
                    {
                        "metric": "latencyP95Ms",
                        "operator": "lte",
                        "expected": 1000,
                        "actual": 0.759,
                        "status": "passed",
                    }
                ]
            rows.append(row)
        cases.append(
            {
                "id": case.id,
                "steps": rows,
                "cleanup": [{"id": step.id, "status": "passed"} for step in case.cleanup],
            }
        )
    summary = write_report(
        runner.service, runner.root, {**runner.intent, "status": "completed", "cases": cases}
    )
    main = (runner.root / "report.md").read_text()
    details = (runner.root / "details.md").read_text()
    assert len(main.splitlines()) <= 85
    assert main.index("0.759") < main.index("```mermaid")
    assert main.count("```mermaid") == 1
    assert "<pre>" not in main and "<details>" not in main
    assert "resource-id-only-in-details" not in main
    assert "resource-id-only-in-details" in details
    assert "[details.md](details.md)" in main
    assert summary["detailsArtifactRef"]
    artifact = runner.service.project(runner.project_ref).get(
        "artifacts", summary["detailsArtifactRef"]
    )
    assert artifact["content"] == details
    assert json.loads((runner.root / "report.json").read_text())["cases"] == cases


def test_failures_and_unknown_cleanup_are_visible_before_successes_and_diagrams(
    tmp_path, monkeypatch
):
    runner = prepared_runner(tmp_path, monkeypatch)
    write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "status": "completed",
            "cases": [
                {
                    "id": "lifecycle",
                    "steps": [
                        {
                            "id": "read",
                            "status": "failed",
                            "assertions": [
                                {"id": "value", "status": "failed", "actual": 43, "expected": 42}
                            ],
                        }
                    ],
                    "cleanup": [
                        {"id": "delete", "status": "blocked", "reason": "missing_cleanup_inputs"}
                    ],
                }
            ],
            "residue": [{"reason": "original_write_outcome_unknown"}],
        },
    )
    main = (runner.root / "report.md").read_text()
    assert main.index("43") < main.index("```mermaid")
    assert main.index("42") < main.index("```mermaid")
    assert "unknown" in main.lower() or "unresolved" in main.lower()
    assert "not_run" not in main  # Human labels rather than machine status codes.


def test_cleanup_recovery_does_not_claim_business_replay_or_missing_cleanup(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "status": "completed",
            "cleanupFrom": "original-run",
            "cases": [
                {
                    "id": c.id,
                    "steps": [
                        {
                            "id": "prior-cleanup",
                            "status": "passed",
                            "reason": "cleanup_previously_verified",
                        }
                    ],
                    "cleanup": [],
                }
                for c in runner.plan.cases
            ],
        },
    )
    main = (runner.root / "report.md").read_text()
    assert "Cleanup recovery only" in main
    assert "Cleanup · lifecycle" in main
    assert "Some outcomes remain unresolved" not in main
    assert "0/6" not in main
