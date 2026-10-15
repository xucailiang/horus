import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from regress import Regex, RegressError

from horus.core.report_text import EN


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


JsonPointer = Annotated[
    str,
    Field(
        pattern=r"^(?:/(?:[^~/]|~[01])*)*$",
        description='RFC 6901 JSON Pointer: /id, /items/0, or "" for the whole body; not $.id.',
    ),
]


class Limits(Model):
    maxVus: int = Field(default=10, ge=1, le=10)
    maxLoadIterations: int = Field(default=1000, ge=1, le=1000)
    maxLoadDurationMs: int = Field(default=60000, ge=1, le=60000)
    maxFunctionalParallelCases: int = Field(default=2, ge=1, le=2)
    maxRequestsPerRun: int = Field(default=1500, ge=1, le=1500)
    maxRunDurationMs: int = Field(default=120000, ge=1, le=120000)
    maxCleanupDurationMs: int = Field(default=30000, ge=1, le=30000)
    maxReportDurationMs: int = Field(default=10000, ge=1, le=10000)


class Policy(Model):
    probeAllow: list[str] = Field(default_factory=list)
    runAllow: list[str]
    cleanupAllow: list[str]
    loadAllow: list[str] = Field(default_factory=list)
    pollAllow: list[str] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)


class Environment(Model):
    baseUrl: str | None = None
    baseUrlFromEnv: str | None = None
    secretBindings: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def target_source(self) -> Self:
        if (self.baseUrl is None) == (self.baseUrlFromEnv is None):
            raise ValueError("Specify exactly one of baseUrl or baseUrlFromEnv")
        return self


class ProjectConfig(Model):
    schemaVersion: Literal["1"] = "1"
    name: str
    environments: dict[str, Environment]
    policy: Policy


class Named(Model):
    id: str
    description: str


class Claim(Named):
    operationIds: list[str]
    basis: Literal["contract", "source-inference", "observation", "human-requirement"]
    evidenceRefs: list[str] = Field(min_length=1)


class State(Model):
    resourceId: str
    values: list[str]
    evidenceRefs: list[str] = Field(min_length=1)


class DataDependency(Model):
    id: str
    producer: str
    sourceJsonPointer: JsonPointer
    consumers: list[str]
    purpose: str
    lifecycle: Literal["case"] = "case"
    evidenceRefs: list[str] = Field(min_length=1)


class Finding(Named):
    impact: str
    evidenceRefs: list[str] = Field(default_factory=list)


class BusinessUnderstanding(Model):
    summary: str = Field(min_length=1)
    actors: list[Named]
    resources: list[Named]
    states: list[State]
    flows: list[Claim]
    rules: list[Claim]
    dataDependencies: list[DataDependency]
    assumptions: list[Finding]
    conflicts: list[Finding]
    unknowns: list[Finding]


class FlowCoverage(Model):
    flowId: str
    caseIds: list[str] = Field(min_length=1)


class RuleCoverage(Model):
    ruleId: str
    assertionRefs: list[str] = Field(
        min_length=1, description="Exact references: <case.id>.<step.id>.<assertion.id>"
    )


class DependencyCoverage(Model):
    dependencyId: str
    captureRef: str = Field(description="Exact reference: <case.id>.<step.id>.<capture name>")
    consumerStepRefs: list[str] = Field(
        min_length=1, description="Exact references: <case.id>.<step.id>"
    )


class Uncovered(Model):
    itemId: str
    reason: str = Field(min_length=1)


class Coverage(Model):
    flows: list[FlowCoverage]
    rules: list[RuleCoverage]
    dataDependencies: list[DependencyCoverage]
    uncovered: list[Uncovered]


class Request(Model):
    """HTTP values use ${vars.name}, ${secrets.binding} or ${run.id} for dynamic data.

    A whole placeholder preserves its JSON type. $${...} escapes a literal.
    Double-brace templates such as {{token}} are unsupported.
    """

    pathParams: dict[str, JsonValue] = Field(default_factory=dict)
    query: dict[str, JsonValue] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: JsonValue = None


