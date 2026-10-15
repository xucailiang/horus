from pathlib import Path
from typing import Annotated

import typer

from horus import __version__

app = typer.Typer(no_args_is_help=True)


@app.command()
def mcp(workspace: Annotated[Path, typer.Option()],
        read_root: Annotated[list[Path] | None, typer.Option()] = None) -> None:
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
