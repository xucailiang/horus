import asyncio
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from horus.adapters.hurl import Response
from horus.core.models import BranchStep
from horus.core.runner import Runner, write_report
from horus.core.store import HorusError, atomic_write
from tests.unit.test_execution_safety import prepared_runner
from tests.unit.test_identities import identity_assets
from tests.unit.test_poll import poll_assets


def branch_assets(tmp_path):
    service, project, plan, config = poll_assets(tmp_path)
    read = plan["cases"][0]["steps"][2]["requestStep"]
    read["captures"] = {
        "answer": {"jsonPointer": "/value", "secret": False, "purpose": "case-data"}
    }
    later = deepcopy(read)
    later.update(id="after", captures={})
    later["assertions"][1]["expected"] = "${vars.answer}"
    branch = {
        "kind": "branch",
        "id": "choose",
        "identityRef": None,
        "predicate": {
            "kind": "compare",
            "left": {"kind": "variable", "name": "recordId"},
            "operator": "exists",
            "right": {"kind": "literal", "value": True},
            "evidenceRefs": read["assertions"][0]["evidenceRefs"],
        },
        "thenSteps": [read],
        "elseSteps": [{**deepcopy(read), "id": "read-" + str(i)} for i in range(3)],
        "outputs": [{"name": "answer"}],
    }
    plan["cases"][0]["steps"][2:] = [branch, later]
    plan["coverage"]["rules"][0]["assertionRefs"] = [
        "lifecycle.choose.then.read.value",
        "load.read.value",
    ]
    return service, project, plan, config


def test_both_paths_are_reviewed_and_budget_uses_the_longer_path(tmp_path):
    service, project, plan, config = branch_assets(tmp_path)
    saved = service.save_plan(project, plan)
    assert saved["maxRequests"] == 114
    config_ref = service.stage_config(project, config)["configRef"]
    bundle = service.prepare_review(project, saved["planRef"], config_ref, "test")
    assert bundle["validation"]["maxRequests"] == 114
    config["policy"]["limits"] = {"maxRequestsPerRun": 113}
    config_ref = service.stage_config(project, config)["configRef"]
    with pytest.raises(HorusError, match="request_budget_exceeded"):
        service.prepare_review(project, saved["planRef"], config_ref, "test")


@pytest.mark.parametrize("failure", ["one-sided", "hidden-input", "hidden-policy", "response"])
def test_branch_rejects_unsafe_outputs_and_unselected_actions(tmp_path, failure):
    service, project, plan, config = branch_assets(tmp_path)
    branch = plan["cases"][0]["steps"][2]
    if failure == "one-sided":
        for step in branch["elseSteps"]:
            step["captures"] = {}
        with pytest.raises(HorusError, match="branch_output_not_definite"):
            service.save_plan(project, plan)
    elif failure == "hidden-input":
        branch["elseSteps"][0]["request"]["query"] = {"q": "${vars.notKnown}"}
        with pytest.raises(HorusError, match="missing_variable_source"):
            service.save_plan(project, plan)
    elif failure == "hidden-policy":
        branch["elseSteps"][0]["operationId"] = "deleteRecord"
        config["policy"]["runAllow"].remove("deleteRecord")
        ref = service.save_plan(project, plan)["planRef"]
        config_ref = service.stage_config(project, config)["configRef"]
        with pytest.raises(HorusError, match="operation_denied"):
            service.prepare_review(project, ref, config_ref, "test")
    else:
        branch["predicate"]["left"] = {"kind": "response", "status": True}
        with pytest.raises(ValidationError):
            service.save_plan(project, plan)


def test_path_specific_capture_requires_a_guard_outside_its_branch(tmp_path):
    service, project, plan, _ = branch_assets(tmp_path)
    branch = plan["cases"][0]["steps"][2]
    branch["outputs"] = []
    for step in branch["elseSteps"]:
        step["captures"] = {}
    with pytest.raises(HorusError, match="missing_variable_source"):
        service.save_plan(project, plan)
    plan["cases"][0]["steps"][3]["requires"] = ["answer"]
    assert service.save_plan(project, plan)["status"] == "validated"


@pytest.mark.parametrize("selected", [True, False])
async def test_runner_executes_only_the_selected_path_and_reports_skips(
    tmp_path, monkeypatch, selected
):
    runner = prepared_runner(tmp_path, monkeypatch)
    _, _, plan, _ = branch_assets(tmp_path / "plan")
    raw = plan["cases"][0]["steps"][2]
    raw["predicate"]["left"] = {"kind": "literal", "value": selected}
    raw["predicate"]["operator"] = "eq"
    calls = []

    async def request(self, operation, base_url, request):
        calls.append(operation)
        return Response(200, {}, {"value": 42}, 1)

    monkeypatch.setattr("horus.adapters.hurl.HurlAdapter.request", request)
    context = runner.context(shared={"recordId": "resource", "accessToken": "private"})
    result = await runner.step(BranchStep.model_validate(raw), context, case_id="lifecycle")
    assert result["status"] == "passed"
    assert result["selected"] == ("then" if selected else "else")
    assert len(calls) == (1 if selected else 3)
    assert context.shared["answer"] == 42
    assert sum(step["status"] == "skipped" for step in result["steps"]) == (3 if selected else 1)
    assert all(
        step.get("reason") == "not_selected"
        for step in result["steps"]
        if step["status"] == "skipped"
    )
    assert all(
        entry["stepId"].startswith("choose." + result["selected"] + ".") for entry in runner.ledger
    )


