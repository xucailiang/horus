"""Deterministic workflow regressions. Never supplied as an answer to native Agent acceptance."""

from horus.core.service import Service
from tests.support import FIXTURES


def reviewed_workflow_plan(service: Service, base_url: str):
    project = service.open_project("workflow-regression", create_if_missing=True)["projectRef"]
    spec = service.register_source(project, "openapi", str(FIXTURES / "workflow-api/openapi.yaml"))
    contract = service.register_source(
        project, "markdown", str(FIXTURES / "workflow-api/requirements.md")
    )
    evidence = [contract["sourceRef"] + "#L1"]
    headers = {"Authorization": "Bearer ${vars.accessToken}"}
    resource = {"headers": headers, "pathParams": {"id": "${vars.ticketId}"}}

    def status(code):
        return {"id": "status", "status": code, "evidenceRefs": evidence}

    def login(identity):
        return {
            "id": "login-" + identity,
            "identityRef": identity,
            "operationId": "login",
            "role": "authentication",
            "request": {
                "body": {"username": "${secrets.username}", "password": "${secrets.password}"}
            },
            "captures": {
                "accessToken": {
                    "jsonPointer": "/token",
                    "secret": True,
                    "purpose": "authentication",
                }
            },
            "assertions": [status(200)],
        }

    def create(prefix):
        return {
            "id": "create",
            "identityRef": "owner",
            "operationId": "createTicket",
            "role": "prepare",
            "request": {"headers": headers, "body": {"title": prefix + "-${run.id}"}},
            "captures": {
                "ticketId": {"jsonPointer": "/id", "secret": False, "purpose": "resource-cleanup"}
            },
            "assertions": [status(201)],
        }

    def cleanup():
        return [
            {
                "id": "delete",
                "identityRef": "owner",
                "operationId": "deleteTicket",
                "request": resource,
                "requires": ["ticketId", "accessToken"],
                "assertions": [status(204)],
            },
            {
                "id": "verify-deleted",
                "identityRef": "owner",
                "operationId": "getTicket",
                "request": resource,
                "requires": ["ticketId", "accessToken"],
                "assertions": [status(404)],
            },
            *[
                {
                    "id": "logout-" + identity,
                    "identityRef": identity,
                    "operationId": "logout",
                    "request": {"headers": headers},
                    "requires": ["accessToken"],
                    "assertions": [status(204)],
                }
                for identity in ["owner", "outsider"]
            ],
        ]

    plan = {
        "name": "ticket-ownership-and-load",
        "sourceRefs": [spec["sourceRef"], contract["sourceRef"]],
        "identityProfiles": [
            {
                "id": identity,
                "description": description,
                "credentialRefs": {
                    "username": identity + "Username",
                    "password": identity + "Password",
                },
                "evidenceRefs": evidence,
            }
            for identity, description in [
                ("owner", "Ticket creator"),
                ("outsider", "Another account"),
            ]
        ],
        "businessUnderstanding": {
            "summary": "Owners can edit their drafts; other accounts must receive 403.",
            "actors": [
                {"id": "owner", "description": "Ticket creator"},
                {"id": "outsider", "description": "Another account"},
            ],
            "resources": [{"id": "ticket", "description": "An isolated draft ticket"}],
            "states": [
                {
                    "resourceId": "ticket",
                    "values": ["draft", "processing", "submitted"],
                    "evidenceRefs": evidence,
                }
            ],
            "flows": [
                {
                    "id": "ownership",
                    "description": "Create, deny outsider access, resume as owner, clean up",
                    "operationIds": [
                        "login",
                        "createTicket",
                        "getTicket",
                        "updateTicket",
                        "deleteTicket",
                        "logout",
                    ],
                    "basis": "contract",
                    "evidenceRefs": evidence,
                }
            ],
            "rules": [
                {
                    "id": "private-ticket",
                    "description": "Other accounts cannot read or change a ticket",
                    "operationIds": ["getTicket", "updateTicket"],
                    "basis": "contract",
                    "evidenceRefs": evidence,
                }
            ],
            "dataDependencies": [
                {
                    "id": "ticket-id",
                    "producer": "createTicket",
                    "sourceJsonPointer": "/id",
                    "consumers": ["getTicket.pathParams.id", "deleteTicket.pathParams.id"],
                    "purpose": "Shared resource identifier; authentication remains private",
                    "evidenceRefs": evidence,
                }
            ],
            "assumptions": [],
            "conflicts": [],
            "unknowns": [],
        },
        "coverage": {
            "flows": [{"flowId": "ownership", "caseIds": ["ownership", "load"]}],
            "rules": [
                {
                    "ruleId": "private-ticket",
                    "assertionRefs": [
                        "ownership.outsider-read.status",
                        "ownership.outsider-edit.status",
                    ],
                }
            ],
            "dataDependencies": [
                {
                    "dependencyId": "ticket-id",
                    "captureRef": "ownership.create.ticketId",
                    "consumerStepRefs": [
                        "ownership.outsider-read",
                        "ownership.owner-read",
                        "ownership.delete",
                    ],
                }
            ],
            "uncovered": [
                {
                    "itemId": "submission",
                    "reason": "Asynchronous submission and state rules need branch/poll support",
                }
            ],
        },
        "cases": [
            {
                "id": "ownership",
                "kind": "functional",
                "steps": [
                    login("owner"),
                    create("draft"),
                    login("outsider"),
                    {
                        "id": "outsider-read",
                        "identityRef": "outsider",
                        "operationId": "getTicket",
                        "request": resource,
                        "negative": True,
                        "assertions": [status(403)],
                    },
                    {
                        "id": "outsider-edit",
                        "identityRef": "outsider",
                        "operationId": "updateTicket",
                        "request": {**resource, "body": {"title": "unauthorized"}},
                        "negative": True,
                        "assertions": [status(403)],
                    },
                    {
                        "id": "owner-edit",
                        "identityRef": "owner",
                        "operationId": "updateTicket",
                        "request": {**resource, "body": {"title": "revised-${run.id}"}},
                        "assertions": [status(200)],
                    },
                    {
                        "id": "owner-read",
                        "identityRef": "owner",
                        "operationId": "getTicket",
                        "request": resource,
                        "assertions": [
                            status(200),
                            {
                                "id": "title",
                                "jsonPointer": "/title",
                                "expected": "revised-${run.id}",
                                "evidenceRefs": evidence,
                            },
                        ],
                    },
                ],
                "cleanup": cleanup(),
            },
            {
                "id": "load",
                "kind": "performance",
                "steps": [
                    login("owner"),
                    create("load"),
                    login("outsider"),
                    {
                        "kind": "load",
                        "id": "read",
                        "identityRef": "owner",
                        "operationId": "getTicket",
                        "request": resource,
                        "assertions": [
                            status(200),
                            {
                                "id": "title",
                                "jsonPointer": "/title",
                                "expected": "load-${run.id}",
                                "evidenceRefs": evidence,
                            },
                        ],
                        "loadProfile": {"vus": 2, "iterations": 20, "maxDurationMs": 5000},
                        "thresholds": {
                            "httpErrorRate": {"lte": 0},
                            "dataCheckFailureRate": {"lte": 0},
                            "completedIterations": {"eq": 20},
                        },
                    },
                ],
                "cleanup": cleanup(),
            },
        ],
    }
    config = {
        "name": "workflow",
        "environments": {
            "test": {
                "baseUrl": base_url,
                "secretBindings": {
                    identity + suffix: "HORUS_" + identity.upper() + "_" + suffix.upper()
                    for identity in ["owner", "outsider"]
                    for suffix in ["Username", "Password"]
                },
            }
        },
        "policy": {
            "runAllow": [
                "login",
                "logout",
                "createTicket",
                "getTicket",
                "updateTicket",
                "deleteTicket",
            ],
            "cleanupAllow": ["login", "logout", "getTicket", "deleteTicket"],
            "loadAllow": ["getTicket"],
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
        "workflow identity regression",
    )
    return project, plan_ref, review["reviewRef"]