class Capture(Model):
    """Authentication captures are private to the selected identity and omitted from the ledger.

    Other captures are case data; secret=true redacts them and omits their ledger values.
    """

    jsonPointer: JsonPointer | None = None
    header: str | None = None
    status: bool = False
    secret: bool
    purpose: str

    @property
    def private(self) -> bool:
        return self.purpose == "authentication"

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if sum([self.jsonPointer is not None, self.header is not None, self.status]) != 1:
            raise ValueError("Capture requires exactly one response source")
        return self


class Assertion(Model):
    """Regex uses a plain ECMAScript pattern with Unicode (`u`) semantics, without / delimiters.

    It searches a JSON/header string; anchor with ^ and $ to check its full format.
    """

    id: str
    description: str | None = Field(default=None, max_length=500)
    status: int | list[int] | None = None
    jsonPointer: JsonPointer | None = None
    header: str | None = None
    elapsedMs: bool = False
    jsonSchema: dict[str, JsonValue] | None = Field(
        default=None,
        description="Validate the whole response body. Requires dialect; do not also set "
        "jsonPointer, status, header or elapsedMs. For array membership, use contains "
        "with dialect 2020-12 or oas31.",
    )
    dialect: Literal["2020-12", "draft4", "oas30", "oas31"] | None = None
    operator: Literal["eq", "ne", "exists", "type", "length", "gte", "lte", "regex"] = "eq"
    expected: JsonValue = None
    evidenceRefs: list[str] = Field(min_length=1)
    tentative: bool = False

    @model_validator(mode="after")
    def one_query(self) -> Self:
        if (
            sum(
                [
                    self.status is not None,
                    self.jsonPointer is not None,
                    self.header is not None,
                    self.elapsedMs,
                    self.jsonSchema is not None,
                ]
            )
            != 1
        ):
            raise ValueError("Assertion requires exactly one query")
        if self.jsonSchema is not None and self.dialect is None:
            raise ValueError("JSON Schema assertion requires a dialect")
        if self.operator == "regex":
            if self.jsonPointer is None and self.header is None:
                raise ValueError("Regex requires a JSON Pointer or header string query")
            if not isinstance(self.expected, str) or len(self.expected) > 512:
                raise ValueError(
                    "Regex expected must be an ECMAScript pattern of at most 512 characters"
                )
            try:
                Regex(self.expected, flags="u")
            except RegressError as exc:
                raise ValueError("Invalid ECMAScript Unicode regular expression") from exc
        return self


class HttpStep(Model):
    kind: Literal["http"] = "http"
    id: str
    title: str | None = Field(default=None, max_length=160)
    operationId: str
    request: Request
    captures: dict[str, Capture] = Field(default_factory=dict)
    assertions: list[Assertion] = Field(min_length=1)
    role: Literal["authentication", "prepare", "exercise"] = "exercise"
    requires: list[str] = Field(default_factory=list)
    dependsOn: list[str] = Field(default_factory=list)
    negative: bool = False
    identityRef: str | None = None


class LoadProfile(Model):
    executor: Literal["shared-iterations"] = "shared-iterations"
    vus: int = Field(ge=1)
    iterations: int = Field(ge=1)
    maxDurationMs: int = Field(ge=1)


class Maximum(Model):
    lte: float = Field(ge=0)


class ExactCount(Model):
    eq: int = Field(ge=1)


class Thresholds(Model):
    latencyP95Ms: Maximum | None = None
    httpErrorRate: Maximum | None = None
    dataCheckFailureRate: Maximum | None = None
    completedIterations: ExactCount | None = None


class LoadStep(Model):
    kind: Literal["load"]
    id: str
    title: str | None = Field(default=None, max_length=160)
    operationId: str
    request: Request
    assertions: list[Assertion] = Field(min_length=1)
    loadProfile: LoadProfile
    thresholds: Thresholds = Field(default_factory=Thresholds)
    identityRef: str | None = None

    @model_validator(mode="after")
    def supported_regex(self) -> Self:
        for assertion in self.assertions:
            if (
                assertion.operator == "regex"
                and isinstance(assertion.expected, str)
                and re.search(r"(?<!\\)(?:\\\\)*\\[pP]\{", assertion.expected)
            ):
                raise ValueError(
                    "k6 does not support Unicode property escapes in regex assertions; "
                    "use explicit character ranges or a functional check"
                )
        return self


class LiteralOperand(Model):
    kind: Literal["literal"]
    value: JsonValue


