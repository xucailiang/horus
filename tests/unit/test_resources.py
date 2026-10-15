import json
import uuid

import pytest
from pydantic import ValidationError

from horus.core.models import ResourceMetrics
from horus.core.reports import write_report
from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write
from tests.support import FIXTURES, reviewed_regression_plan


def test_resource_metrics_validate_actual_samples_and_do_not_turn_failures_into_passes(tmp_path):
    service = Service(tmp_path, read_roots=[FIXTURES])
    project, plan_ref, review_ref = reviewed_regression_plan(service, "http://127.0.0.1:1")
    plan = service.project(project).get("plans", plan_ref)
    plan["resourceMonitoring"] = {
        "targetLabel": "test-host",
        "commands": ["read /proc"],
        "maxDurationSeconds": 30,
    }
    plan_ref = service.save_plan(project, plan)["planRef"]
    run_id = "run_" + uuid.uuid4().hex
    root = service.project(project).root / "runs" / run_id
    intent = {"projectRef": project, "runId": run_id, "planRef": plan_ref, "reviewRef": review_ref}
    atomic_write(root / "intent.json", intent)
    write_report(
        service,
        root,
        {
            **intent,
            "status": "completed",
            "cases": [{"id": "case", "steps": [{"id": "read", "status": "failed"}], "cleanup": []}],
        },
    )
    metrics = ResourceMetrics.model_validate(
        {
            "targetLabel": "test-host",
            "status": "collected",
            "samples": [
                {
                    "at": 1000.0,
                    "cpuPercent": 25.0,
                    "memoryUsedBytes": 100,
                    "memoryTotalBytes": 1000,
                },
                {
                    "at": 1002.0,
                    "cpuPercent": 75.0,
                    "memoryUsedBytes": 200,
                    "memoryTotalBytes": 1000,
                },
            ],
        }
    )
    runs = Runs(service)
    result = runs.attach_metrics(project, run_id, metrics)
    assert result["verdict"] == "failed"
    assert runs.attach_metrics(project, run_id, metrics)["artifactRef"] == result["artifactRef"]
    report = json.loads((root / "report.json").read_bytes())
    assert report["resourceMetrics"]["samples"][1]["cpuPercent"] == 75.0
    text = (root / "report.md").read_text()
    assert "test-host" in text and "75.00" in text
    metrics.targetLabel = "different-host"
    with pytest.raises(HorusError, match="monitoring_scope_mismatch"):
        runs.attach_metrics(project, run_id, metrics)


def test_empty_or_impossible_resource_samples_cannot_claim_success():
    with pytest.raises(ValidationError):
        ResourceMetrics.model_validate(
            {"targetLabel": "host", "status": "collected", "samples": []}
        )
    with pytest.raises(ValidationError):
        ResourceMetrics.model_validate(
            {
                "targetLabel": "host",
                "status": "collected",
                "samples": [{"at": 1.0, "cpuPercent": 120.0}],
            }
        )
