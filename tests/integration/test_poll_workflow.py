import asyncio
import json
import threading
import time

import pytest

from horus.core.runs import Runs
from horus.core.service import Service
from tests.async_plan import reviewed_async_plan
from tests.fixtures.workflow_api import WorkflowServer
from tests.support import FIXTURES


@pytest.fixture
def asynchronous_workflow(tmp_path, monkeypatch):
    for identity in ("OWNER", "OUTSIDER"):
        monkeypatch.setenv(f"HORUS_{identity}_USERNAME", identity.lower())
        monkeypatch.setenv(f"HORUS_{identity}_PASSWORD", identity.lower() + "-private")
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    server = WorkflowServer(0, users={"owner": "owner-private", "outsider": "outsider-private"},
                            job_delay=0.3)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = Service(tmp_path, read_roots=[FIXTURES])
    refs = reviewed_async_plan(service, f"http://127.0.0.1:{server.server_port}")
    try:
        yield server, Runs(service), refs
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode,verdict", [
    ("normal", "passed"), ("stuck-job", "failed"), ("illegal-state", "failed"),
])
async def test_real_polling_detects_stuck_jobs_and_illegal_state_changes(
    asynchronous_workflow, mode, verdict
):
    server, runs, refs = asynchronous_workflow
    server.mode = mode
    run = await asyncio.to_thread(runs.start, *refs, "test", "async-workflow")
    result = await runs.wait(refs[0], run["runId"], wait_ms=10000)
    assert result["status"] == "completed" and result["verdict"] == verdict
    root = runs.root(refs[0], run["runId"])
    report = json.loads((root / "report.json").read_bytes())
    case = report["cases"][0]
    poll = next(step for step in case["steps"] if step["id"] == "completion")
    assert poll["identityRef"] == "owner"
    assert poll["observations"][0]["matched"] is False
    assert all(step["status"] == "passed" for step in case["cleanup"])
    assert not server.jobs and not server.tickets and not server.tokens
    details = (root / "details.md").read_text()
    assert '"observations"' in details and f'"attempts": {poll["attempts"]}' in details
    assert "<system-out>" in (root / "junit.xml").read_text()
    if mode == "stuck-job":
        assert poll["attempts"] == 10 and poll["reason"] == "poll_attempts_exhausted"
        assert all("completedState" not in entry["variables"] for entry in report["ledger"])
        assert next(step for step in case["steps"] if step["id"] == "submitted-ticket")[
            "status"] == "blocked"
    else:
        assert 1 < poll["attempts"] <= 10 and poll["status"] == "passed"
        assert poll["observations"][-1]["matched"] is True
        assert any(entry["variables"].get("completedState") == "done" for entry in report["ledger"])
        if mode == "illegal-state":
            assert next(step for step in case["steps"] if step["id"] == "reject-edit")[
                "status"] == "failed"


async def test_cancellation_during_readonly_polling_enters_cleanup(asynchronous_workflow):
    server, runs, refs = asynchronous_workflow
    server.job_delay = 30
    run = await asyncio.to_thread(runs.start, *refs, "test", "cancel-poll")
    for _ in range(500):
        if any(method == "GET" and path.startswith("/jobs/") and actor == "owner"
               for method, path, actor in server.requests):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the polling request did not start")
    started = time.monotonic()
    runs.cancel(refs[0], run["runId"])
    result = await runs.wait(refs[0], run["runId"], wait_ms=5000)
    assert result["status"] == "cancelled" and result["verdict"] == "inconclusive"
    assert time.monotonic() - started < 3
    assert not server.jobs and not server.tickets and not server.tokens
    report = json.loads((runs.root(refs[0], run["runId"]) / "report.json").read_bytes())
    assert all(step["status"] == "passed" for step in report["cases"][0]["cleanup"])
    assert sum(method == "POST" and path.endswith("/submit")
               for method, path, _ in server.requests) == 1
