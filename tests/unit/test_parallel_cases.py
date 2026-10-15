import asyncio
import json

import pytest

from horus import worker
from horus.adapters.hurl import HurlAdapter, Response
from horus.adapters.k6 import K6Adapter
from horus.core.runner import Runner
from horus.core.runs import Runs
from horus.core.store import atomic_write
from tests.parallel_plan import reviewed_parallel_plan
from tests.unit.test_execution_safety import prepared_runner


def parallel_runner(tmp_path, monkeypatch, *, limit=2, order=None, run_duration_ms=120000):
    original = prepared_runner(tmp_path, monkeypatch)
    project, plan, review = reviewed_parallel_plan(
        original.service,
        "http://127.0.0.1:1",
        limit=limit,
        run_duration_ms=run_duration_ms,
        **({"order": order} if order else {}),
    )
    config = original.service.project(project).get("reviews", review)["configRef"]
    atomic_write(
        original.root / "intent.json",
        {**original.intent, "planRef": plan, "reviewRef": review, "configRef": config},
    )
    return Runner(original.service, project, original.run_id, original.root)


@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize("failed_case", [None, "first"])
async def test_functional_limit_is_observed_data_is_isolated_and_load_is_exclusive(
    tmp_path, monkeypatch, limit, failed_case
):
    runner = parallel_runner(tmp_path, monkeypatch, limit=limit)
    active, remaining = set(), set()
    peak = 0
    finished = []

    async def version(name):
        return name

    async def request(self, operation, base_url, request):
        nonlocal peak
        name = request.headers["X-Horus-Case"]
        await asyncio.sleep(0.01)
        match operation["definition"]["operationId"]:
            case "login":
                active.add(name)
                if not name.startswith("load"):
                    peak = max(peak, len(active))
                return Response(200, {}, {"token": "private-" + name}, 1)
            case "createRecord":
                assert request.headers["Authorization"] == "Bearer private-" + name
                remaining.add(name)
                return Response(201, {}, {"id": name, "value": 42}, 1)
            case "getRecord":
                assert request.pathParams["id"] == name
                assert request.headers["Authorization"] == "Bearer private-" + name
                return Response(
                    200 if name in remaining else 404,
                    {},
                    {"value": 43 if name == failed_case else 42},
                    1,
                )
            case "deleteRecord":
                assert request.pathParams["id"] == name
                remaining.remove(name)
                return Response(204, {}, None, 1)
            case "logout":
                active.remove(name)
                finished.append(name)
                return Response(204, {}, None, 1)

    async def load(self, operation, base_url, step):
        assert active == {"load"} and remaining == {"load"}
        assert set(finished) == {"first", "second", "third"}
        return {"status": "passed"}

    monkeypatch.setattr("horus.core.runner.check_engine", version)
    monkeypatch.setattr(HurlAdapter, "request", request)
    monkeypatch.setattr(K6Adapter, "run", load)
    result = await runner.execute()
    assert result["verdict"] == ("failed" if failed_case else "passed")
    assert peak == limit
    assert not remaining and not active
    report = json.loads((runner.root / "report.json").read_bytes())
    assert [case["id"] for case in report["cases"]] == ["first", "second", "third", "load", "last"]
    assert report["scheduling"]["peakFunctionalCases"] == limit
    assert all(
        step["status"] == "passed"
        for case in report["cases"]
        if case["id"] != failed_case
        for step in case["steps"]
    )
    assert all(
        any(
            entry["caseId"] == name and entry["variables"].get("recordId") == name
            for entry in report["ledger"]
        )
        for name in finished
    )


