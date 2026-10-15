import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from horus import __version__
from horus.core.bundles import export_review, import_review
from horus.core.runs import TERMINAL, Runs
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write

app = typer.Typer(no_args_is_help=True)
plans = typer.Typer(no_args_is_help=True)
app.add_typer(plans, name="plan")


def emit(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = action()
    except HorusError as exc:
        typer.echo(
            json.dumps(
                {
                    "status": "blocked" if exc.code.startswith("review_") else "error",
                    "code": exc.code,
                    "summary": str(exc),
                }
            )
        )
        raise typer.Exit(3 if exc.code.startswith("review_") else 2) from exc
    except (ValueError, OSError) as exc:
        typer.echo(
            json.dumps({"status": "error", "code": "input_error", "summary": type(exc).__name__})
        )
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(result, ensure_ascii=False))
    return result


async def wait_finished(runs: Runs, project: str, run_id: str) -> dict[str, Any]:
    while True:
        result = await runs.wait(project, run_id)
        if result["status"] in TERMINAL:
            return result


@app.command()
def run(
    workspace: Annotated[Path, typer.Option()],
    project_ref: Annotated[str, typer.Option()],
    plan_ref: Annotated[str, typer.Option()],
    review_ref: Annotated[str | None, typer.Option()] = None,
    environment: Annotated[str, typer.Option("--env")] = "test",
    request_id: Annotated[str | None, typer.Option()] = None,
    wait: Annotated[bool, typer.Option("--wait/--no-wait")] = True,
) -> None:
    """Replay a reviewed plan and return its automatic report; no MCP or model is loaded."""
    runs = Runs(Service(workspace))

    def execute() -> dict[str, Any]:
        result = runs.start(
            project_ref, plan_ref, review_ref, environment, request_id or uuid.uuid4().hex
        )
        return asyncio.run(wait_finished(runs, project_ref, result["runId"])) if wait else result

    result = emit(execute)
    if wait:
        status = result.get("verdict", "inconclusive")
        code = (
            2 if status == "error" else 1 if status == "failed" else 0 if status == "passed" else 3
        )
        raise typer.Exit(code)


@plans.command("export")
def export_plan(
    workspace: Annotated[Path, typer.Option()],
    project_ref: Annotated[str, typer.Option()],
    review_ref: Annotated[str, typer.Option()],
    out: Annotated[Path, typer.Option()],
) -> None:
    """Export a closed, already-reviewed package for trusted CI input, without credential values."""

    def save() -> dict[str, Any]:
        payload = export_review(Service(workspace), project_ref, review_ref)
        atomic_write(out, payload)
        return {"status": "exported", "path": str(out.resolve()), "reviewRef": review_ref}

    emit(save)


@plans.command("import")
def import_plan(
    workspace: Annotated[Path, typer.Option()], bundle: Annotated[Path, typer.Option()]
) -> None:
    """Verify and import an existing approval; retain project identity and fixed target."""
    emit(lambda: import_review(Service(workspace), json.loads(bundle.read_bytes())))


@plans.command("approve")
def approve(
    workspace: Annotated[Path, typer.Option()],
    project_ref: Annotated[str, typer.Option()],
    bundle_ref: Annotated[str, typer.Option()],
) -> None:
    """Display the exact bundle and record the human's explicit CLI confirmation."""
    service = Service(workspace)
    bundle = service.project(project_ref).get("bundles", bundle_ref)
    offset = 0
    while True:
        page = service.get_artifact(project_ref, bundle["artifactRef"], offset)
        typer.echo(page["content"])
        if page["nextOffset"] is None:
            break
        offset = page["nextOffset"]
    confirmation = typer.prompt("确认以上计划请输入“我确定”")
    if confirmation.strip() != "我确定":
        raise typer.Exit(3)
    emit(
        lambda: service.record_decision(
            project_ref,
            bundle_ref,
            bundle["planRef"],
            bundle["scopeHash"],
            "approved",
            "CLI",
            confirmation,
        )
    )


@app.command()
def status(
    workspace: Annotated[Path, typer.Option()],
    project_ref: Annotated[str, typer.Option()],
    run_id: Annotated[str, typer.Option()],
) -> None:
    """Read persisted run status and its report reference."""
    emit(lambda: Runs(Service(workspace)).get(project_ref, run_id))


@app.command()
def mcp(
    workspace: Annotated[Path, typer.Option()],
    read_root: Annotated[list[Path] | None, typer.Option()] = None,
) -> None:
    """Start the optional stdio server; an empty workspace is valid."""
    from horus.mcp.server import create_server

    create_server(workspace, read_root).run(transport="stdio", show_banner=False)


@app.command()
def version() -> None:
    """Print the installed horus version."""
    typer.echo(__version__)


@app.command()
def skill() -> None:
    """Print the bundled skill location for installation in your chosen host."""
    path = Path(__file__).parent / "skill" / "SKILL.md"
    if not path.exists():
        path = Path(__file__).parents[2] / "skills" / "horus" / "SKILL.md"
    typer.echo(str(path.resolve()))
