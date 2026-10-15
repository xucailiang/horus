import asyncio
import json
import threading
import uuid

import pytest

from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError, file_lock
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
