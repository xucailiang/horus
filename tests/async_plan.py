"""Fixed asynchronous workflow regression input; not native Agent acceptance evidence."""

from horus.core.service import Service
from tests.workflow_plan import reviewed_workflow_plan


def reviewed_async_plan(service: Service, base_url: str):
    project, original, review = reviewed_workflow_plan(service, base_url)
    store = service.project(project)
    plan = store.get("plans", original)
    config = store.get("configs", store.get("reviews", review)["configRef"])
    evidence = [plan["sourceRefs"][1] + "#L12"]
    headers = {"Authorization": "Bearer ${vars.accessToken}"}
    ticket = {"headers": headers, "pathParams": {"id": "${vars.ticketId}"}}
    job = {"headers": headers, "pathParams": {"id": "${vars.jobId}"}}

    def status(code):
        return {"id": "status", "status": code, "evidenceRefs": evidence}

    def field(name, value):
        return {"id": name, "jsonPointer": "/" + name, "expected": value,
                "evidenceRefs": evidence}

    case = plan["cases"][0]
    case["steps"].extend([
        {
            "id": "submit", "operationId": "submitTicket", "identityRef": "owner",
            "request": ticket,
            "captures": {"jobId": {"jsonPointer": "/jobId", "secret": False,
                                   "purpose": "resource-cleanup"}},
            "assertions": [status(202), field("ticketId", "${vars.ticketId}")],
        },
        {
            "id": "outsider-job", "operationId": "getJob", "identityRef": "outsider",
            "request": job, "negative": True, "assertions": [status(403)],
        },
        {
            "kind": "poll", "id": "completion",
            "requestStep": {
                "id": "observe-job", "operationId": "getJob", "identityRef": "owner",
                "request": job,
                "captures": {"completedState": {"jsonPointer": "/state", "secret": False,
                                                "purpose": "case-data"}},
                "assertions": [status(200), field("state", "done"),
                               field("ticketId", "${vars.ticketId}"), field("id", "${vars.jobId}")],
            },
            "until": {
                "kind": "compare", "left": {"kind": "response", "jsonPointer": "/state"},
                "operator": "eq", "right": {"kind": "literal", "value": "done"},
                "evidenceRefs": evidence,
            },
            "intervalMs": 40, "maxAttempts": 10, "timeoutMs": 3000,
        },
        {
            "id": "submitted-ticket", "operationId": "getTicket", "identityRef": "owner",
            "request": ticket,
            "assertions": [status(200), field("state", "submitted"),
                           field("title", "revised-${run.id}"), field("id", "${vars.ticketId}")],
        },
        {
            "id": "reject-edit", "operationId": "updateTicket", "identityRef": "owner",
            "request": {**ticket, "body": {"title": "illegal"}}, "negative": True,
            "assertions": [status(409)],
        },
        {
            "id": "reject-submit", "operationId": "submitTicket", "identityRef": "owner",
            "request": ticket, "negative": True, "assertions": [status(409)],
        },
        {
            "id": "unchanged-ticket", "operationId": "getTicket", "identityRef": "owner",
            "request": ticket,
            "assertions": [status(200), field("state", "submitted"),
                           field("title", "revised-${run.id}")],
        },
    ])
    case["cleanup"][:0] = [
        {"id": "delete-job", "operationId": "deleteJob", "identityRef": "owner",
         "request": job, "requires": ["accessToken", "jobId"], "assertions": [status(204)]},
        {"id": "verify-job-deleted", "operationId": "getJob", "identityRef": "owner",
         "request": job, "requires": ["accessToken", "jobId"], "assertions": [status(404)]},
    ]
    understanding = plan["businessUnderstanding"]
    understanding["summary"] += " Submission completes asynchronously and prevents later edits."
    understanding["flows"].append({
        "id": "submission", "description": "Submit, observe completion, verify state and cleanup",
        "operationIds": ["submitTicket", "getJob", "getTicket", "deleteJob"],
        "basis": "contract", "evidenceRefs": evidence,
    })
    for rule, description, operations in [
        ("job-result", "Completion retains the ticket relationship", ["getJob", "getTicket"]),
        ("submitted-state", "Submitted tickets cannot change or submit again",
         ["updateTicket", "submitTicket"]),
    ]:
        understanding["rules"].append({
            "id": rule, "description": description, "operationIds": operations,
            "basis": "contract", "evidenceRefs": evidence,
        })
    plan["coverage"]["flows"].append({"flowId": "submission", "caseIds": ["ownership"]})
    plan["coverage"]["rules"].extend([
        {"ruleId": "job-result", "assertionRefs": ["ownership.completion.ticketId",
                                                  "ownership.submitted-ticket.state"]},
        {"ruleId": "submitted-state", "assertionRefs": ["ownership.reject-edit.status",
                                                       "ownership.reject-submit.status",
                                                       "ownership.unchanged-ticket.title"]},
    ])
    plan["coverage"]["uncovered"] = [{
        "itemId": "conditional-path", "reason": "Conditional branches are not implemented yet",
    }]
    config["policy"]["runAllow"].extend(["submitTicket", "getJob", "deleteJob"])
    config["policy"]["cleanupAllow"].extend(["getJob", "deleteJob"])
    config["policy"]["pollAllow"] = ["getJob"]
    plan["name"] = "ticket-asynchronous-submission"
    plan_ref = service.save_plan(project, plan)["planRef"]
    config_ref = service.stage_config(project, config)["configRef"]
    bundle = service.prepare_review(project, plan_ref, config_ref, "test")
    service.get_artifact(project, bundle["artifactRef"])
    decision = service.record_decision(project, bundle["reviewBundleRef"], plan_ref,
                                       bundle["scopeHash"], "approved", "test-only",
                                       "asynchronous fixture regression")
    return project, plan_ref, decision["reviewRef"]
