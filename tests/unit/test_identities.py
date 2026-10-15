from copy import deepcopy

import pytest

from horus.core.service import Service
from horus.core.store import HorusError
from tests.support import FIXTURES, reviewed_regression_plan


def identity_assets(tmp_path):
    service = Service(tmp_path, read_roots=[FIXTURES])
    project, plan_ref, review_ref = reviewed_regression_plan(service, "http://127.0.0.1:1")
    store = service.project(project)
    plan = store.get("plans", plan_ref)
    config = store.get("configs", store.get("reviews", review_ref)["configRef"])
    plan["cases"] = plan["cases"][:1]
    plan["coverage"]["flows"][0]["caseIds"] = ["lifecycle"]
    plan["coverage"]["rules"][0]["assertionRefs"] = ["lifecycle.read.value"]
    plan["identityProfiles"] = [
        {
            "id": identity,
            "description": identity,
            "credentialRefs": {
                "username": identity + "Username",
                "password": identity + "Password",
            },
            "evidenceRefs": [plan["sourceRefs"][1] + "#L1"],
        }
        for identity in ["owner", "outsider"]
    ]
    config["environments"]["test"]["secretBindings"] = {
        identity + suffix: "HORUS_" + identity.upper() + "_" + suffix.upper()
        for identity in ["owner", "outsider"]
        for suffix in ["Username", "Password"]
    }
    case = plan["cases"][0]
    for step in [*case["steps"], *case["cleanup"]]:
        step["identityRef"] = "owner"
    outsider_login = deepcopy(case["steps"][0])
    outsider_login.update(id="outsider-login", identityRef="outsider")
    case["steps"].insert(2, outsider_login)
    outsider_logout = deepcopy(case["cleanup"][-1])
    outsider_logout.update(id="outsider-logout", identityRef="outsider")
    case["cleanup"].append(outsider_logout)
    return service, project, plan, config


def test_identity_aliases_and_shared_resource_ids_validate_before_review(tmp_path):
    service, project, plan, config = identity_assets(tmp_path)
    saved = service.save_plan(project, plan)
    config_ref = service.stage_config(project, config)["configRef"]
    assert (
        service.prepare_review(project, saved["planRef"], config_ref, "test")["status"]
        == "awaiting_review"
    )


@pytest.mark.parametrize("failure", ["unknown", "duplicate", "borrow-token", "wrong-alias"])
def test_invalid_identity_or_cross_identity_reference_is_rejected(tmp_path, failure):
    service, project, plan, _ = identity_assets(tmp_path)
    match failure:
        case "unknown":
            plan["cases"][0]["steps"][-1]["identityRef"] = "missing"
            error = "unknown_identity"
        case "duplicate":
            plan["identityProfiles"].append(deepcopy(plan["identityProfiles"][0]))
            error = "duplicate_identity"
        case "borrow-token":
            plan["cases"][0]["steps"].pop(2)
            plan["cases"][0]["steps"][-1]["identityRef"] = "outsider"
            error = "missing_variable_source"
        case "wrong-alias":
            plan["cases"][0]["steps"][0]["request"]["body"]["password"] = "${secrets.ownerPassword}"
            error = "missing_secret_binding"
    with pytest.raises(HorusError, match=error):
        service.save_plan(project, plan)


def test_profile_cannot_reference_missing_environment_credentials(tmp_path):
    service, project, plan, config = identity_assets(tmp_path)
    saved = service.save_plan(project, plan)
    del config["environments"]["test"]["secretBindings"]["outsiderPassword"]
    config_ref = service.stage_config(project, config)["configRef"]
    with pytest.raises(HorusError, match="missing_identity_binding"):
        service.prepare_review(project, saved["planRef"], config_ref, "test")


def test_runtime_context_keeps_tokens_private_and_resource_ids_shared(tmp_path):
    from horus.core.models import Capture, Plan
    from horus.core.values import CaseContext

    _, _, raw, _ = identity_assets(tmp_path)
    profiles = {profile.id: profile for profile in Plan.model_validate(raw).identityProfiles}
    context = CaseContext(
        run_id="run-example",
        profiles=profiles,
        secrets={
            "ownerUsername": "alice",
            "ownerPassword": "alice-password",
            "outsiderUsername": "bob",
            "outsiderPassword": "bob-password",
        },
    )
    token = Capture(jsonPointer="/token", secret=True, purpose="authentication")
    context.capture("owner", "accessToken", token, "alice-token")
    context.capture("outsider", "accessToken", token, "bob-token")
    context.capture(
        "owner",
        "ticketId",
        Capture(jsonPointer="/id", secret=False, purpose="resource-cleanup"),
        "ticket",
    )
    assert context.view("owner")["vars"] == {"ticketId": "ticket", "accessToken": "alice-token"}
    assert context.view("outsider")["vars"] == {"ticketId": "ticket", "accessToken": "bob-token"}
    assert "accessToken" not in context.view(None)["vars"]
    assert context.view("owner")["secrets"] == {"username": "alice", "password": "alice-password"}
    assert context.view("outsider")["secrets"] == {"username": "bob", "password": "bob-password"}
    context.capture(
        "owner",
        "sensitiveId",
        Capture(jsonPointer="/id", secret=True, purpose="resource-cleanup"),
        "private-id",
    )
    assert context.view("outsider")["vars"]["sensitiveId"] == "private-id"
