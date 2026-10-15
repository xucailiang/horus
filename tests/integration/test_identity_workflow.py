import asyncio
import json
import os
import signal
import threading

import pytest

from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write
from tests.fixtures.workflow_api import WorkflowServer
from tests.support import FIXTURES
from tests.workflow_plan import reviewed_workflow_plan


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    credentials = {
        "OWNER_USERNAME": "alice-user",
        "OWNER_PASSWORD": "alice-private",
        "OUTSIDER_USERNAME": "bob-user",
        "OUTSIDER_PASSWORD": "bob-private",
    }
    for key, value in credentials.items():
        monkeypatch.setenv("HORUS_" + key, value)
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    server = WorkflowServer(
        0, users={"alice-user": "alice-private", "bob-user": "bob-private"}, read_delay=0.05
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = Service(tmp_path, read_roots=[FIXTURES])
    refs = reviewed_workflow_plan(service, f"http://127.0.0.1:{server.server_port}")
    runs = Runs(service)
    try:
        yield server, runs, refs
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode,verdict", [("normal", "passed"), ("permission-error", "failed")])
async def test_real_worker_switches_identity_for_http_load_and_cleanup(workflow, mode, verdict):
    server, runs, refs = workflow
    server.mode = mode
    with pytest.raises(HorusError, match="review_required"):
        runs.start(refs[0], refs[1], None, "test", "not-approved")
    assert not server.requests
    run = await asyncio.to_thread(runs.start, *refs, "test", "identity-run")
    result = await runs.wait(refs[0], run["runId"], wait_ms=10000)
    assert result["status"] == "completed"
    assert result["verdict"] == verdict
    assert not server.tokens and not server.tickets and not server.jobs
    assert any(
        method == "GET" and path.startswith("/tickets/") and actor == "bob-user"
        for method, path, actor in server.requests
    )
    assert all(actor == "alice-user" for method, _, actor in server.requests if method == "DELETE")
    root = runs.root(refs[0], run["runId"])
    report = json.loads((root / "report.json").read_bytes())
    assert {step.get("identityRef") for case in report["cases"] for step in case["cleanup"]} == {
        "owner",
        "outsider",
    }
    assert all(step["status"] == "passed" for case in report["cases"] for step in case["cleanup"])
    assert "(owner): passed" in (root / "report.md").read_text()
    assert 'name="identityRef" value="outsider"' in (root / "junit.xml").read_text()
    metrics = report["cases"][1]["steps"][-1]["metrics"]
    assert metrics["completedIterations"] == 20 and metrics["httpErrorRate"] == 0
    assert metrics["dataCheckFailureRate"] == 0
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_text(errors="replace")
            assert "alice-private" not in content and "bob-private" not in content
    assert not any("accessToken" in entry["variables"] for entry in report["ledger"])
    assert any(entry.get("identityRef") == "outsider" for entry in report["ledger"])


async def test_crash_recovery_reauthenticates_each_identity_and_reports_lost_sessions(workflow):
    server, runs, refs = workflow
    original = await asyncio.to_thread(runs.start, *refs, "test", "identity-interruption")
    root = runs.root(refs[0], original["runId"])
    for _ in range(500):
        if (root / "ledger.json").exists():
            entries = json.loads((root / "ledger.json").read_bytes())
            if any(entry["caseId"] == "load" and entry["stepId"] == "read" for entry in entries):
                break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("load did not start")
    pid = json.loads((root / "process.json").read_bytes())["pid"]
    os.kill(pid, signal.SIGKILL)
    await asyncio.sleep(0.2)
    atomic_write(root / "heartbeat.json", {"pid": pid, "at": 0})
    assert runs.get(refs[0], original["runId"])["status"] == "interrupted"
    old_report = (root / "report.json").read_bytes()
    assert len(server.tokens) == 2 and server.tickets
    creations = sum(method == "POST" and path == "/tickets" for method, path, _ in server.requests)
    repair = await asyncio.to_thread(runs.cleanup, refs[0], original["runId"], "identity-repair")
    result = await runs.wait(refs[0], repair["runId"], wait_ms=10000)
    assert result["status"] == "completed" and result["verdict"] == "inconclusive"
    assert not server.tickets and not server.jobs
    assert (
        len(server.tokens) == 2
    )  # Old credentials were never persisted; new sessions were revoked.
    assert creations == sum(
        method == "POST" and path == "/tickets" for method, path, _ in server.requests
    )
    assert (root / "report.json").read_bytes() == old_report
    assert {
        entry["identityRef"]
        for entry in result["residue"]
        if entry["reason"] == "original_credential_not_persisted"
    } == {"owner", "outsider"}
