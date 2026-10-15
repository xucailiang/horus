import asyncio
import json
import os
import signal
import threading
import uuid

import pytest

from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write, file_lock
from tests.fixtures.reference_api import ReferenceServer
from tests.support import FIXTURES, reviewed_regression_plan


@pytest.mark.parametrize("mode,verdict", [("normal", "passed"), ("data-error", "failed")])
async def test_detached_worker_cleans_reports_and_deduplicates_start(
    tmp_path, monkeypatch, mode, verdict
):
    password = "private-" + uuid.uuid4().hex
    monkeypatch.setenv("HORUS_USERNAME", "fixture-user")
    monkeypatch.setenv("HORUS_PASSWORD", password)
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    server = ReferenceServer(0, mode=mode, username="fixture-user", password=password)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = Service(tmp_path, read_roots=[FIXTURES])
    project, plan_ref, review_ref = reviewed_regression_plan(
        service, f"http://127.0.0.1:{server.server_port}"
    )
    runs = Runs(service)
    try:
        with pytest.raises(HorusError, match="review_required"):
            runs.start(project, plan_ref, None, "test", "unapproved")
        assert server.requests == []
        first = await asyncio.to_thread(runs.start, project, plan_ref, review_ref, "test", "same")
        second = await asyncio.to_thread(runs.start, project, plan_ref, review_ref, "test", "same")
        assert first["runId"] == second["runId"]
        with pytest.raises(HorusError, match="request_conflict"):
            runs.start(project, plan_ref, review_ref, "different-env", "same")
        result = await runs.wait(project, first["runId"], wait_ms=30000)
        assert result["status"] == "completed"
        assert result["verdict"] == verdict
        assert not server.records
        assert not server.tokens
        assert sum(method == "POST" and path == "/records" for method, path in server.requests) == 2
        content = json.dumps(result)
        assert password not in content
        run_root = runs.root(project, first["runId"])
        for path in run_root.rglob("*"):
            if path.is_file():
                assert password not in path.read_text(errors="replace")
        assert (run_root / "report.md").exists()
        assert (run_root / "junit.xml").exists()
        assert "artifactRef" in result
        report = json.loads((run_root / "report.json").read_bytes())
        assert (run_root / "report.html").is_file()
        assert (run_root / "plan.md").is_file()
        assert json.loads((run_root / "plan.json").read_bytes()) == service.project(
            project).get("plans", plan_ref)
        assert report["testDirectory"] == str(run_root)
        assert report["reportPath"] == str(run_root / "report.html")
        assert "htmlArtifactRef" in report
        assert report["target"] == f"http://127.0.0.1:{server.server_port}"
        assert report["testDesign"]["coverage"]["rules"][0]["ruleId"] == "value"
        coverage = report["executionCoverage"]["assertions"]
        assert coverage["lifecycle.read.value"]["status"] == verdict
        assert coverage["load.read.value"]["status"] == "aggregate_only"
        assert "Reads preserve the written value" in (run_root / "report.md").read_text()
        assert "testDesign" not in result and "executionCoverage" not in result
    finally:
        server.shutdown()
        server.server_close()


