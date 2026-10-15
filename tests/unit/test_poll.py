import asyncio
import json
from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError

from horus.adapters.hurl import Response
from horus.core.models import PollStep, Predicate
from horus.core.predicates import evaluate
from horus.core.reports import verdict, write_report
from horus.core.service import Service
from horus.core.store import HorusError
from tests.support import FIXTURES, reviewed_regression_plan
from tests.unit.test_execution_safety import prepared_runner


def comparison(left=None, right=None, operator="eq"):
    return {
        "kind": "compare",
        "left": left or {"kind": "response", "jsonPointer": "/state"},
        "operator": operator,
        "right": right or {"kind": "literal", "value": "done"},
        "evidenceRefs": ["contract"],
    }


def poll_raw():
    return {
        "kind": "poll",
        "id": "wait",
        "requestStep": {
            "id": "observe",
            "operationId": "getRecord",
            "request": {},
            "captures": {
                "resultId": {"jsonPointer": "/id", "secret": False, "purpose": "case-data"}
            },
            "assertions": [
                {"id": "status", "status": 200, "evidenceRefs": ["contract"]},
                {"id": "result", "jsonPointer": "/value", "expected": 42,
                 "evidenceRefs": ["contract"]},
            ],
        },
        "until": comparison(),
        "intervalMs": 1,
        "maxAttempts": 3,
        "timeoutMs": 1000,
    }


def test_predicates_compare_typed_values_and_compose_without_scripts():
    raw = {"kind": "all", "predicates": [
        comparison(),
        comparison({"kind": "variable", "name": "id"},
                   {"kind": "response", "jsonPointer": "/id"}),
        {"kind": "not", "predicate": comparison(
            {"kind": "literal", "value": True}, {"kind": "literal", "value": 1})},
        {"kind": "any", "predicates": [comparison(), comparison(
            {"kind": "response", "status": True}, {"kind": "literal", "value": 202})]},
    ]}
    predicate = TypeAdapter(Predicate).validate_python(raw)
    assert evaluate(predicate, {"id": "resource"}, Response(200, {}, {
        "id": "resource", "state": "done"}, 1))
    assert not evaluate(predicate, {"id": "other"}, Response(200, {}, {
        "id": "resource", "state": "pending"}, 1))
    with pytest.raises(HorusError, match="predicate_input_missing"):
        evaluate(predicate, {}, Response(200, {}, {"state": "done"}, 1))


@pytest.mark.parametrize("change", ["zero-attempts", "zero-interval", "no-status", "deep"])
def test_poll_contract_rejects_unbounded_or_ambiguous_observation(change):
    raw = poll_raw()
    if change == "zero-attempts":
        raw["maxAttempts"] = 0
    elif change == "zero-interval":
        raw["intervalMs"] = 0
    elif change == "no-status":
        raw["requestStep"]["assertions"].pop(0)
    else:
        for _ in range(10):
            raw["until"] = {"kind": "not", "predicate": raw["until"]}
    with pytest.raises(ValidationError):
        PollStep.model_validate(raw)


@pytest.mark.parametrize("outcome", ["ready", "exhausted", "http-error", "network", "final-fail"])
async def test_poll_exports_only_final_captures_and_stops_on_errors(
    tmp_path, monkeypatch, outcome
):
    runner = prepared_runner(tmp_path, monkeypatch)
    context = runner.context()
    attempts = []

    async def request(self, operation, base_url, request):
        assert "resultId" not in context.shared
        attempts.append(1)
        if outcome == "network":
            raise HorusError("hurl_request_error")
        ready = len(attempts) == 2 and outcome in {"ready", "final-fail"}
        return Response(503 if outcome == "http-error" else 200, {}, {
            "id": "resource", "state": "done" if ready else "pending",
            "value": 41 if outcome == "final-fail" else 42,
        }, 1)

    monkeypatch.setattr("horus.adapters.hurl.HurlAdapter.request", request)
    result = await runner.step(PollStep.model_validate(poll_raw()), context, case_id="poll")
    expected = {"ready": "passed", "exhausted": "failed", "http-error": "failed",
                "network": "error", "final-fail": "failed"}[outcome]
    assert result["status"] == expected
    assert len(attempts) == {"ready": 2, "final-fail": 2, "exhausted": 3}.get(outcome, 1)
    assert ("resultId" in context.shared) == (outcome in {"ready", "final-fail"})
    if outcome == "exhausted":
        assert result["reason"] == "poll_attempts_exhausted"
        assert all(o["status"] == "observed" for o in result["observations"])
    if outcome == "ready":
        assert [o["matched"] for o in result["observations"]] == [False, True]
        assert result["assertions"][-1]["status"] == "passed"
    assert all(entry["stepId"] == "wait" for entry in runner.ledger)


def test_predicate_missing_is_explicit_except_for_an_existence_check():
    adapter = TypeAdapter(Predicate)
    response = Response(200, {}, {}, 1)
    with pytest.raises(HorusError, match="predicate_input_missing"):
        evaluate(adapter.validate_python(comparison()), {}, response)
    absent = comparison(right={"kind": "literal", "value": False}, operator="exists")
    assert evaluate(adapter.validate_python(absent), {}, response)
    invalid = deepcopy(absent)
    invalid["operator"] = "gte"
    with pytest.raises(HorusError, match="predicate_input_missing"):
        evaluate(adapter.validate_python(invalid), {}, response)


