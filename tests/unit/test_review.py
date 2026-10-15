import json

import pytest

from horus.core.service import Service
from horus.core.store import HorusError


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    spec = {
        "openapi": "3.1.0", "info": {"title": "test", "version": "1"},
        "paths": {"/items": {"get": {"operationId": "listItems",
                    "responses": {"200": {"description": "OK"}}}}},
    }
    (root / "api.json").write_text(json.dumps(spec))
    service = Service(tmp_path / "workspace", read_roots=[root])
    project_ref = service.open_project("test", create_if_missing=True)["projectRef"]
    source = service.register_source(project_ref, "openapi", str(root / "api.json"))
    evidence = source["sourceRef"] + "#L1"
    plan = {
        "name": "list", "sourceRefs": [source["sourceRef"]],
        "businessUnderstanding": {
            "summary": "List items returns 200", "actors": [], "resources": [], "states": [],
            "flows": [{"id": "list", "description": "List", "operationIds": ["listItems"],
                       "basis": "contract", "evidenceRefs": [evidence]}],
            "rules": [], "dataDependencies": [], "assumptions": [], "conflicts": [], "unknowns": [],
        },
        "coverage": {"flows": [{"flowId": "list", "caseIds": ["list"]}],
                     "rules": [], "dataDependencies": [], "uncovered": []},
        "cases": [{"id": "list", "kind": "functional", "steps": [{
            "id": "read", "operationId": "listItems", "request": {},
            "assertions": [{"id": "status", "status": 200, "evidenceRefs": [evidence]}],
        }], "cleanup": []}],
    }
    config = {"name": "test", "environments": {"test": {"baseUrl": "http://127.0.0.1:34567"}},
              "policy": {"runAllow": ["listItems"], "cleanupAllow": []}}
    return service, project_ref, plan, config


def test_save_and_validation_do_not_approve(project):
    service, project_ref, plan, config = project
    plan_ref = service.save_plan(project_ref, plan)["planRef"]
    config_ref = service.stage_config(project_ref, config)["configRef"]
    bundle = service.prepare_review(project_ref, plan_ref, config_ref, "test")
    assert bundle["status"] == "awaiting_review"
    with pytest.raises(HorusError, match="review_required"):
        service.review_gate(project_ref, plan_ref, None, "test")


def test_approval_is_bound_to_displayed_bundle_and_target(project, monkeypatch):
    monkeypatch.setenv("HORUS_TEST_MODE", "1")
    service, project_ref, plan, config = project
    plan_ref = service.save_plan(project_ref, plan)["planRef"]
    config_ref = service.stage_config(project_ref, config)["configRef"]
    bundle = service.prepare_review(project_ref, plan_ref, config_ref, "test")
    service.get_artifact(project_ref, bundle["artifactRef"])
    decision = service.record_decision(project_ref, bundle["reviewBundleRef"], plan_ref,
                                       bundle["scopeHash"], "approved", "test-only", "unit-test")
    snapshot = service.review_gate(project_ref, plan_ref, decision["reviewRef"], "test")
    assert snapshot["configRef"] == config_ref
    plan["name"] = "changed"
    changed = service.save_plan(project_ref, plan)["planRef"]
    with pytest.raises(HorusError, match="review_mismatch"):
        service.review_gate(project_ref, changed, decision["reviewRef"], "test")


def test_test_only_review_is_rejected_in_normal_execution(project, monkeypatch):
    monkeypatch.delenv("HORUS_TEST_MODE", raising=False)
    service, project_ref, plan, config = project
    plan_ref = service.save_plan(project_ref, plan)["planRef"]
    config_ref = service.stage_config(project_ref, config)["configRef"]
    bundle = service.prepare_review(project_ref, plan_ref, config_ref, "test")
    service.get_artifact(project_ref, bundle["artifactRef"])
    decision = service.record_decision(project_ref, bundle["reviewBundleRef"], plan_ref,
                                       bundle["scopeHash"], "approved", "test-only", "unit-test")
    with pytest.raises(HorusError, match="test_review_not_allowed"):
        service.review_gate(project_ref, plan_ref, decision["reviewRef"], "test")


def test_unread_material_cannot_be_recorded_as_reviewed(project):
    service, project_ref, plan, config = project
    plan_ref = service.save_plan(project_ref, plan)["planRef"]
    config_ref = service.stage_config(project_ref, config)["configRef"]
    bundle = service.prepare_review(project_ref, plan_ref, config_ref, "test")
    with pytest.raises(HorusError, match="review_not_presented"):
        service.record_decision(project_ref, bundle["reviewBundleRef"], plan_ref,
                                bundle["scopeHash"], "approved", "conversation", "I approve")


def test_cross_project_sources_and_unregistered_paths_are_rejected(project, tmp_path):
    service, project_ref, plan, _ = project
    other = service.open_project("other", create_if_missing=True)["projectRef"]
    with pytest.raises(HorusError, match="asset_unavailable"):
        service.save_plan(other, plan)
    with pytest.raises(HorusError, match="read_root"):
        service.register_source(project_ref, "markdown", str(tmp_path / "outside.txt"))
