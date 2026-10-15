import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field, ValidationError

from horus import __version__
from horus.adapters.process import check_engine
from horus.core.models import Model, Plan, ProjectConfig, ResourceMetrics
from horus.core.runs import Runs
from horus.core.service import Service
from horus.core.store import HorusError

INSTRUCTIONS = """horus tests HTTP APIs with human-reviewed, immutable plans.
Call horus_get_capabilities, open a project, stage config, import an OpenAPI/Swagger URL,
then generate your own business-aware plan using horus_get_plan_schema.
Set plan.report.language and all human-facing text to the current conversation language.
Call horus_preview_plan to validate without saving. Present its readable content DIRECTLY
in chat, never in an Ask/question tool card. Wait for a real human confirmation, then
call horus_confirm_plan with the SAME draft, previewRef and the actual confirmation.
Each run saves plan.md, plan.json and report.md in the returned testDirectory.
Open reportPath: lead with verdict, failures and key metrics; use a brief business Mermaid.
Detailed actions and evidence are in details.md / detailsArtifactRef, separate from the summary.
Use ${vars.name}, ${secrets.binding}, ${run.id}; double-brace templates are unsupported.
Read optional project code using your host's existing tools. Put relevant source findings
or user chat material in plan.supportingContext; cite these as context:<id>. No repository
cloning or special source channel is required from horus.
The legacy save/prepare/record review tools remain for saved plans and CLI replay.
Only record an explicit human decision on the exact displayed version.
A general test request is not approval. Never submit credential values.
Use explicit projectRef throughout. The installed horus skill adds workflow guidance;
MCP works without the skill. Supported capabilities and remaining gaps are explicit.
Read a saved plan with horus_get_plan; horus_get_artifact accepts only artifact references.
Once execution is available, use a stable requestId, bounded waits and report retrieval;
do not restart writes after losing a response or timing out a wait.
"""


class Issue(Model):
    code: str
    location: str
    reason: str
    nextAction: str
    needsUserInput: bool = False


class Result(Model):
    schemaVersion: str = "1"
    projectRef: str | None = None
    status: str
    summary: str
    refs: dict[str, str] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)
    nextActions: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