async def test_cancel_poll_preserves_sibling_write_then_cleans_both_cases(tmp_path, monkeypatch):
    runner = parallel_runner(tmp_path, monkeypatch, order=("reader", "writer", "queued", "load"))
    raw = runner.plan.model_dump(mode="json")
    read = raw["cases"][0]["steps"][2]
    evidence = read["assertions"][0]["evidenceRefs"]
    raw["cases"][0]["steps"][2] = {
        "kind": "poll",
        "id": "read",
        "requestStep": read,
        "until": {
            "kind": "compare",
            "left": {"kind": "literal", "value": False},
            "operator": "eq",
            "right": {"kind": "literal", "value": True},
            "evidenceRefs": evidence,
        },
        "maxAttempts": 1000,
        "intervalMs": 1,
        "timeoutMs": 5000,
    }
    service, project = runner.service, runner.project_ref
    config = service.project(project).get("configs", runner.intent["configRef"])
    config["policy"]["pollAllow"] = ["getRecord"]
    plan = service.save_plan(project, raw)["planRef"]
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
        "cancel regression",
    )
    atomic_write(
        runner.root / "intent.json",
        {
            **runner.intent,
            "planRef": plan,
            "reviewRef": decision["reviewRef"],
            "configRef": config_ref,
        },
    )
    writing, polling, poll_stopped, finish_write, reader_cleaned = [
        asyncio.Event() for _ in range(5)
    ]
    remaining, calls = set(), []
    write_cancelled = False

    async def version(name):
        return name

    async def request(self, operation, base_url, request):
        nonlocal write_cancelled
        name = request.headers["X-Horus-Case"]
        operation_id = operation["definition"]["operationId"]
        calls.append((name, operation_id))
        if operation_id == "login":
            return Response(200, {}, {"token": "private-" + name}, 1)
        if operation_id == "createRecord":
            if name == "writer":
                writing.set()
                try:
                    await finish_write.wait()
                except asyncio.CancelledError:
                    write_cancelled = True
                    raise
            remaining.add(name)
            return Response(201, {}, {"id": name, "value": 42}, 1)
        if operation_id == "getRecord":
            if name == "reader" and name in remaining:
                polling.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    poll_stopped.set()
            return Response(200 if name in remaining else 404, {}, {"value": 42}, 1)
        if operation_id == "deleteRecord":
            remaining.remove(name)
            return Response(204, {}, None, 1)
        if name == "reader" and operation_id == "logout":
            reader_cleaned.set()
        return Response(204, {}, None, 1)

    monkeypatch.setattr("horus.core.runner.check_engine", version)
    monkeypatch.setattr(HurlAdapter, "request", request)
    execution = asyncio.create_task(worker.execute(tmp_path, project, runner.run_id))
    try:
        await asyncio.wait_for(asyncio.gather(writing.wait(), polling.wait()), 2)
        Runs(service).cancel(project, runner.run_id)
        await asyncio.wait_for(poll_stopped.wait(), 2)
        await asyncio.wait_for(reader_cleaned.wait(), 2)
        assert not write_cancelled
        assert json.loads((runner.root / "state.json").read_bytes())["status"] == "running"
        finish_write.set()
        await asyncio.wait_for(execution, 3)
        report = json.loads((runner.root / "report.json").read_bytes())
        assert report["status"] == "cancelled"
        assert not remaining
        assert all(
            step["status"] == "passed" for case in report["cases"][:2] for step in case["cleanup"]
        )
        assert all(name not in {"queued", "load"} for name, _ in calls)
    finally:
        finish_write.set()
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)


async def test_deadline_stops_queued_cases_but_finishes_started_case_cleanup(tmp_path, monkeypatch):
    runner = parallel_runner(
        tmp_path, monkeypatch, order=("slow-a", "slow-b", "queued"), run_duration_ms=200
    )
    calls, remaining = [], set()

    async def version(name):
        return name

    async def request(self, operation, base_url, request):
        name = request.headers["X-Horus-Case"]
        operation_id = operation["definition"]["operationId"]
        calls.append((name, operation_id))
        if operation_id == "login":
            return Response(200, {}, {"token": "private-" + name}, 1)
        if operation_id == "createRecord":
            remaining.add(name)
            return Response(201, {}, {"id": name, "value": 42}, 1)
        if operation_id == "getRecord":
            if name in remaining:
                await asyncio.sleep(2)
            return Response(404, {}, None, 1)
        if operation_id == "deleteRecord":
            await asyncio.sleep(0.02)
            remaining.remove(name)
        return Response(204, {}, None, 1)

    monkeypatch.setattr("horus.core.runner.check_engine", version)
    monkeypatch.setattr(HurlAdapter, "request", request)
    result = await runner.execute()
    assert result["verdict"] == "inconclusive" and not remaining
    assert all(name != "queued" for name, _ in calls)
    report = json.loads((runner.root / "report.json").read_bytes())
    assert all(
        step["status"] == "passed" for case in report["cases"][:2] for step in case["cleanup"]
    )
    assert all(step["reason"] == "case_not_started" for step in report["cases"][-1]["cleanup"])
