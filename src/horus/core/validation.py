import json
import re
from dataclasses import dataclass, field
from typing import Any

from horus.core.models import (
    BranchStep,
    Environment,
    HttpStep,
    Plan,
    PollStep,
    Predicate,
    ProjectConfig,
    Step,
    VariableOperand,
)
from horus.core.predicates import comparisons, guaranteed_variables
from horus.core.steps import request_action, walk_steps
from horus.core.store import HorusError
from horus.core.values import VARIABLE

VariableKey = tuple[str | None, str]


@dataclass
class Scope:
    known: set[VariableKey] = field(default_factory=set)
    possible: set[VariableKey] = field(default_factory=set)
    prior: set[str] = field(default_factory=set)

    def copy(self) -> "Scope":
        return Scope(set(self.known), set(self.possible), set(self.prior))


class FlowValidator:
    def __init__(
        self,
        plan: Plan,
        operations: dict[str, Any],
        config: ProjectConfig | None,
        environment: Environment | None,
    ) -> None:
        self.plan, self.operations, self.config, self.environment = (
            plan,
            operations,
            config,
            environment,
        )
        self.profiles = {profile.id: profile for profile in plan.identityProfiles}
        self.case_ids: set[str] = set()
        self.step_refs: set[str] = set()
        self.assertion_refs: set[str] = set()
        self.capture_refs: set[str] = set()
        self.private_names: set[str] = set()
        self.max_requests = 0
        self.declared_duration_ms = 0

    def variable(self, identity: str | None, name: str) -> VariableKey:
        return (identity if name in self.private_names else None, name)

    def visible(self, keys: set[VariableKey], identity: str | None) -> set[str]:
        return {name for owner, name in keys if self.variable(identity, name) == (owner, name)}

    def identity(self, identity: str | None, location: str) -> None:
        if identity is not None and identity not in self.profiles:
            raise HorusError("unknown_identity", location)

    def predicate(
        self,
        predicate: Predicate,
        identity: str | None,
        scope: Scope,
        location: str,
    ) -> None:
        for condition in comparisons(predicate):
            for operand in (condition.left, condition.right):
                if isinstance(operand, VariableOperand):
                    available = (
                        scope.possible
                        if (operand is condition.left and condition.operator == "exists")
                        else scope.known
                    )
                    if self.variable(identity, operand.name) not in available:
                        raise HorusError("missing_variable_source", location)

    def validate(self) -> "FlowValidator":
        for case in self.plan.cases:
            if case.id in self.case_ids:
                raise HorusError("duplicate_case", case.id)
            self.case_ids.add(case.id)
            self.private_names = set()
            public: set[str] = set()
            for _, node in walk_steps([*case.steps, *case.cleanup]):
                if not isinstance(node, BranchStep) and isinstance(
                    http := request_action(node), HttpStep
                ):
                    for name, capture in http.captures.items():
                        (self.private_names if capture.private else public).add(name)
            if self.private_names & public:
                raise HorusError("ambiguous_variable_scope", case.id)
            scope = Scope()
            requests, duration = self.sequence(case.steps, scope, case.id, case.kind)
            cleanup_requests, _ = self.sequence(
                case.cleanup, scope, case.id, case.kind, cleanup=True
            )
            self.max_requests += requests + cleanup_requests
            self.declared_duration_ms += duration
        return self

    def sequence(
        self,
        steps: list[Step] | list[HttpStep],
        scope: Scope,
        case_id: str,
        case_kind: str,
        prefix: str = "",
        *,
        cleanup: bool = False,
    ) -> tuple[int, int]:
        requests = duration = 0
        for node in steps:
            path = prefix + node.id
            location = case_id + "." + path
            if location in self.step_refs:
                raise HorusError("invalid_step", location)
            self.step_refs.add(location)
            if isinstance(node, BranchStep):
                self.identity(node.identityRef, location)
                self.predicate(node.predicate, node.identityRef, scope, location)
                paths = []
                for side, children, truth in (
                    ("then", node.thenSteps, True),
                    ("else", node.elseSteps, False),
                ):
                    child_scope = scope.copy()
                    child_scope.known.update(
                        self.variable(node.identityRef, name)
                        for name in guaranteed_variables(node.predicate, truth)
                    )
                    budget = self.sequence(
                        children, child_scope, case_id, case_kind, path + "." + side + "."
                    )
                    paths.append((child_scope, budget))
                for output in node.outputs:
                    self.identity(output.identityRef, location)
                    if output.identityRef is not None and output.name not in self.private_names:
                        raise HorusError("invalid_branch_output", location)
                    key = self.variable(output.identityRef, output.name)
                    if not all(key in state.known for state, _ in paths):
                        raise HorusError("branch_output_not_definite", location)
                    scope.known.add(key)
                scope.possible.update(set().union(*(state.possible for state, _ in paths)))
                requests += max(budget[0] for _, budget in paths)
                duration += max(budget[1] for _, budget in paths)
            else:
                count, elapsed = self.validate_action(node, scope, location, case_kind, cleanup)
                requests += count
                duration += elapsed
            scope.prior.add(path)
        return requests, duration

    def validate_action(
        self,
        node: Step,
        scope: Scope,
        location: str,
        case_kind: str,
        cleanup: bool,
    ) -> tuple[int, int]:
        assert not isinstance(node, BranchStep)
        step = request_action(node)
        if step.operationId not in self.operations:
            raise HorusError("invalid_step", location)
        self.identity(step.identityRef, location)
        visible = self.visible(scope.known, step.identityRef)
        requires = set(step.requires) if isinstance(step, HttpStep) else set()
        if not requires.issubset(self.visible(scope.possible, step.identityRef)):
            raise HorusError("unknown_cleanup_variable", location)
        if isinstance(step, HttpStep) and not set(step.dependsOn).issubset(scope.prior):
            raise HorusError("invalid_dependency", location)
        if isinstance(node, PollStep):
            guarded = scope.copy()
            guarded.known.update(self.variable(step.identityRef, name) for name in requires)
            self.predicate(node.until, step.identityRef, guarded, location)
            if self.config and step.operationId not in self.config.policy.pollAllow:
                raise HorusError("poll_operation_denied", location)
        request_json = step.request.model_dump_json()
        if re.search(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", request_json):
            raise HorusError("unsupported_variable_syntax", f"{location}: use ${{vars.name}}")
        for expression in re.findall(r"(?<!\$)\$\{[^}]+\}", request_json):
            if not VARIABLE.fullmatch(expression):
                raise HorusError("unsupported_variable_syntax", location)
        captured = set(step.captures) if isinstance(step, HttpStep) else set()
        for data, available in (
            (request_json, visible | requires),
            (
                json.dumps([a.model_dump(mode="json") for a in step.assertions]),
                visible | requires | captured,
            ),
        ):
            for namespace, name in VARIABLE.findall(data):
                if namespace == "vars" and name not in available:
                    raise HorusError("missing_variable_source", f"{location}: {name}")
                if namespace == "run" and name != "id":
                    raise HorusError("unknown_run_variable", name)
                if namespace == "secrets":
                    bindings = (
                        self.profiles[step.identityRef].credentialRefs
                        if (step.identityRef is not None)
                        else self.environment.secretBindings
                        if self.environment
                        else None
                    )
                    if bindings is not None and name not in bindings:
                        raise HorusError("missing_secret_binding", f"{location}: {name}")
        if self.config:
            allowed = self.config.policy.cleanupAllow if cleanup else self.config.policy.runAllow
            if step.operationId not in allowed:
                raise HorusError("operation_denied", location)
        for assertion in step.assertions:
            ref = location + "." + assertion.id
            if ref in self.assertion_refs:
                raise HorusError("duplicate_assertion", ref)
            self.assertion_refs.add(ref)
            if not isinstance(step, HttpStep) and (assertion.jsonSchema or assertion.elapsedMs):
                raise HorusError("unsupported_load_assertion", ref)
        if isinstance(step, HttpStep):
            for name in step.captures:
                key = self.variable(step.identityRef, name)
                scope.possible.add(key)
                if requires.issubset(visible):
                    scope.known.add(key)
            self.capture_refs.update(location + "." + name for name in step.captures)
            return (node.maxAttempts, node.timeoutMs) if isinstance(node, PollStep) else (1, 0)
        if case_kind != "performance":
            raise HorusError("load_requires_performance_case", location)
        if self.config:
            limits = self.config.policy.limits
            if (
                step.operationId not in self.config.policy.loadAllow
                or step.loadProfile.vus > limits.maxVus
                or step.loadProfile.iterations > limits.maxLoadIterations
                or step.loadProfile.maxDurationMs > limits.maxLoadDurationMs
            ):
                raise HorusError("load_budget_exceeded", location)
        return step.loadProfile.iterations, step.loadProfile.maxDurationMs
