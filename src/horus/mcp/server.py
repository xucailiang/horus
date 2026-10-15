import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field, ValidationError

from horus import __version__
from horus.core.models import Model, Plan, ProjectConfig
from horus.core.service import Service
from horus.core.store import HorusError

INSTRUCTIONS = """horus tests HTTP APIs with human-reviewed, immutable plans.
Call horus_get_capabilities, open a project, stage config, register/read documentation
and code, then generate and save your own business-aware plan using horus_get_plan_schema.
Prepare and read the review artifact. Show its exact version, target, writes, cleanup,
load budget and unknowns to the user. Only record an explicit human decision on THAT
version. A general test request is not approval. Never submit credential values.
Use explicit projectRef throughout. The installed horus skill adds workflow guidance;
MCP works without the skill. Supported capabilities and remaining gaps are explicit.
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
    server = FastMCP("horus", instructions=INSTRUCTIONS, version=__version__)

    async def invoke(project_ref: str | None, action: Callable[[], dict[str, Any]]) -> Result:
        try:
            payload = await asyncio.to_thread(action)
        except HorusError as exc:
            if exc.code.startswith("review_") or exc.code == "missing_target":
                return Result(projectRef=project_ref, status="blocked", summary=str(exc),
                              issues=[Issue(code=exc.code, location="", reason=str(exc),
                                            nextAction="Resolve the review or input issue",
                                            needsUserInput=True)])
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            raise ToolError(str(exc.errors(include_input=False, include_url=False))) from exc
        except (OSError, ValueError) as exc:
            raise ToolError(f"input_error: {type(exc).__name__}") from exc
        refs = {k: v for k, v in payload.items() if k.endswith("Ref") and isinstance(v, str)}
        return Result(projectRef=payload.get("projectRef", project_ref),
                      status=payload.get("status", "ready"), summary=payload.get("summary", "OK"),
                      refs=refs, payload=payload)

    @server.tool()
    async def horus_get_capabilities() -> Result:
        """Check readiness, supported capabilities and workflow before opening a project."""
        return Result(status="ready", summary="Plan and review capabilities are available",
                      payload={"version": __version__, "python": "3.12",
                               "sources": ["local-openapi", "local-markdown", "local-code"],
                               "steps": ["http", "load"], "executionReady": False,
                               "gaps": ["execution adapters and Worker are under implementation",
                                        "Git/URL, identity, branch and poll support are pending"],
                               "readRoots": [str(p) for p in service.read_roots]},
                      nextActions=["horus_open_project", "horus_get_plan_schema"])

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_get_plan_schema() -> Result:
        """Read the exact plan JSON Schema before generating a plan; no golden plan is supplied."""
        return Result(status="ready", summary="Plan schema", payload=Plan.model_json_schema())

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_list_projects(offset: int = 0, limit: int = 20) -> Result:
        """List persisted projects; page size is bounded to 100."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ToolError("invalid_page")
        projects = service.list_projects()
        return Result(status="ready", summary="Projects", payload={
            "projects": projects[offset:offset + limit],
            "nextOffset": offset + limit if offset + limit < len(projects) else None})

    @server.tool()
    async def horus_open_project(name: str, createIfMissing: bool = False) -> Result:
        """Create or restore a project by name or projectRef, including saved asset references."""
        return await invoke(None, lambda: service.open_project(
            name, create_if_missing=createIfMissing))

    @server.tool()
    async def horus_stage_project_config(projectRef: str, config: ProjectConfig) -> Result:
        """Save an immutable configuration draft. This does not authorize probes or execution."""
        return await invoke(projectRef, lambda: service.stage_config(
            projectRef, config.model_dump(mode="json")))

    @server.tool()
    async def horus_register_source(projectRef: str, kind: str, uri: str) -> Result:
        """Register a local openapi/markdown/code file within a server read root."""
        return await invoke(projectRef, lambda: service.register_source(projectRef, kind, uri))

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_get_context(projectRef: str, sourceRef: str, offset: int = 0,
                                limit: int = 100) -> Result:
        """Read numbered documentation/code and operation catalog. Evidence uses sourceRef#Lx."""
        return await invoke(projectRef, lambda: service.get_context(
            projectRef, sourceRef, offset, limit))

    @server.tool()
    async def horus_save_plan(projectRef: str, plan: Plan) -> Result:
        """Save and validate your generated plan. Saving never approves it."""
        return await invoke(projectRef, lambda: service.save_plan(
            projectRef, plan.model_dump(mode="json")))

    @server.tool(annotations={"readOnlyHint": True})
    async def horus_validate_plan(projectRef: str, planRef: str, configRef: str | None = None,
                                 environment: str = "test") -> Result:
        """Check sources, coverage, variables, scope and worst-case request budget."""
        return await invoke(projectRef, lambda: service.validate_plan(
            projectRef, planRef, configRef, environment))

    @server.tool()
    async def horus_prepare_review(projectRef: str, planRef: str, configRef: str,
                                  environment: str = "test") -> Result:
        """Render a fixed review bundle; read and show it before requesting human confirmation."""
        return await invoke(projectRef, lambda: service.prepare_review(
            projectRef, planRef, configRef, environment))

    @server.tool()
    async def horus_get_artifact(projectRef: str, artifactRef: str, offset: int = 0,
                                limit: int = 200) -> Result:
        """Read bounded review/report content. Reading review material records retrieval only."""
        return await invoke(projectRef, lambda: service.get_artifact(
            projectRef, artifactRef, offset, limit))

    @server.tool()
    async def horus_record_review_decision(projectRef: str, reviewBundleRef: str,
                                            planRef: str, scopeHash: str, decision: str,
                                            source: str, confirmation: str) -> Result:
        """Record an actual human decision after displaying this version; never invent approval.

        source is conversation or CLI. confirmation is the real message reference or excerpt.
        This trusted-local record is not independent identity authentication.
        """
        if source not in {"conversation", "CLI"}:
            raise ToolError("invalid_decision_source")
        return await invoke(projectRef, lambda: service.record_decision(
            projectRef, reviewBundleRef, planRef, scopeHash, decision, source, confirmation))

    return server
