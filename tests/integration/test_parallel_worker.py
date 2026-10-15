import asyncio
import json
import os
import signal
import threading

import pytest

from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import atomic_write
from tests.fixtures.recording_api import RecordingServer
from tests.parallel_plan import reviewed_parallel_plan
from tests.support import FIXTURES


@pytest.fixture
def parallel_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_USERNAME", "parallel-user")
    monkeypatch.setenv("HORUS_PASSWORD", "parallel-private-password")
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    server = RecordingServer(0, username="parallel-user", password="parallel-private-password")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = Service(tmp_path, read_roots=[FIXTURES])
    try:
        yield server, service
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("limit", [1, 2])
async def test_real_workers_observe_parallel_limit_and_keep_k6_exclusive(parallel_backend, limit):
    server, service = parallel_backend
    refs = reviewed_parallel_plan(service, f"http://127.0.0.1:{server.server_port}", limit=limit)
    runs = Runs(service)
    run = await asyncio.to_thread(runs.start, *refs, "test", "parallel-cases")
    summary = await runs.wait(refs[0], run["runId"], wait_ms=30000)
    assert summary["status"] == "completed" and summary["verdict"] == "passed"
    assert server.peak_functional_cases == limit
    assert bool(server.read_overlaps) is (limit == 2)
    assert not server.load_overlaps and not server.credential_mismatches
    assert not server.records and not server.tokens
    for name, _method, path in server.tagged_requests:
        if path.startswith("/records/"):
            assert path == "/records/" + server.case_resources[name]
    assert len(set(server.case_resources.values())) == 5
    root = runs.root(refs[0], run["runId"])
    report = json.loads((root / "report.json").read_bytes())
    assert report["scheduling"]["peakFunctionalCases"] == limit
    assert f"Functional cases: peak {limit}, approved limit {limit}" in (
        root / "report.md"
    ).read_text()
    cases = {case["id"]: case for case in report["cases"]}
    assert (
        max(cases[name]["finishedAt"] for name in ("first", "second", "third"))
        <= (cases["load"]["startedAt"])
    )
    assert cases["load"]["finishedAt"] <= cases["last"]["startedAt"]
    metrics = cases["load"]["steps"][-1]["metrics"]
    assert metrics["completedIterations"] == 100 and metrics["dataCheckFailureRate"] == 0
    assert metrics["httpErrorRate"] == 0
    assert all(step["status"] == "passed" for case in cases.values() for step in case["cleanup"])
    for path in root.rglob("*"):
        if path.is_file():
            assert "parallel-private-password" not in path.read_text(errors="replace")


async def test_killed_parallel_workers_recover_both_cases_without_starting_queued_cases(
    parallel_backend,
):
    server, service = parallel_backend
    refs = reviewed_parallel_plan(service, f"http://127.0.0.1:{server.server_port}")
    runs = Runs(service)
    original = await asyncio.to_thread(runs.start, *refs, "test", "parallel-crash")
    root = runs.root(refs[0], original["runId"])
    for _ in range(500):
        with server.lock:
            reading = set(server.reading)
        if {"first", "second"}.issubset(reading):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("independent reads did not overlap")
    pid = json.loads((root / "process.json").read_bytes())["pid"]
    os.kill(pid, signal.SIGKILL)
    await asyncio.sleep(0.3)
    atomic_write(root / "heartbeat.json", {"pid": pid, "at": 0})
    assert runs.get(refs[0], original["runId"])["status"] == "interrupted"
    old_report = (root / "report.json").read_bytes()
    creations = sum(
        method == "POST" and path == "/records" for _, method, path in server.tagged_requests
    )
    assert creations == 2
    repair = await asyncio.to_thread(runs.cleanup, refs[0], original["runId"], "parallel-repair")
    summary = await runs.wait(refs[0], repair["runId"], wait_ms=10000)
    assert summary["status"] == "completed" and summary["verdict"] == "inconclusive"
    assert not server.records and len(server.tokens) == 2
    assert creations == sum(
        method == "POST" and path == "/records" for _, method, path in server.tagged_requests
    )
    assert all(name in {"first", "second"} for name, _, _ in server.tagged_requests)
    assert (root / "report.json").read_bytes() == old_report