def create_server(workspace: Path, read_roots: list[Path] | None = None) -> FastMCP:
    service = Service(workspace, read_roots=read_roots)
    runs = Runs(service)
    server = FastMCP("horus", instructions=INSTRUCTIONS, version=__version__)

    async def invoke(project_ref: str | None, action: Callable[[], dict[str, Any]]) -> Result:
        try:
            payload = await asyncio.to_thread(action)
        except HorusError as exc:
            if exc.code == "run_not_finished":
                return Result(
                    projectRef=project_ref,
                    status="blocked",
                    summary=str(exc),
                    issues=[
                        Issue(
                            code=exc.code,
                            location="runId",
                            reason=str(exc),
                            nextAction="Wait for the existing run to finish, then retry",
                            needsUserInput=False,
                        )
                    ],
                    nextActions=["horus_get_run", "horus_wait_run"],
                )
            if exc.code.startswith("review_") or exc.code == "missing_target":
                return Result(
                    projectRef=project_ref,
                    status="blocked",
                    summary=str(exc),
                    issues=[
                        Issue(
                            code=exc.code,
                            location="",
                            reason=str(exc),
                            nextAction="Resolve the review or input issue",
                            needsUserInput=True,
                        )
                    ],
                )
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            raise ToolError(str(exc.errors(include_input=False, include_url=False))) from exc
        except (OSError, ValueError) as exc:
            raise ToolError(f"input_error: {type(exc).__name__}") from exc
        refs = {k: v for k, v in payload.items() if k.endswith("Ref") and isinstance(v, str)}
        return Result(
            projectRef=payload.get("projectRef", project_ref),
            status=payload.get("status", "ready"),
            summary=payload.get("summary", "OK"),
            refs=refs,
            payload=payload,
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_get_capabilities() -> Result:
        """Check readiness, supported capabilities and workflow before opening a project."""
        engines = {}
        for name in ["hurl", "k6"]:
            try:
                engines[name] = {"status": "ready", "version": await check_engine(name)}
            except HorusError as exc:
                engines[name] = {"status": "unavailable", "reason": exc.code}
        return Result(
            status="ready",
            summary="Plan, review, execution and reports are available",
            payload={
                "version": __version__,
                "python": "3.12",
                "sources": ["openapi-url", "swagger-ui-url", "local-openapi"],
                "steps": ["http", "load", "poll", "branch"],
                "scheduling": {
                    "maxFunctionalParallelCases": 2,
                    "performanceCasesExclusive": True,
                },
                "engines": engines,
                "reports": ["markdown-mermaid", "json", "junit"],
                "planPreviewBeforeSave": True,
                "executionReady": all(e["status"] == "ready" for e in engines.values()),
                "gaps": [
                    "k6 regex assertions do not support Unicode property escapes",
                ],
                "readRoots": [str(p) for p in service.read_roots],
            },
            nextActions=["horus_open_project", "horus_get_plan_schema"],
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_get_plan_schema() -> Result:
        """Read the exact plan JSON Schema before generating a plan; no golden plan is supplied."""
        return Result(status="ready", summary="Plan schema", payload=Plan.model_json_schema())

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_list_projects(offset: int = 0, limit: int = 20) -> Result:
        """List persisted projects: start at offset 0, use limit 1–100, follow nextOffset."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ToolError("invalid_page")
        projects = service.list_projects()
        return Result(
            status="ready",
            summary="Projects",
            payload={
                "projects": projects[offset : offset + limit],
                "nextOffset": offset + limit if offset + limit < len(projects) else None,
            },
        )

    @server.tool()
    async def horus_open_project(name: str, createIfMissing: bool = False) -> Result:
        """Create or restore a project by name or projectRef, including saved asset references."""
        return await invoke(
            None, lambda: service.open_project(name, create_if_missing=createIfMissing)
        )

    @server.tool()
    async def horus_stage_project_config(projectRef: str, config: ProjectConfig) -> Result:
        """Save an immutable configuration draft. This does not authorize probes or execution."""
        return await invoke(
            projectRef, lambda: service.stage_config(projectRef, config.model_dump(mode="json"))
        )

    @server.tool()
    async def horus_import_api(projectRef: str, url: str) -> Result:
        """Import an OpenAPI/Swagger definition or common Swagger UI URL into the project."""
        return await invoke(projectRef, lambda: service.import_api(projectRef, url))

    @server.tool()
    async def horus_register_source(projectRef: str, kind: str, uri: str) -> Result:
        """Register an openapi URL or a local openapi/markdown file within a server read root."""
        return await invoke(projectRef, lambda: service.register_source(projectRef, kind, uri))

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_get_context(
        projectRef: str, sourceRef: str, offset: int = 0, limit: int = 100
    ) -> Result:
        """Read numbered documentation/code and operation catalog. Evidence uses sourceRef#Lx.

        offset is a zero-based line index; start at 0 with limit 1–200.
        Follow nextOffset until null. Returned evidence line numbers start at 1.
        """
        return await invoke(
            projectRef, lambda: service.get_context(projectRef, sourceRef, offset, limit)
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_preview_plan(
        projectRef: str, plan: Plan, configRef: str, environment: str = "test"
    ) -> Result:
        """Validate without saving. Show content directly in chat, not an Ask tool card.

        Set report.language, case/step titles and assertion descriptions in the conversation
        language. Wait for human confirmation before calling horus_confirm_plan.
        """
        return await invoke(
            projectRef,
            lambda: service.preview_plan(
                projectRef, plan.model_dump(mode="json"), configRef, environment
            ),
        )

    @server.tool()
    async def horus_confirm_plan(
        projectRef: str,
        plan: Plan,
        configRef: str,
        previewRef: str,
        confirmation: str,
        environment: str = "test",
    ) -> Result:
        """After actual human confirmation, save and approve the unchanged displayed draft.

        Pass its previewRef and the real human message reference/excerpt. Do not invent a
        confirmation or change the plan after it was confirmed. Changed drafts need a new
        preview and confirmation in chat. This call never sends target requests.
        """
        return await invoke(
            projectRef,
            lambda: service.confirm_plan(
                projectRef,
                plan.model_dump(mode="json"),
                configRef,
                environment,
                previewRef,
                confirmation,
            ),
        )

    @server.tool()
    async def horus_save_plan(projectRef: str, plan: Plan) -> Result:
        """Low-level saved-plan/replay API. For new tests use preview then confirm instead."""
        return await invoke(
            projectRef, lambda: service.save_plan(projectRef, plan.model_dump(mode="json"))
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_get_plan(
        projectRef: str,
        planRef: str,
        offset: int = 0,
        limit: int = 200,
    ) -> Result:
        """Read saved plan JSON pages after reconnecting; this does not present or approve it.

        offset is a zero-based line index; start at 0 with limit 1–200.
        Follow nextOffset until null to read the complete plan.
        """
        return await invoke(
            projectRef, lambda: service.get_plan(projectRef, planRef, offset, limit)
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_validate_plan(
        projectRef: str, planRef: str, configRef: str | None = None, environment: str = "test"
    ) -> Result:
        """Check sources, coverage, variables, scope and worst-case request budget."""
        return await invoke(
            projectRef, lambda: service.validate_plan(projectRef, planRef, configRef, environment)
        )

    @server.tool()
    async def horus_prepare_review(
        projectRef: str, planRef: str, configRef: str, environment: str = "test"
    ) -> Result:
        """Legacy saved-plan review. Use preview/confirm for new drafts approved in chat."""
        return await invoke(
            projectRef, lambda: service.prepare_review(projectRef, planRef, configRef, environment)
        )

    @server.tool()
    async def horus_get_artifact(
        projectRef: str, artifactRef: str, offset: int = 0, limit: int = 200
    ) -> Result:
        """Read review/report content; reading review material records retrieval only.

        offset is a zero-based line index; start at 0 with limit 1–200.
        Follow nextOffset until null to read the complete artifact.
        """
        return await invoke(
            projectRef, lambda: service.get_artifact(projectRef, artifactRef, offset, limit)
        )

    @server.tool()
    async def horus_record_review_decision(
        projectRef: str,
        reviewBundleRef: str,
        planRef: str,
        scopeHash: str,
        decision: str,
        source: str,
        confirmation: str,
    ) -> Result:
        """Record an actual human decision after displaying this version; never invent approval.

        decision is approved or rejected. source is conversation or CLI.
        confirmation is the real message reference or excerpt.
        This trusted-local record is not independent identity authentication.
        """
        if source not in {"conversation", "CLI"}:
            raise ToolError("invalid_decision_source")
        return await invoke(
            projectRef,
            lambda: service.record_decision(
                projectRef, reviewBundleRef, planRef, scopeHash, decision, source, confirmation
            ),
        )

    @server.tool()
    async def horus_start_run(
        projectRef: str,
        planRef: str,
        reviewRef: str | None,
        requestId: str,
        environment: str = "test",
    ) -> Result:
        """Start once after review. Retry a lost response with the SAME requestId and parameters."""
        return await invoke(
            projectRef, lambda: runs.start(projectRef, planRef, reviewRef, environment, requestId)
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_get_run(projectRef: str, runId: str) -> Result:
        """Read persisted run state and final report reference, including after reconnecting."""
        return await invoke(projectRef, lambda: runs.get(projectRef, runId))

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_wait_run(projectRef: str, runId: str, waitMs: int = 30000) -> Result:
        """Wait at most 30s. Wait timeout/cancellation does not stop the independent Worker."""
        if not 0 <= waitMs <= 30000:
            raise ToolError("invalid_wait")
        payload = await runs.wait(projectRef, runId, waitMs)
        return Result(
            projectRef=projectRef,
            status=payload["status"],
            summary=payload.get("verdict", payload["status"]),
            payload=payload,
            refs={k: payload[k] for k in ("artifactRef", "detailsArtifactRef") if k in payload},
        )

    @server.tool()
    async def horus_attach_resource_metrics(
        projectRef: str, runId: str, metrics: ResourceMetrics
    ) -> Result:
        """Attach opted-in SSH observations to a finished report; HTTP verdict stays unchanged.

        Collect only within plan.resourceMonitoring. Never invent samples or upload credentials.
        """
        return await invoke(projectRef, lambda: runs.attach_metrics(projectRef, runId, metrics))

    @server.tool()
    async def horus_cancel_run(projectRef: str, runId: str) -> Result:
        """Request cancellation, followed by bounded cleanup and an automatic report."""
        return await invoke(projectRef, lambda: runs.cancel(projectRef, runId))

    @server.tool()
    async def horus_cleanup_run(projectRef: str, runId: str, requestId: str) -> Result:
        """Start a bounded cleanup attempt for a finished original run, without business writes.

        Uses its existing approval, nonsecret ledger values and cleanupAllow only.
        Reauthentication is allowed only when explicitly approved for cleanup.
        Lost credentials and unresolved writes stay visible. Retry with the same requestId.
        """
        return await invoke(projectRef, lambda: runs.cleanup(projectRef, runId, requestId))

    return server
