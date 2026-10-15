import asyncio
import json
import time
import uuid

import pytest

from horus import worker
from horus.adapters.hurl import HurlAdapter, Response
from horus.adapters.k6 import K6Adapter
from horus.core.models import Assertion, Capture, HttpStep
from horus.core.reports import verdict
from horus.core.runner import Runner
from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write
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


@pytest.mark.parametrize("secret", [True, False])
async def test_authentication_objects_and_numbers_never_reach_events(tmp_path, monkeypatch, secret):
    runner = prepared_runner(tmp_path, monkeypatch)

    async def request(self, operation, base_url, request):
        return Response(200, {}, {"session": {"access": "private-session", "pin": 948376}}, 1)

    monkeypatch.setattr(HurlAdapter, "request", request)
    step = HttpStep.model_validate(
        {
            "id": "credentials",
            "operationId": "login",
            "request": {},
            "captures": {
                "session": {
                    "jsonPointer": "/session",
                    "secret": secret,
                    "purpose": "authentication",
                }
            },
            "assertions": [
                {"id": "status", "status": 200, "evidenceRefs": ["contract"]},
                {
                    "id": "same-session",
                    "jsonPointer": "/session",
                    "expected": "${vars.session}",
                    "evidenceRefs": ["contract"],
                },
            ],
        }
    )
    context = runner.context()
    result = await runner.step(step, context, case_id="secret")
    assert result["status"] == "passed"
    assert context.view(None)["vars"]["session"]["access"] == "private-session"
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
    step = HttpStep.model_validate(
        {
            "id": "failed",
            "operationId": "login",
            "request": {},
            "assertions": [{"id": "failed", "status": 200, "evidenceRefs": ["contract"]}],
        }
    )
    action = await runner.step(step, runner.context(), case_id="failed")
    assert action["status"] == "failed"
    assert action["id"] == "failed"
    assert action["assertions"][0]["status"] == "failed"
    assert action["response"]["body"]["message"] == "[REDACTED]"


async def test_cleanup_with_missing_captures_skips_before_materializing_request(
    tmp_path, monkeypatch
):
    runner = prepared_runner(tmp_path, monkeypatch)
    step = HttpStep.model_validate(
        {
            "id": "delete",
            "operationId": "deleteRecord",
            "request": {"pathParams": {"id": "${vars.recordId}"}},
            "requires": ["recordId"],
            "assertions": [{"id": "status", "status": 204, "evidenceRefs": ["contract"]}],
        }
    )
    action = await runner.step(step, runner.context(), case_id="missing", cleanup=True)
    assert action["status"] == "skipped"
    assert action["reason"] == "missing_cleanup_inputs"
    assert not runner.ledger
    assert verdict([{"steps": [], "cleanup": [action]}]) == "inconclusive"