async def test_cancelled_run_still_cleans_and_produces_report(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_USERNAME", "fixture-user")
    monkeypatch.setenv("HORUS_PASSWORD", "fixture-private")
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    server = ReferenceServer(0, mode="delay", username="fixture-user", password="fixture-private")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = Service(tmp_path, read_roots=[FIXTURES])
    refs = reviewed_regression_plan(service, f"http://127.0.0.1:{server.server_port}")
    runs = Runs(service)
    try:
        run = await asyncio.to_thread(runs.start, *refs, "test", "cancel-me")
        for _ in range(100):
            if server.records:
                break
            await asyncio.sleep(0.05)
        assert server.records
        runs.cancel(refs[0], run["runId"])
        result = await runs.wait(refs[0], run["runId"], wait_ms=10000)
        assert result["status"] == "cancelled"
        assert result["verdict"] != "passed"
        assert "artifactRef" in result
        assert not server.records
        assert not server.tokens
    finally:
        server.shutdown()
        server.server_close()


async def test_queued_run_can_be_cancelled_without_waiting_for_project_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_USERNAME", "fixture-user")
    monkeypatch.setenv("HORUS_PASSWORD", "fixture-private")
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    service = Service(tmp_path, read_roots=[FIXTURES])
    refs = reviewed_regression_plan(service, "http://127.0.0.1:1")
    runs = Runs(service)
    with file_lock(service.project(refs[0]).root / ".execution.lock"):
        run = await asyncio.to_thread(runs.start, *refs, "test", "queued-cancel")
        assert run["status"] == "queued"
        runs.cancel(refs[0], run["runId"])
        result = await runs.wait(refs[0], run["runId"], wait_ms=1000)
        assert result["status"] == "cancelled"
        assert result["verdict"] == "inconclusive"


@pytest.mark.parametrize("allow_auth", [True, False])
async def test_interrupted_run_repair_only_reauthenticates_and_cleans_known_resources(
    tmp_path, monkeypatch, allow_auth
):
    monkeypatch.setenv("HORUS_USERNAME", "fixture-user")
    monkeypatch.setenv("HORUS_PASSWORD", "fixture-private")
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    server = ReferenceServer(0, mode="delay", username="fixture-user", password="fixture-private")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = Service(tmp_path, read_roots=[FIXTURES])
    refs = reviewed_regression_plan(service, f"http://127.0.0.1:{server.server_port}")
    if not allow_auth:
        project, plan, review = refs
        store = service.project(project)
        config = store.get("configs", store.get("reviews", review)["configRef"])
        config["policy"]["cleanupAllow"].remove("login")
        config_ref = service.stage_config(project, config)["configRef"]
        bundle = service.prepare_review(project, plan, config_ref, "test")
        service.get_artifact(project, bundle["artifactRef"])
        decision = service.record_decision(
            project,
            bundle["reviewBundleRef"],
            plan,
            bundle["scopeHash"],
            "approved",
            "test-only",
            "repair scope regression",
        )
        refs = project, plan, decision["reviewRef"]
    runs = Runs(service)
    try:
        original = await asyncio.to_thread(runs.start, *refs, "test", "interrupt-for-repair")
        root = runs.root(refs[0], original["runId"])
        for _ in range(200):
            if (root / "ledger.json").exists():
                ledger = json.loads((root / "ledger.json").read_bytes())
                if any(e["caseId"] == "load" and e["stepId"] == "read" for e in ledger):
                    break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("load did not start")
        pid = json.loads((root / "process.json").read_bytes())["pid"]
        os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.3)
        atomic_write(root / "heartbeat.json", {"pid": pid, "at": 0})
        assert runs.get(refs[0], original["runId"])["status"] == "interrupted"
        assert server.records
        created = sum(method == "POST" and path == "/records" for method, path in server.requests)
        logins = sum(path == "/auth/token" for _, path in server.requests)
        monkeypatch.setenv("HORUS_K6_BIN", str(tmp_path / "k6-is-not-required-for-cleanup"))
        repair = await asyncio.to_thread(runs.cleanup, refs[0], original["runId"], "repair-once")
        same = await asyncio.to_thread(runs.cleanup, refs[0], original["runId"], "repair-once")
        assert repair["runId"] == same["runId"]
        result = await runs.wait(refs[0], repair["runId"], wait_ms=10000)
        assert result["status"] == "completed"
        assert result["verdict"] == "inconclusive"  # The lost original session is not recoverable.
        assert bool(server.records) is not allow_auth
        assert len(server.tokens) == 1
        assert sum(path == "/auth/token" for _, path in server.requests) == logins + int(allow_auth)
        assert (
            sum(method == "POST" and path == "/records" for method, path in server.requests)
            == created
        )
        report = json.loads((runs.root(refs[0], repair["runId"]) / "report.json").read_bytes())
        assert any(e["reason"] == "original_credential_not_persisted" for e in report["residue"])
        assert report["cleanupFrom"] == original["runId"]
        assert not any(s["id"] == "create" for c in report["cases"] for s in c["steps"])
        if not allow_auth:
            assert any(e["reason"] == "cleanup_not_verified" for e in report["residue"])
    finally:
        server.shutdown()
        server.server_close()
