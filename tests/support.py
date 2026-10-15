from pathlib import Path

from horus.core.service import Service

FIXTURES = Path(__file__).parent / "fixtures"


def reviewed_regression_plan(service: Service, base_url: str):
    """Deterministic engine regression input; never evidence of Agent generation/human review."""
    project = service.open_project("engine-regression", create_if_missing=True)["projectRef"]
    spec = service.register_source(project, "openapi", str(FIXTURES / "reference-api/openapi.yaml"))
    contract = service.register_source(
        project, "markdown", str(FIXTURES / "reference-api/requirements.md")
    )
    basis = [contract["sourceRef"] + "#L1"]

    def status(name, code):
        return {"id": name, "status": code, "evidenceRefs": basis}

    headers = {"Authorization": "Bearer ${vars.accessToken}"}
    query = {"headers": headers, "pathParams": {"id": "${vars.recordId}"}}
    cases = []
    for name, kind in [("lifecycle", "functional"), ("load", "performance")]:
        read = {
            "id": "read",
            "operationId": "getRecord",
            "request": query,
            "assertions": [
                status("status", 200),
                {"id": "value", "jsonPointer": "/value", "expected": 42, "evidenceRefs": basis},
            ],
        }
        if kind == "performance":
            read.update(
                {
                    "kind": "load",
                    "loadProfile": {"vus": 5, "iterations": 100, "maxDurationMs": 10000},
                    "thresholds": {
                        "latencyP95Ms": {"lte": 1000},
                        "httpErrorRate": {"lte": 0},
                        "dataCheckFailureRate": {"lte": 0},
                        "completedIterations": {"eq": 100},
                    },
                }
            )
        cases.append(
            {
                "id": name,
                "kind": kind,
                "steps": [
                    {
                        "id": "login",
                        "operationId": "login",
                        "role": "authentication",
                        "request": {
                            "body": {
                                "username": "${secrets.username}",
                                "password": "${secrets.password}",
                            }
                        },
                        "captures": {
                            "accessToken": {
                                "jsonPointer": "/token",
                                "secret": True,
                                "purpose": "authentication",
                            }
                        },
                        "assertions": [status("status", 200)],
                    },
                    {
                        "id": "create",
                        "operationId": "createRecord",
                        "role": "prepare",
                        "request": {
                            "headers": headers,
                            "body": {"name": "ci-${run.id}", "value": 42},
                        },
                        "captures": {
                            "recordId": {
                                "jsonPointer": "/id",
                                "secret": False,
                                "purpose": "resource-cleanup",
                            }
                        },
                        "assertions": [status("status", 201)],
                    },
                    read,
                ],
                "cleanup": [
                    {
                        "id": "delete",
                        "operationId": "deleteRecord",
                        "request": query,
                        "requires": ["accessToken", "recordId"],
                        "assertions": [status("status", 204)],
                    },
                    {
                        "id": "verify-deleted",
                        "operationId": "getRecord",
                        "request": query,
                        "requires": ["accessToken", "recordId"],
                        "assertions": [status("status", 404)],
                    },
                    {
                        "id": "logout",
                        "operationId": "logout",
                        "request": {"headers": headers},
                        "requires": ["accessToken"],
                        "assertions": [status("status", 204)],
                    },
                ],
            }
        )
    plan = {
        "name": "reference-lifecycle-and-load",
        "sourceRefs": [spec["sourceRef"], contract["sourceRef"]],
        "businessUnderstanding": {
            "summary": "Authenticate, create isolated records, verify values, clean up.",
            "actors": [],
            "resources": [],
            "states": [],
            "flows": [
                {
                    "id": "lifecycle",
                    "description": "Isolated lifecycle and load",
                    "operationIds": [
                        "login",
                        "createRecord",
                        "getRecord",
                        "deleteRecord",
                        "logout",
                    ],
                    "basis": "contract",
                    "evidenceRefs": basis,
                }
            ],
            "rules": [
                {
                    "id": "value",
                    "description": "Reads preserve the written value",
                    "operationIds": ["createRecord", "getRecord"],
                    "basis": "contract",
                    "evidenceRefs": basis,
                }
            ],
            "dataDependencies": [],
            "assumptions": [],
            "conflicts": [],
            "unknowns": [],
        },
        "coverage": {
            "flows": [{"flowId": "lifecycle", "caseIds": ["lifecycle", "load"]}],
            "rules": [
                {"ruleId": "value", "assertionRefs": ["lifecycle.read.value", "load.read.value"]}
            ],
            "dataDependencies": [],
            "uncovered": [],
        },
        "cases": cases,
    }
    config = {
        "name": "reference",
        "environments": {
            "test": {
                "baseUrl": base_url,
                "secretBindings": {"username": "HORUS_USERNAME", "password": "HORUS_PASSWORD"},
            }
        },
        "policy": {
            "runAllow": ["login", "logout", "createRecord", "getRecord", "deleteRecord"],
            "cleanupAllow": ["login", "logout", "deleteRecord", "getRecord"],
            "loadAllow": ["getRecord"],
        },
    }
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
        "regression fixture",
    )
    return project, plan_ref, review["reviewRef"]
