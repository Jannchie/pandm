"""pandm CLI: `pandm ui` (local dashboard), `pandm server` (cloud mode), `pandm ls`."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .storage import LocalStore, resolve_dir

app = typer.Typer(
    help="pandm — beautiful, local-first experiment tracking.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DirOption = typer.Option(None, "--dir", "-d", help="Data directory (default: ./.pandm or $PANDM_DIR).")


def _banner(url: str, data_dir: Path, mode: str) -> None:
    console.print(
        Panel.fit(
            f"[bold]pandm[/bold] [dim]v{__version__}[/dim] · {mode}\n\n"
            f"  [bold cyan]{url}[/bold cyan]\n"
            f"  [dim]data: {data_dir}[/dim]",
            border_style="bright_black",
            padding=(1, 3),
        )
    )


@app.command()
def ui(
    directory: Optional[Path] = DirOption,
    port: int = typer.Option(7878, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the dashboard in a browser."),
) -> None:
    """Start the local dashboard."""
    import uvicorn

    from .server import create_app

    data_dir = resolve_dir(directory)
    url = f"http://{host}:{port}"
    _banner(url, data_dir, "local dashboard")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=[url]).start()
    uvicorn.run(create_app(data_dir), host=host, port=port, log_level="warning")


@app.command()
def server(
    directory: Optional[Path] = DirOption,
    port: int = typer.Option(7878, "--port", "-p"),
    host: str = typer.Option("0.0.0.0", "--host"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", envvar="PANDM_API_KEY", help="Require x-api-key on write endpoints."
    ),
) -> None:
    """Start a pandm server for cloud deployment (SDKs report via PANDM_REMOTE)."""
    import uvicorn

    from .server import create_app

    data_dir = resolve_dir(directory)
    _banner(f"http://{host}:{port}", data_dir, "server mode" + (" · api-key on" if api_key else ""))
    uvicorn.run(create_app(data_dir, api_key=api_key), host=host, port=port, log_level="info")


@app.command("ls")
def list_runs(
    project: Optional[str] = typer.Option(None, "--project", "-P"),
    directory: Optional[Path] = DirOption,
) -> None:
    """List runs in the terminal."""
    store = LocalStore(resolve_dir(directory))
    runs = store.list_runs(project)
    if not runs:
        console.print("[dim]no runs yet — call pandm.init() in your training script[/dim]")
        return

    import datetime as dt

    status_style = {"running": "bold cyan", "finished": "green", "crashed": "red"}
    table = Table(box=None, header_style="bold dim", pad_edge=False)
    for col in ("ID", "NAME", "PROJECT", "STATUS", "CREATED", "DURATION"):
        table.add_column(col)
    for run in runs:
        created = dt.datetime.fromtimestamp(run["created_at"]).strftime("%Y-%m-%d %H:%M")
        end = run["finished_at"] or run["updated_at"]
        mins, secs = divmod(int(max(0, end - run["created_at"])), 60)
        hours, mins = divmod(mins, 60)
        duration = f"{hours}h{mins:02d}m" if hours else (f"{mins}m{secs:02d}s" if mins else f"{secs}s")
        table.add_row(
            f"[dim]{run['id']}[/dim]",
            run["name"],
            run["project"],
            f"[{status_style.get(run['status'], 'white')}]{run['status']}[/]",
            created,
            duration,
        )
    console.print(table)


@app.command()
def delete(
    run_id: str,
    directory: Optional[Path] = DirOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a run and its media files."""
    store = LocalStore(resolve_dir(directory))
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"delete run {run['name']} ({run_id})?"):
        raise typer.Exit(0)
    store.delete_run(run_id)
    console.print(f"[dim]deleted {run_id}[/dim]")


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"pandm v{__version__}")