def test_cleanup_does_not_start_while_original_run_is_active(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    with pytest.raises(HorusError, match="run_not_finished"):
        Runs(runner.service).cleanup(runner.project_ref, runner.run_id, "still-active")


def prepared_repair(tmp_path, monkeypatch, entries):
    runner = prepared_runner(tmp_path, monkeypatch)
    original_id = "run_" + uuid.uuid4().hex
    original_root = runner.root.parent / original_id
    atomic_write(original_root / "intent.json", {**runner.intent, "runId": original_id})
    atomic_write(original_root / "ledger.json", entries)
    runner.intent["cleanupFrom"] = original_id
    runner.plan.cases = runner.plan.cases[:1]
    return runner


def original_entry(step_id, operation_id, variables=None):
    return {
        "caseId": "lifecycle",
        "stepId": step_id,
        "operationId": operation_id,
        "cleanup": False,
        "state": "confirmed",
        "variables": variables or {},
    }


async def test_repair_without_persisted_resource_id_reports_unresolved_write(tmp_path, monkeypatch):
    runner = prepared_repair(tmp_path, monkeypatch, [original_entry("create", "createRecord")])
    case = runner.plan.cases[0]
    case.steps = [case.steps[1]]
    case.steps[0].captures["recordId"].secret = True
    case.cleanup = case.cleanup[:2]
    for step in case.cleanup:
        step.request.headers = {}
        step.requires = ["recordId"]

    async def unexpected_request(*args):
        pytest.fail("A secret resource ID cannot be reconstructed from the ledger")

    monkeypatch.setattr(HurlAdapter, "request", unexpected_request)
    result = await runner.repair({**runner.intent, "cases": []}, time.monotonic() + 10)
    assert result["verdict"] == "inconclusive"
    assert any(
        entry["stepId"] == "create" and entry["reason"] == "cleanup_not_verified"
        for entry in result["residue"]
    )


@pytest.mark.parametrize("revoke_ok", [True, False])
async def test_repair_uses_captured_token_for_cleanup_even_when_auth_check_fails(
    tmp_path, monkeypatch, revoke_ok
):
    runner = prepared_repair(
        tmp_path,
        monkeypatch,
        [
            original_entry("login", "login"),
            original_entry("create", "createRecord", {"recordId": "resource"}),
        ],
    )
    runner.plan.cases[0].steps[0].assertions.append(
        Assertion(id="fast", elapsedMs=True, operator="lte", expected=0, evidenceRefs=["contract"])
    )
    resources, tokens = {"resource"}, set()

    async def request(self, operation, base_url, request):
        match operation["definition"]["operationId"]:
            case "login":
                tokens.add("fresh-private-token")
                return Response(200, {}, {"token": "fresh-private-token"}, 1)
            case "deleteRecord":
                assert request.headers["Authorization"] == "Bearer fresh-private-token"
                resources.clear()
                return Response(204, {}, None, 1)
            case "getRecord":
                return Response(404 if not resources else 200, {}, {}, 1)
            case "logout":
                if revoke_ok:
                    tokens.clear()
                return Response(204 if revoke_ok else 500, {}, None, 1)
            case _:
                pytest.fail("Repair must not create business resources")

    monkeypatch.setattr(HurlAdapter, "request", request)
    result = await runner.repair({**runner.intent, "cases": []}, time.monotonic() + 10)
    assert result["verdict"] == "failed"
    assert not resources
    assert bool(tokens) is not revoke_ok
    assert (
        any(
            entry["reason"] == "cleanup_credential_revocation_unverified"
            for entry in result["residue"]
        )
        is not revoke_ok
    )
    assert "fresh-private-token" not in (runner.root / "report.json").read_text()


async def test_repair_resolves_authentication_predecessors_without_replaying_preparation(
    tmp_path, monkeypatch
):
    runner = prepared_repair(
        tmp_path,
        monkeypatch,
        [
            original_entry("login", "login"),
            original_entry("create", "createRecord", {"recordId": "resource"}),
        ],
    )
    case = runner.plan.cases[0]
    bootstrap = case.steps[0].model_copy(deep=True)
    bootstrap.id = "bootstrap"
    bootstrap.captures = {
        "bootstrapToken": Capture(jsonPointer="/challenge", secret=True, purpose="authentication")
    }
    case.steps[0].request.headers = {"X-Challenge": "${vars.bootstrapToken}"}
    case.steps.insert(0, bootstrap)
    remaining = {"resource"}
    sent = []

    async def request(self, operation, base_url, request):
        sent.append(operation["definition"]["operationId"])
        match operation["definition"]["operationId"]:
            case "login":
                if "X-Challenge" not in request.headers:
                    return Response(200, {}, {"challenge": "private-challenge"}, 1)
                assert request.headers["X-Challenge"] == "private-challenge"
                return Response(200, {}, {"token": "fresh-private-token"}, 1)
            case "deleteRecord":
                remaining.clear()
                return Response(204, {}, None, 1)
            case "getRecord":
                return Response(404 if not remaining else 200, {}, {}, 1)
            case "logout":
                return Response(204, {}, None, 1)
            case _:
                pytest.fail("Repair must not create business resources")

    monkeypatch.setattr(HurlAdapter, "request", request)
    await runner.repair({**runner.intent, "cases": []}, time.monotonic() + 10)
    assert not remaining
    assert sent[:2] == ["login", "login"]


async def test_regex_timeout_preserves_captured_ids_for_finally_cleanup(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    runner.plan.cases = runner.plan.cases[:1]
    runner.plan.cases[0].steps[1].assertions.append(
        Assertion(
            id="format",
            jsonPointer="/name",
            operator="regex",
            expected="(a+)+$",
            evidenceRefs=["contract"],
        )
    )
    resources, tokens = {"resource"}, {"private-token"}

    async def version(name):
        return name

    async def request(self, operation, base_url, request):
        match operation["definition"]["operationId"]:
            case "login":
                return Response(200, {}, {"token": "private-token"}, 1)
            case "createRecord":
                return Response(201, {}, {"id": "resource", "name": "a" * 10000 + "!"}, 1)
            case "deleteRecord":
                resources.clear()
                return Response(204, {}, None, 1)
            case "getRecord":
                return Response(404 if not resources else 200, {}, {}, 1)
            case "logout":
                tokens.clear()
                return Response(204, {}, None, 1)

    monkeypatch.setattr("horus.core.runner.check_engine", version)
    monkeypatch.setattr(HurlAdapter, "request", request)
    result = await runner.execute()
    assert result["verdict"] == "error"
    assert not resources and not tokens
    report = json.loads((runner.root / "report.json").read_bytes())
    assert report["cases"][0]["steps"][1]["reason"] == "regex_timeout"
    assert all(step["status"] == "passed" for step in report["cases"][0]["cleanup"])