class VariableOperand(Model):
    kind: Literal["variable"]
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class ResponseOperand(Model):
    kind: Literal["response"]
    jsonPointer: JsonPointer | None = None
    header: str | None = None
    status: bool = False

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if sum([self.jsonPointer is not None, self.header is not None, self.status]) != 1:
            raise ValueError("Response operand requires exactly one source")
        return self


Operand = Annotated[LiteralOperand | VariableOperand | ResponseOperand, Field(discriminator="kind")]


class Comparison(Model):
    kind: Literal["compare"]
    left: Operand
    operator: Literal["eq", "ne", "exists", "type", "length", "gte", "lte"]
    right: Operand
    evidenceRefs: list[str] = Field(min_length=1)


class PredicateGroup(Model):
    kind: Literal["all", "any"]
    predicates: list["Predicate"] = Field(min_length=1, max_length=20)


class PredicateNot(Model):
    kind: Literal["not"]
    predicate: "Predicate"


Predicate = Annotated[Comparison | PredicateGroup | PredicateNot, Field(discriminator="kind")]


def check_predicate(predicate: Predicate, *, response: bool) -> None:
    pending: list[tuple[Predicate, int]] = [(predicate, 1)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 64 or depth > 8:
            raise ValueError("Predicate is limited to 64 nodes and depth 8")
        if isinstance(item, PredicateGroup):
            pending.extend((child, depth + 1) for child in item.predicates)
        elif isinstance(item, PredicateNot):
            pending.append((item.predicate, depth + 1))
        elif not response and any(
            isinstance(op, ResponseOperand) for op in (item.left, item.right)
        ):
            raise ValueError("Branch predicates can only read literals and prior variables")


class PollStep(Model):
    """Observe an explicitly allowed read operation, then capture and assert its final response.

    Status assertions guard every attempt; other assertions run only once `until` is true.
    Coverage and ledger references use the outer poll id, not requestStep.id.
    """

    kind: Literal["poll"]
    id: str
    title: str | None = Field(default=None, max_length=160)
    requestStep: HttpStep
    until: Predicate
    intervalMs: int = Field(ge=1, le=10000)
    maxAttempts: int = Field(ge=1, le=1000)
    timeoutMs: int = Field(ge=1, le=60000)

    @model_validator(mode="after")
    def bounded_observation(self) -> Self:
        if not any(a.status is not None and not a.tentative for a in self.requestStep.assertions):
            raise ValueError("Polling requires an explicit, non-tentative status assertion")
        if self.requestStep.role != "exercise":
            raise ValueError(
                "A polling request observes progress; it cannot authenticate or prepare"
            )
        check_predicate(self.until, response=True)
        return self


class BranchOutput(Model):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    identityRef: str | None = None


class BranchStep(Model):
    kind: Literal["branch"]
    id: str
    title: str | None = Field(default=None, max_length=160)
    predicate: Predicate
    identityRef: str | None = None
    thenSteps: list["Step"]
    elseSteps: list["Step"]
    outputs: list[BranchOutput]

    @model_validator(mode="after")
    def bounded_condition(self) -> Self:
        check_predicate(self.predicate, response=False)
        keys = {(output.identityRef, output.name) for output in self.outputs}
        if len(keys) != len(self.outputs):
            raise ValueError("Duplicate branch output")
        return self


Step = HttpStep | LoadStep | PollStep | BranchStep


class Case(Model):
    id: str
    title: str | None = Field(default=None, max_length=160)
    kind: Literal["functional", "performance"]
    steps: list[Step] = Field(min_length=1)
    cleanup: list[HttpStep]

    @model_validator(mode="after")
    def bounded_steps(self) -> Self:
        pending: list[tuple[Step, int]] = [(step, 1) for step in self.steps]
        count = len(self.cleanup)
        while pending:
            step, depth = pending.pop()
            count += 1
            if count > 2000 or depth > 8:
                raise ValueError("A case is limited to 2000 steps and depth 8")
            if isinstance(step, BranchStep):
                pending.extend((child, depth + 1) for child in [*step.thenSteps, *step.elseSteps])
        return self


class SupportingContext(Model):
    id: str
    text: str = Field(min_length=1)
    source: Literal["conversation", "host-source", "note"]
    locator: str | None = None


class ResourceMonitoring(Model):
    targetLabel: str = Field(min_length=1, max_length=120)
    commands: list[str] = Field(min_length=1)
    intervalSeconds: float = Field(default=2.0, ge=1, le=60)
    maxDurationSeconds: int = Field(default=180, ge=1, le=1800)


class GpuSample(Model):
    index: int = Field(ge=0)
    utilizationPercent: float | None = Field(default=None, ge=0, le=100)
    memoryUsedBytes: int | None = Field(default=None, ge=0)
    memoryTotalBytes: int | None = Field(default=None, ge=0)


class ResourceSample(Model):
    at: float = Field(ge=0, le=253402300799)
    cpuPercent: float | None = Field(default=None, ge=0, le=100)
    memoryUsedBytes: int | None = Field(default=None, ge=0)
    memoryTotalBytes: int | None = Field(default=None, ge=0)
    gpus: list[GpuSample] = Field(default_factory=list)


class ResourceMetrics(Model):
    targetLabel: str = Field(min_length=1, max_length=120)
    source: Literal["ssh"] = "ssh"
    status: Literal["collected", "partial", "unavailable"]
    intervalSeconds: float = Field(default=2.0, ge=1, le=60)
    samples: list[ResourceSample] = Field(default_factory=list, max_length=600)
    notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def measured_samples(self) -> Self:
        if self.status == "collected" and not any(
            s.cpuPercent is not None or s.memoryUsedBytes is not None or s.gpus
            for s in self.samples
        ):
            raise ValueError("collected requires actual resource measurements")
        if self.status == "unavailable" and (self.samples or not self.notes):
            raise ValueError("unavailable requires a reason and no invented samples")
        if [s.at for s in self.samples] != sorted(s.at for s in self.samples):
            raise ValueError("Resource samples must be chronological")
        for sample in self.samples:
            memories: list[ResourceSample | GpuSample] = [sample, *sample.gpus]
            for memory in memories:
                if (
                    memory.memoryUsedBytes is not None
                    and memory.memoryTotalBytes is not None
                    and memory.memoryUsedBytes > memory.memoryTotalBytes
                ):
                    raise ValueError("Used memory cannot exceed total memory")
        return self


class IdentityProfile(Named):
    """Map local credential aliases to environment secretBindings names, never values.

    Steps select identityRef; ${secrets.username} resolves through this profile's username alias.
    Authentication captures are private to that identity within the case.
    """

    id: str = Field(min_length=1)
    credentialRefs: dict[
        Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")],
        Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")],
    ]
    evidenceRefs: list[str] = Field(min_length=1)


class ReportPresentation(Model):
    language: str = Field(
        default="en",
        pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
        description="Use the current Codex/Claude Code conversation language.",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        json_schema_extra={"x-labels": {k: v for k, v in EN.items()}},
        description="UI translations. Required in full for languages "
        "other than English or Chinese; see x-labels for all keys.",
    )

    @model_validator(mode="after")
    def translated_labels(self) -> Self:
        if set(self.labels) - set(EN):
            raise ValueError("Unknown report label key")
        if self.language.split("-")[0].lower() not in {"en", "zh"} and set(self.labels) != set(EN):
            raise ValueError("Provide every report label in the conversation language")
        if any(not text.strip() for text in self.labels.values()):
            raise ValueError("Report labels cannot be empty")
        return self


class Plan(Model):
    """Executable plan. Dynamic values use ${vars.name}, ${secrets.binding}, ${run.id}.

    Coverage references must be fully qualified, e.g. caseId.stepId.assertionId.
    Approval is a separate human decision after reading the fixed review material.
    """

    schemaVersion: Literal["1"] = "1"
    name: str
    report: ReportPresentation = Field(default_factory=ReportPresentation)
    sourceRefs: list[str] = Field(min_length=1)
    businessUnderstanding: BusinessUnderstanding
    coverage: Coverage
    cases: list[Case] = Field(min_length=1)
    supportingContext: list[SupportingContext] = Field(default_factory=list)
    identityProfiles: list[IdentityProfile] = Field(default_factory=list)
    resourceMonitoring: ResourceMonitoring | None = None
    parentPlanRef: str | None = None
    changeReason: str | None = None

    @model_validator(mode="after")
    def parent_reason(self) -> Self:
        if self.parentPlanRef is not None and not self.changeReason:
            raise ValueError("A revised plan needs changeReason")
        return self
