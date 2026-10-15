"""Fixed scheduler regressions; not an answer supplied to an Agent during acceptance."""

from copy import deepcopy

from horus.core.service import Service
from tests.support import reviewed_regression_plan


def reviewed_parallel_plan(
    service: Service,
    base_url: str,
    *,
    limit: int = 2,
    order: tuple[str, ...] = ("first", "second", "third", "load", "last"),
    run_duration_ms: int = 120000,
):
    project, original, review = reviewed_regression_plan(service, base_url)
    store = service.project(project)
    plan = store.get("plans", original)
    config = store.get("configs", store.get("reviews", review)["configRef"])
    templates = plan["cases"]
    plan["cases"] = []
    for name in order:
        case = deepcopy(templates[1 if name.startswith("load") else 0])
        case["id"] = name
        for step in [*case["steps"], *case["cleanup"]]:
            step["request"].setdefault("headers", {})["X-Horus-Case"] = name
        case["steps"][1]["request"]["body"]["name"] = name + "-${run.id}"
        plan["cases"].append(case)
    plan["name"] = "independent-cases-and-exclusive-load"
    plan["coverage"]["flows"][0]["caseIds"] = list(order)
    plan["coverage"]["rules"][0]["assertionRefs"] = [name + ".read.value" for name in order]
    config["policy"]["limits"]["maxFunctionalParallelCases"] = limit
    config["policy"]["limits"]["maxRunDurationMs"] = run_duration_ms
    ref = service.save_plan(project, plan)["planRef"]
    config_ref = service.stage_config(project, config)["configRef"]
    bundle = service.prepare_review(project, ref, config_ref, "test")
    service.get_artifact(project, bundle["artifactRef"])
    decision = service.record_decision(
        project,
        bundle["reviewBundleRef"],
        ref,
        bundle["scopeHash"],
        "approved",
        "test-only",
        "bounded functional scheduling regression",
    )
    return project, ref, decision["reviewRef"]