def poll_assets(tmp_path):
    service = Service(tmp_path, read_roots=[FIXTURES])
    project, ref, review = reviewed_regression_plan(service, "http://127.0.0.1:1")
    store = service.project(project)
    plan = store.get("plans", ref)
    config = store.get("configs", store.get("reviews", review)["configRef"])
    raw = poll_raw()
    raw["id"] = "read"
    raw["requestStep"] = plan["cases"][0]["steps"][2]
    raw["until"]["left"]["jsonPointer"] = "/value"
    raw["until"]["right"]["value"] = 42
    raw["until"]["evidenceRefs"] = raw["requestStep"]["assertions"][0]["evidenceRefs"]
    plan["cases"][0]["steps"][2] = raw
    config["policy"]["pollAllow"] = ["getRecord"]
    return service, project, plan, config


def test_poll_review_counts_every_attempt_and_requires_explicit_read_permission(tmp_path):
    service, project, plan, config = poll_assets(tmp_path)
    saved = service.save_plan(project, plan)
    assert saved["maxRequests"] == 113
    ref = service.stage_config(project, config)["configRef"]
    bundle = service.prepare_review(project, saved["planRef"], ref, "test")
    assert bundle["validation"]["maxRequests"] == 113
    config["policy"]["pollAllow"] = []
    ref = service.stage_config(project, config)["configRef"]
    with pytest.raises(HorusError, match="poll_operation_denied"):
        service.prepare_review(project, saved["planRef"], ref, "test")


@pytest.mark.parametrize("failure", ["variable", "provisional", "request-budget", "time-budget"])
def test_poll_rejects_unknown_inputs_and_excessive_review_scope(tmp_path, failure):
    service, project, plan, config = poll_assets(tmp_path)
    poll = plan["cases"][0]["steps"][2]
    if failure in {"variable", "provisional"}:
        name = "missing" if failure == "variable" else "later"
        poll["until"]["right"] = {"kind": "variable", "name": name}
        if failure == "provisional":
            poll["requestStep"]["captures"] = {name: {
                "jsonPointer": "/value", "secret": False, "purpose": "case-data",
            }}
        with pytest.raises(HorusError, match="missing_variable_source"):
            service.save_plan(project, plan)
    else:
        if failure == "request-budget":
            config["policy"]["limits"] = {"maxRequestsPerRun": 112}
            expected = "request_budget_exceeded"
        else:
            config["policy"]["limits"] = {"maxRunDurationMs": 500}
            expected = "run_duration_budget_exceeded"
        saved = service.save_plan(project, plan)
        ref = service.stage_config(project, config)["configRef"]
        with pytest.raises(HorusError, match=expected):
            service.prepare_review(project, saved["planRef"], ref, "test")


@pytest.mark.parametrize("outcome", ["timeout", "cancelled", "private"])
async def test_poll_bounds_waits_and_redacts_observations(tmp_path, monkeypatch, outcome):
    runner = prepared_runner(tmp_path, monkeypatch)
    raw = poll_raw()
    raw.update(maxAttempts=10, intervalMs=1000, timeoutMs=30)
    if outcome == "private":
        raw["requestStep"]["captures"]["resultId"]["secret"] = True
        raw["until"] = comparison({"kind": "response", "jsonPointer": "/id"},
                                  {"kind": "literal", "value": "private-result"})
        runner.redactor.register(True)  # Trusted boolean result fields must remain intact.
    observed = asyncio.Event()

    async def request(self, operation, base_url, request):
        observed.set()
        return Response(200, {}, {"id": "private-result", "state": "pending", "value": 42}, 1)

    monkeypatch.setattr("horus.adapters.hurl.HurlAdapter.request", request)
    execution = asyncio.create_task(runner.step(
        PollStep.model_validate(raw), runner.context(), case_id="poll"))
    await observed.wait()
    if outcome == "cancelled":
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
    else:
        result = await asyncio.wait_for(execution, 1)
        assert result["attempts"] == 1
        if outcome == "timeout":
            assert result["status"] == "failed" and result["reason"] == "poll_timeout"
        else:
            assert result["status"] == "passed"
            assert result["observations"][0]["matched"] is True
            assert result["observations"][0]["conditions"][0]["matched"] is True
            assert "private-result" not in (runner.root / "events.jsonl").read_text()
    assert not runner.active_poll


def test_intermediate_observations_are_not_passing_tests(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    cases = [{"id": "poll", "steps": [{
        "id": "wait", "kind": "poll-attempt", "attempt": 1, "status": "observed",
    }], "cleanup": []}]
    assert verdict(cases) == "inconclusive"
    result = write_report(runner.service, runner.root, {**runner.intent, "cases": cases,
                                                     "status": "interrupted"})
    assert result["counts"]["steps"] == 0
    assert json.loads((runner.root / "report.json").read_bytes())["verdict"] == "inconclusive"
