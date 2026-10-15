"""Branch regression input; never a supplied answer to Agent generation acceptance."""

from copy import deepcopy

from horus.core.service import Service
from tests.async_plan import reviewed_async_plan


def reviewed_branch_plan(service: Service, base_url: str):
    project, original, review = reviewed_async_plan(service, base_url)
    store = service.project(project)
    plan = store.get("plans", original)
    config_ref = store.get("reviews", review)["configRef"]
    case = plan["cases"][0]
    evidence = [plan["sourceRefs"][1] + "#L12"]

    def predicate(left, right, operator="eq"):
        return {
            "kind": "compare",
            "left": left,
            "operator": operator,
            "right": right,
            "evidenceRefs": evidence,
        }

    login = case["steps"][0]
    case["steps"][0] = {
        "kind": "branch",
        "id": "owner-auth",
        "predicate": predicate(
            {"kind": "literal", "value": True}, {"kind": "literal", "value": True}
        ),
        "thenSteps": [login],
        "elseSteps": [{**deepcopy(login), "id": "alternate-login"}],
        "outputs": [{"name": "accessToken", "identityRef": "owner"}],
    }
    index = next(i for i, step in enumerate(case["steps"]) if step["id"] == "completion")
    poll = case["steps"][index]
    poll["id"] = "wait"
    poll["requestStep"]["captures"]["completedTicketId"] = {
        "jsonPointer": "/ticketId",
        "secret": False,
        "purpose": "case-data",
    }
    ready = deepcopy(poll["requestStep"])
    ready["id"] = "ready"
    observation = deepcopy(ready)
    observation.update(
        id="observe-state",
        captures={"jobState": {"jsonPointer": "/state", "secret": False, "purpose": "case-data"}},
        assertions=ready["assertions"][:1],
    )
    case["steps"][index : index + 1] = [
        observation,
        {
            "kind": "branch",
            "id": "completion",
            "identityRef": "owner",
            "predicate": predicate(
                {"kind": "variable", "name": "jobState"}, {"kind": "literal", "value": "done"}
            ),
            "thenSteps": [ready],
            "elseSteps": [poll],
            "outputs": [{"name": "completedTicketId"}],
        },
    ]
    next(step for step in case["steps"] if step["id"] == "submitted-ticket")["request"][
        "pathParams"
    ]["id"] = "${vars.completedTicketId}"
    for rule in plan["coverage"]["rules"]:
        rule["assertionRefs"] = [
            path
            for ref in rule["assertionRefs"]
            for path in (
                [
                    ref.replace(".completion.", ".completion.then.ready."),
                    ref.replace(".completion.", ".completion.else.wait."),
                ]
                if ".completion." in ref
                else [ref]
            )
        ]
    plan["coverage"]["uncovered"] = []
    plan["name"] = "ticket-conditional-completion"
    ref = service.save_plan(project, plan)["planRef"]
    bundle = service.prepare_review(project, ref, config_ref, "test")
    service.get_artifact(project, bundle["artifactRef"])
    decision = service.record_decision(
        project,
        bundle["reviewBundleRef"],
        ref,
        bundle["scopeHash"],
        "approved",
        "test-only",
        "conditional fixture regression",
    )
    return project, ref, decision["reviewRef"]
