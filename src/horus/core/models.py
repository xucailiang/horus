from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


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
    sourceJsonPointer: str
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
    assertionRefs: list[str] = Field(min_length=1)


class DependencyCoverage(Model):
    dependencyId: str
    captureRef: str
    consumerStepRefs: list[str] = Field(min_length=1)


class Uncovered(Model):
    itemId: str
    reason: str = Field(min_length=1)


class Coverage(Model):
    flows: list[FlowCoverage]
    rules: list[RuleCoverage]
    dataDependencies: list[DependencyCoverage]
    uncovered: list[Uncovered]


class Request(Model):
    pathParams: dict[str, JsonValue] = Field(default_factory=dict)
    query: dict[str, JsonValue] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: JsonValue = None


class Capture(Model):
    jsonPointer: str | None = None
    header: str | None = None
    status: bool = False
    secret: bool
    purpose: str

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if sum([self.jsonPointer is not None, self.header is not None, self.status]) != 1:
            raise ValueError("Capture requires exactly one response source")
        return self


class Assertion(Model):
    id: str
    status: int | list[int] | None = None
    jsonPointer: str | None = None
    header: str | None = None
    elapsedMs: bool = False
    jsonSchema: dict[str, JsonValue] | None = None
    dialect: Literal["2020-12", "draft4", "oas30", "oas31"] | None = None
    operator: Literal["eq", "ne", "exists", "type", "length", "gte", "lte", "regex"] = "eq"
    expected: JsonValue = None
    evidenceRefs: list[str] = Field(min_length=1)
    tentative: bool = False

    @model_validator(mode="after")
    def one_query(self) -> Self:
        if sum([self.status is not None, self.jsonPointer is not None,
                self.header is not None, self.elapsedMs, self.jsonSchema is not None]) != 1:
            raise ValueError("Assertion requires exactly one query")
        if self.jsonSchema is not None and self.dialect is None:
            raise ValueError("JSON Schema assertion requires a dialect")
        return self


class HttpStep(Model):
    kind: Literal["http"] = "http"
    id: str
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
    operationId: str
    request: Request
    assertions: list[Assertion] = Field(min_length=1)
    loadProfile: LoadProfile
    thresholds: Thresholds = Field(default_factory=Thresholds)


Step = HttpStep | LoadStep


class Case(Model):
    id: str
    kind: Literal["functional", "performance"]
    steps: list[Step] = Field(min_length=1)
    cleanup: list[HttpStep]


class Plan(Model):
    schemaVersion: Literal["1"] = "1"
    name: str
    sourceRefs: list[str] = Field(min_length=1)
    businessUnderstanding: BusinessUnderstanding
    coverage: Coverage
    cases: list[Case] = Field(min_length=1)
    parentPlanRef: str | None = None
    changeReason: str | None = None

    @model_validator(mode="after")
    def parent_reason(self) -> Self:
        if self.parentPlanRef is not None and not self.changeReason:
            raise ValueError("A revised plan needs changeReason")
        return self
