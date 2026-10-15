import asyncio
import json
import os
import signal
import threading
import xml.etree.ElementTree as ET

import pytest

from horus.core.reports import recorded_events, result_steps
from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write
from tests.branch_plan import reviewed_branch_plan
from tests.fixtures.workflow_api import WorkflowServer
from tests.support import FIXTURES


@pytest.fixture
def branched_workflow(tmp_path, monkeypatch):
    for identity in ("OWNER", "OUTSIDER"):
        monkeypatch.setenv(f"HORUS_{identity}_USERNAME", identity.lower())
        monkeypatch.setenv(f"HORUS_{identity}_PASSWORD", identity.lower() + "-private")
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    server = WorkflowServer(0, users={"owner": "owner-private", "outsider": "outsider-private"})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = Service(tmp_path, read_roots=[FIXTURES])
    refs = reviewed_branch_plan(service, f"http://127.0.0.1:{server.server_port}")
    try:
        yield server, Runs(service), refs
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "delay,mode,verdict",
    [
        (0, "normal", "passed"),
        (0.4, "normal", "passed"),
        (0.4, "stuck-job", "failed"),
        (0, "illegal-state", "failed"),
    ],
)
async def test_worker_selects_ready_or_waits_and_preserves_cleanup(
    branched_workflow, delay, mode, verdict
):
    server, runs, refs = branched_workflow
    server.job_delay, server.mode = delay, mode
    with pytest.raises(HorusError, match="review_required"):
        runs.start(refs[0], refs[1], None, "test", "not-approved")
    assert not server.requests
    run = await asyncio.to_thread(runs.start, *refs, "test", "conditional")
    summary = await runs.wait(refs[0], run["runId"], wait_ms=10000)
    assert summary["status"] == "completed" and summary["verdict"] == verdict
    root = runs.root(refs[0], run["runId"])
    report = json.loads((root / "report.json").read_bytes())
    branch = next(step for step in report["cases"][0]["steps"] if step["id"] == "completion")
    assert branch["selected"] == ("then" if delay == 0 else "else")
    skipped = [step for step in branch["steps"] if step["status"] == "skipped"]
    assert len(skipped) == 1 and skipped[0]["reason"] == "not_selected"
    assert all(entry["stepId"] != skipped[0]["id"] for entry in report["ledger"])
    prefix = report["cases"][0]["id"] + "." + skipped[0]["id"]
    assert report["executionCoverage"]["steps"][prefix]["status"] == "skipped"
    assert all(
        entry["status"] == "skipped"
        for ref, entry in report["executionCoverage"]["assertions"].items()
        if ref.startswith(prefix + ".")
    )
    nodes = {node.attrib["name"]: node for node in ET.parse(root / "junit.xml").findall("testcase")}
    assert nodes[skipped[0]["id"]].find("skipped") is not None
    assert "completion" not in nodes  # A routing decision is not another passing HTTP test.
    assert all(step["status"] == "passed" for step in report["cases"][0]["cleanup"])
    assert not server.jobs and not server.tickets and not server.tokens
    if mode == "stuck-job":
        assert any(step.get("reason") == "poll_attempts_exhausted" for step in branch["steps"])
    if mode == "illegal-state":
        assert (
            next(step for step in report["cases"][0]["steps"] if step["id"] == "reject-edit")[
                "status"
            ]
            == "failed"
        )


async def test_crash_repair_uses_recorded_branch_and_never_alternative_auth(branched_workflow):
    server, runs, refs = branched_workflow
    server.mode = "stuck-job"
    run = await asyncio.to_thread(runs.start, *refs, "test", "branch-crash")
    root = runs.root(refs[0], run["runId"])
    for _ in range(500):
        if any(event.get("kind") == "poll-attempt" for event in recorded_events(root)):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the selected polling path did not start")
    pid = json.loads((root / "process.json").read_bytes())["pid"]
    os.kill(pid, signal.SIGKILL)
    await asyncio.sleep(0.2)
    atomic_write(root / "heartbeat.json", {"pid": pid, "at": 0})
    assert runs.get(refs[0], run["runId"])["status"] == "interrupted"
    original = (root / "report.json").read_bytes()
    writes = sum(
        method == "POST" and path.endswith("/submit") for method, path, _ in server.requests
    )
    repaired = await asyncio.to_thread(runs.cleanup, refs[0], run["runId"], "branch-repair")
    summary = await runs.wait(refs[0], repaired["runId"], wait_ms=10000)
    assert summary["status"] == "completed" and summary["verdict"] == "inconclusive"
    repair = json.loads((runs.root(refs[0], repaired["runId"]) / "report.json").read_bytes())
    ids = [step["id"] for step in result_steps(repair["cases"][0]["steps"])]
    assert "owner-auth.then.login-owner" in ids
    assert all("alternate-login" not in step_id for step_id in ids)
    assert writes == sum(
        method == "POST" and path.endswith("/submit") for method, path, _ in server.requests
    )
    assert not server.jobs and not server.tickets and len(server.tokens) == 2
    assert (root / "report.json").read_bytes() == original


async def test_cancel_selected_poll_path_still_cleans_up(branched_workflow):
    server, runs, refs = branched_workflow
    server.mode = "stuck-job"
    run = await asyncio.to_thread(runs.start, *refs, "test", "branch-cancel")
    root = runs.root(refs[0], run["runId"])
    for _ in range(500):
        if any(event.get("kind") == "poll-attempt" for event in recorded_events(root)):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the polling path did not start")
    runs.cancel(refs[0], run["runId"])
    summary = await runs.wait(refs[0], run["runId"], wait_ms=5000)
    assert summary["status"] == "cancelled" and summary["verdict"] == "inconclusive"
    report = json.loads((root / "report.json").read_bytes())
    assert all(step["status"] == "passed" for step in report["cases"][0]["cleanup"])
    assert not server.jobs and not server.tickets and not server.tokens