async def test_branch_timeout_blocks_later_write_and_preserves_cleanup(tmp_path, monkeypatch):
    original = prepared_runner(tmp_path, monkeypatch)
    service, project, plan, config = branch_assets(tmp_path)
    plan["cases"] = plan["cases"][:1]
    plan["coverage"]["flows"][0]["caseIds"] = ["lifecycle"]
    plan["coverage"]["rules"][0]["assertionRefs"] = ["lifecycle.choose.then.read.value"]
    evidence = plan["cases"][0]["steps"][0]["assertions"][0]["evidenceRefs"]
    plan["cases"][0]["steps"][-1] = {
        "id": "later-write",
        "operationId": "createRecord",
        "request": {},
        "assertions": [{"id": "status", "status": 201, "evidenceRefs": evidence}],
    }
    config["policy"]["limits"] = {"maxRunDurationMs": 50}
    plan_ref = service.save_plan(project, plan)["planRef"]
    config_ref = service.stage_config(project, config)["configRef"]
    bundle = service.prepare_review(project, plan_ref, config_ref, "test")
    service.get_artifact(project, bundle["artifactRef"])
    review = service.record_decision(
        project,
        bundle["reviewBundleRef"],
        plan_ref,
        bundle["scopeHash"],
        "approved",
        "test-only",
        "timeout regression",
    )
    atomic_write(
        original.root / "intent.json",
        {
            **original.intent,
            "planRef": plan_ref,
            "reviewRef": review["reviewRef"],
            "configRef": config_ref,
        },
    )
    runner = Runner(service, project, original.run_id, original.root)
    writes = []

    async def version(name):
        return name

    async def request(self, operation, base_url, request):
        match operation["definition"]["operationId"]:
            case "login":
                return Response(200, {}, {"token": "private"}, 1)
            case "createRecord":
                writes.append(1)
                return Response(201, {}, {"id": "resource", "value": 42}, 1)
            case "getRecord":
                if not any(e["cleanup"] for e in runner.ledger):
                    await asyncio.sleep(0.2)
                return Response(404, {}, None, 1)
            case _:
                return Response(204, {}, None, 1)

    monkeypatch.setattr("horus.core.runner.check_engine", version)
    monkeypatch.setattr("horus.adapters.hurl.HurlAdapter.request", request)
    result = await runner.execute()
    assert result["verdict"] == "inconclusive"
    assert len(writes) == 1
    report = json.loads((runner.root / "report.json").read_bytes())
    assert report["cases"][0]["steps"][-1]["status"] == "blocked"
    assert all(step["status"] == "passed" for step in report["cases"][0]["cleanup"])


def test_report_handles_a_poll_in_the_unselected_branch(tmp_path, monkeypatch):
    runner = prepared_runner(tmp_path, monkeypatch)
    report = write_report(
        runner.service,
        runner.root,
        {
            **runner.intent,
            "status": "completed",
            "cases": [
                {
                    "id": "route",
                    "cleanup": [],
                    "steps": [
                        {
                            "id": "choice",
                            "kind": "branch",
                            "status": "passed",
                            "selected": "then",
                            "steps": [
                                {"id": "choice.then.read", "status": "passed"},
                                {
                                    "id": "choice.else.wait",
                                    "kind": "poll",
                                    "status": "skipped",
                                    "reason": "not_selected",
                                },
                            ],
                        }
                    ],
                }
            ],
        },
    )
    assert report["verdict"] == "passed" and report["counts"]["steps"] == 2


def test_exists_condition_guards_a_path_specific_value(tmp_path):
    service, project, plan, _ = branch_assets(tmp_path)
    branch, after = plan["cases"][0]["steps"][2:]
    branch["outputs"] = []
    for step in branch["elseSteps"]:
        step["captures"] = {}
    plan["cases"][0]["steps"][-1] = {
        "kind": "branch",
        "id": "guard",
        "predicate": {
            **deepcopy(branch["predicate"]),
            "left": {"kind": "variable", "name": "answer"},
        },
        "thenSteps": [after],
        "elseSteps": [],
        "outputs": [],
    }
    assert service.save_plan(project, plan)["status"] == "validated"


def test_nested_branch_references_and_outputs_are_checked(tmp_path):
    service, project, plan, _ = branch_assets(tmp_path)
    branch = plan["cases"][0]["steps"][2]
    inner = deepcopy(branch)
    inner["id"] = "inner"
    branch["thenSteps"] = [inner]
    plan["coverage"]["rules"][0]["assertionRefs"][0] = "lifecycle.choose.then.inner.then.read.value"
    assert service.save_plan(project, plan)["maxRequests"] == 114
    inner["elseSteps"][0]["request"]["query"] = {"q": "${vars.notKnown}"}
    with pytest.raises(HorusError, match="missing_variable_source"):
        service.save_plan(project, plan)


def test_authentication_outputs_are_definite_for_the_same_identity(tmp_path):
    service, project, plan, _ = identity_assets(tmp_path)
    case = plan["cases"][0]
    owner = case["steps"][0]
    outsider = case["steps"].pop(2)
    evidence = owner["assertions"][0]["evidenceRefs"]
    case["steps"][0] = {
        "kind": "branch",
        "id": "auth",
        "predicate": {
            "kind": "compare",
            "left": {"kind": "literal", "value": True},
            "operator": "eq",
            "right": {"kind": "literal", "value": True},
            "evidenceRefs": evidence,
        },
        "thenSteps": [owner],
        "elseSteps": [outsider],
        "outputs": [{"name": "accessToken", "identityRef": "owner"}],
    }
    with pytest.raises(HorusError, match="branch_output_not_definite"):
        service.save_plan(project, plan)
