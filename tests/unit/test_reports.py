import json
from copy import deepcopy

import pytest

from horus.core.reports import interrupted_report, write_report
from horus.core.store import atomic_write
from tests.unit.test_execution_safety import prepared_runner


def test_report_keeps_reviewed_business_context_and_distinguishes_unexecuted_checks(
    tmp_path, monkeypatch
):
    runner = prepared_runner(tmp_path, monkeypatch)
    plan = runner.plan.model_dump(mode="json")
    plan["businessUnderstanding"]["unknowns"] = [
        {
            "id": "list-coverage",
            "description": "List consistency has not been checked.",
            "impact": "Detail success does not cover list behavior.",
            "evidenceRefs": [],
        }
    ]
    plan["coverage"]["uncovered"] = [{"itemId": "list-coverage", "reason": "Follow-up required."}]
    plan_ref = runner.service.save_plan(runner.project_ref, plan)["planRef"]
    result = write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "planRef": plan_ref,
            "status": "completed",
            "cases": [
                {
                    "id": "lifecycle",
                    "cleanup": [],
                    "steps": [
                        {
                            "id": "read",
                            "status": "failed",
                            "assertions": [
                                {
                                    "id": "value",
                                    "status": "failed",
                                    "actual": 43,
                                    "expected": 42,
                                    "evidenceRefs": plan["businessUnderstanding"]["rules"][0][
                                        "evidenceRefs"
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    report = json.loads((runner.root / "report.json").read_bytes())
    assert report["target"] == "http://127.0.0.1:1"
    assert report["testDesign"]["businessUnderstanding"] == plan["businessUnderstanding"]
    assert report["testDesign"]["coverage"] == plan["coverage"]
    assert report["testDesign"]["sourceRefs"] == plan["sourceRefs"]
    coverage = report["executionCoverage"]["assertions"]
    assert coverage["lifecycle.read.value"]["status"] == "failed"
    assert (
        coverage["lifecycle.read.value"]["evidenceRefs"]
        == (plan["businessUnderstanding"]["rules"][0]["evidenceRefs"])
    )
    assert coverage["load.read.value"]["status"] == "not_run"
    text = (runner.root / "report.md").read_text()
    assert "http://127.0.0.1:1" in text
    assert "Reads preserve the written value" in (runner.root / "plan.md").read_text()
    details = (runner.root / "details.md").read_text()
    assert "lifecycle.read.value" in details and "load.read.value" in details
    assert "Not passed" in details and "Not run" in details
    assert "List consistency has not been checked." in text and "Follow-up required." in text
    assert "testDesign" not in result and "executionCoverage" not in result
    assert result["verdict"] == "failed"


@pytest.mark.parametrize("failed", [True, False])
def test_load_coverage_reports_aggregate_evidence_without_inventing_per_assertion_results(
    tmp_path, monkeypatch, failed
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
                    "id": "load",
                    "cleanup": [],
                    "steps": [
                        {
                            "id": "read",
                            "status": "failed" if failed else "passed",
                            "metrics": {
                                "sampleCount": 100,
                                "dataCheckFailureRate": 0.1 if failed else 0,
                            },
                        }
                    ],
                }
            ],
        },
    )
    report = json.loads((runner.root / "report.json").read_bytes())
    assertion = report["executionCoverage"]["assertions"]["load.read.value"]
    assert assertion["status"] == "aggregate_only"
    assert assertion["stepStatus"] == ("failed" if failed else "passed")
    assert report["executionCoverage"]["assertions"]["lifecycle.read.value"]["status"] == "not_run"


def test_interrupted_report_maps_events_to_original_cases_and_keeps_unstarted_work_unexecuted(
    tmp_path, monkeypatch
):
    runner = prepared_runner(tmp_path, monkeypatch)
    event = {
        "caseId": "lifecycle",
        "id": "read",
        "status": "passed",
        "assertions": [
            {
                "id": "value",
                "status": "passed",
                "actual": 42,
                "expected": 42,
            }
        ],
    }
    (runner.root / "events.jsonl").write_text(json.dumps(event) + "\n")
    result = interrupted_report(runner.service, runner.project_ref, runner.run_id)
    report = json.loads((runner.root / "report.json").read_bytes())
    assert report["executionCoverage"]["assertions"]["lifecycle.read.value"]["status"] == "passed"
    assert report["executionCoverage"]["assertions"]["load.read.value"]["status"] == "not_run"
    assert result["verdict"] == "inconclusive"


def test_unavailable_plan_cannot_suppress_a_failure_report(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    path = runner.store.path("plans", runner.intent["planRef"])
    atomic_write(path, {"tampered": True})
    result = write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "status": "completed",
            "cases": [
                {
                    "id": "platform",
                    "cleanup": [],
                    "steps": [
                        {
                            "id": "worker",
                            "status": "error",
                            "reason": "integrity_error",
                        }
                    ],
                }
            ],
        },
    )
    assert result["verdict"] == "error"
    assert result["reportIssues"][0]["code"] == "integrity_error"
    assert "integrity_error" in (runner.root / "details.md").read_text()


def test_step_and_assertion_references_cannot_overwrite_each_other(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    plan = runner.plan.model_dump(mode="json")
    read = deepcopy(plan["cases"][0]["steps"][2])
    read["id"] = "read.value"
    plan["cases"][0]["steps"].append(read)
    plan_ref = runner.service.save_plan(runner.project_ref, plan)["planRef"]
    write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "planRef": plan_ref,
            "status": "completed",
            "cases": [
                {
                    "id": "lifecycle",
                    "cleanup": [],
                    "steps": [
                        {
                            "id": "read",
                            "status": "passed",
                            "assertions": [
                                {"id": "value", "status": "passed"},
                            ],
                        },
                        {"id": "read.value", "status": "failed"},
                    ],
                }
            ],
        },
    )
    report = json.loads((runner.root / "report.json").read_bytes())
    ref = "lifecycle.read.value"
    assert report["executionCoverage"]["steps"][ref]["status"] == "failed"
    assert report["executionCoverage"]["assertions"][ref]["status"] == "passed"
    assert ref in (runner.root / "details.md").read_text()


def test_mcp_artifact_delivers_bounded_failure_evidence_and_sampling_limits(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    response = {"status": 200, "body": "evidence-prefix" + "x" * 30000 + "unretained-tail"}
    result = write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "status": "completed",
            "cases": [
                {
                    "id": "load",
                    "cleanup": [],
                    "steps": [
                        {
                            "id": "read",
                            "status": "failed",
                            "request": {"pathParams": {"id": "sample-id"}},
                            "response": response,
                            "failureSamples": [
                                {
                                    "failedAssertions": ["value"],
                                    "body": "sample-debug-marker",
                                    "vu": 1,
                                }
                            ],
                            "samplingLimitPerVu": 5,
                        }
                    ],
                }
            ],
        },
    )
    content = []
    offset = 0
    while offset is not None:
        page = runner.service.get_artifact(runner.project_ref, result["detailsArtifactRef"], offset)
        content.append(page["content"])
        offset = page["nextOffset"]
    text = "\n".join(content)
    assert "sample-debug-marker" in text and '"failedAssertions"' in text
    assert '"retainedFailureSamples": 1' in text and '"samplingLimitPerVu": 5' in text
    assert '"request"' in text and "sample-id" in text
    assert "evidence-prefix" in text and '"truncated": true' in text
    assert "unretained-tail" not in text and len(text) < 40000
    report = json.loads((runner.root / "report.json").read_bytes())
    assert report["cases"][0]["steps"][0]["response"] == response
