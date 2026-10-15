import asyncio
import json
import time
import uuid

from horus import worker
from horus.adapters.hurl import HurlAdapter, Response
from horus.adapters.k6 import K6Adapter
from horus.core.models import HttpStep
from horus.core.runner import Runner
from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import atomic_write
from tests.support import FIXTURES, reviewed_regression_plan


def prepared_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_USERNAME", "user")
    monkeypatch.setenv("HORUS_PASSWORD", "private-password")
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    service = Service(tmp_path, read_roots=[FIXTURES])
    project, plan, review = reviewed_regression_plan(service, "http://127.0.0.1:1")
    gate = service.review_gate(project, plan, review, "test")
    run_id = "run_" + uuid.uuid4().hex
    root = service.project(project).root / "runs" / run_id
    intent = {
        "projectRef": project,
        "runId": run_id,
        "planRef": plan,
        "reviewRef": review,
        "configRef": gate["configRef"],
        "environment": "test",
        "createdAt": time.time(),
    }
    atomic_write(root / "intent.json", intent)
    atomic_write(root / "state.json", {**intent, "status": "queued"})
    return Runner(service, project, run_id, root)


async def test_cancel_during_cleanup_finishes_delete_before_reporting(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    deleting, finish_delete = asyncio.Event(), asyncio.Event()
    remaining = {"resource"}

    async def version(name):
        return name

    async def request(self, operation, base_url, request):
        match operation["method"], operation["path"]:
            case "POST", "/auth/token":
                return Response(200, {}, {"token": "private-token"}, 1)
            case "POST", "/records":
                return Response(201, {}, {"id": "resource", "value": 42}, 1)
            case "DELETE", _:
                deleting.set()
                await finish_delete.wait()
                remaining.clear()
                return Response(204, {}, None, 1)
            case "POST", "/auth/logout":
                return Response(204, {}, None, 1)
            case _:
                return Response(200 if remaining else 404, {}, {"value": 42}, 1)

    monkeypatch.setattr("horus.core.runner.check_engine", version)
    monkeypatch.setattr(HurlAdapter, "request", request)
    execution = asyncio.create_task(worker.execute(tmp_path, runner.project_ref, runner.run_id))
    try:
        await asyncio.wait_for(deleting.wait(), 2)
        Runs(runner.service).cancel(runner.project_ref, runner.run_id)
        await asyncio.sleep(0.2)
        finish_delete.set()
        await asyncio.wait_for(execution, 2)
        report = json.loads((runner.root / "report.json").read_bytes())
        assert report["status"] == "cancelled"
        assert not remaining
        assert report["cases"][0]["cleanup"][0]["status"] == "passed"
        assert report["cases"][1]["steps"][0]["status"] == "blocked"
    finally:
        finish_delete.set()
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)


async def test_secret_capture_objects_and_numbers_never_reach_events(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)

    async def request(self, operation, base_url, request):
        return Response(200, {}, {"session": {"access": "private-session", "pin": 948376}}, 1)

    monkeypatch.setattr(HurlAdapter, "request", request)
    step = HttpStep.model_validate({
        "id": "credentials", "operationId": "login", "request": {},
        "captures": {"session": {
            "jsonPointer": "/session", "secret": True, "purpose": "authentication",
        }},
        "assertions": [{"id": "status", "status": 200, "evidenceRefs": ["contract"]}],
    })
    context = {"vars": {}, "secrets": {}, "run": {"id": runner.run_id}}
    result = await runner.step(step, context, case_id="secret")
    assert result["status"] == "passed"
    assert context["vars"]["session"]["access"] == "private-session"
    for path in runner.root.rglob("*"):
        if path.is_file():
            content = path.read_text()
            assert "private-session" not in content
            assert "948376" not in content


async def test_short_numeric_secret_preserves_report_identity(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)

    async def version(name):
        return name

    async def request(self, operation, base_url, request):
        match operation["method"], operation["path"]:
            case "POST", "/auth/token":
                return Response(200, {}, {"token": 1}, 1)
            case "POST", "/records":
                return Response(201, {}, {"id": "resource", "value": 42}, 1)
            case ("DELETE", _) | ("POST", "/auth/logout"):
                return Response(204, {}, None, 1)
            case _:
                return Response(404, {}, None, 1)

    async def load(self, operation, base_url, step):
        return {"status": "passed"}

    monkeypatch.setattr("horus.core.runner.check_engine", version)
    monkeypatch.setattr(HurlAdapter, "request", request)
    monkeypatch.setattr(K6Adapter, "run", load)
    result = await runner.execute()
    assert result["projectRef"] == runner.project_ref
    assert result["runId"] == runner.run_id
    report = json.loads((runner.root / "report.json").read_bytes())
    assert len(report["cases"]) == 2
    assert report["cases"][0]["cleanup"][-1]["status"] == "passed"


async def test_secret_equal_to_control_word_cannot_change_test_verdict(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    runner.redactor.register("failed")

    async def request(self, operation, base_url, request):
        return Response(500, {}, {"message": "failed"}, 1)

    monkeypatch.setattr(HurlAdapter, "request", request)
    step = HttpStep.model_validate({
        "id": "failed", "operationId": "login", "request": {},
        "assertions": [{"id": "failed", "status": 200, "evidenceRefs": ["contract"]}],
    })
    action = await runner.step(step, {"vars": {}, "secrets": {}, "run": {}}, case_id="failed")
    assert action["status"] == "failed"
    assert action["id"] == "failed"
    assert action["assertions"][0]["status"] == "failed"
    assert action["response"]["body"]["message"] == "[REDACTED]"
