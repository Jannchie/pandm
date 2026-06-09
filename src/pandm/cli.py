"""pandm CLI: `pandm ui` (local dashboard), `pandm server` (cloud mode), `pandm ls`."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .credentials import DEFAULT_SERVER
from .storage import LocalStore, resolve_dir

app = typer.Typer(
    help="pandm — beautiful, local-first experiment tracking.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DirOption = typer.Option(None, "--dir", "-d", help="Data directory (default: ./.pandm or $PANDM_DIR).")


def _banner(url: str, data_dir: Path, mode: str) -> None:
    console.print(f"\n[bold]pandm[/bold] [dim]v{__version__}[/dim] · {mode}")
    console.print(f"[bold cyan]{url}[/bold cyan] [dim]· data: {data_dir}[/dim]\n")


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
    """Start a pandm server for cloud deployment (SDKs report via `pandm login` or PANDM_REMOTE)."""
    import os as _os

    import uvicorn

    from .server import create_app

    data_dir = resolve_dir(directory)
    multi_user = bool(_os.environ.get("GITHUB_CLIENT_ID") and _os.environ.get("GITHUB_CLIENT_SECRET"))
    mode = "multi-user (GitHub OAuth)" if multi_user else "server mode" + (" · api-key on" if api_key else "")
    _banner(f"http://{host}:{port}", data_dir, mode)
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
def show(
    run_id: str,
    directory: Optional[Path] = DirOption,
) -> None:
    """Show a run's config, summary and logged metrics."""
    import datetime as dt

    store = LocalStore(resolve_dir(directory))
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)
    status_style = {"running": "bold cyan", "finished": "green", "crashed": "red"}
    created = dt.datetime.fromtimestamp(run["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
    console.print(f"\n[bold]{run['name']}[/bold] [dim]({run['id']})[/dim]")
    console.print(
        f"[dim]project[/dim] {run['project']}"
        f"  [dim]status[/dim] [{status_style.get(run['status'], 'white')}]{run['status']}[/]"
        f"  [dim]created[/dim] {created}"
    )
    if run["config"]:
        console.print("\n[bold dim]CONFIG[/bold dim]")
        for k, v in sorted(run["config"].items()):
            console.print(f"  [dim]{k}[/dim] = {v}")
    keys = store.metric_keys(run_id)
    if keys:
        console.print("\n[bold dim]METRICS[/bold dim]")
        table = Table(box=None, header_style="bold dim", pad_edge=False)
        for col in ("KEY", "POINTS", "LAST STEP", "LAST VALUE"):
            table.add_column(col)
        for k in keys:
            last = run["summary"].get(k["key"])
            table.add_row(
                f"[dim]{k['key']}[/dim]", str(k["points"]), str(k["last_step"]),
                f"{last:.6g}" if last is not None else "-",
            )
        console.print(table)
    media = store.list_media(run_id)
    if media:
        console.print(f"\n[dim]{len(media)} media files — pandm ui to browse[/dim]")


@app.command()
def export(
    run_id: str,
    keys: Optional[list[str]] = typer.Option(None, "--key", "-k", help="Metric keys to export (default: all)."),
    as_json: bool = typer.Option(False, "--json", help="Emit a JSON object instead of CSV rows."),
    directory: Optional[Path] = DirOption,
) -> None:
    """Export full metric series to stdout — CSV (key,step,value,ts) or JSON."""
    import csv
    import json
    import sys

    store = LocalStore(resolve_dir(directory))
    if store.get_run(run_id) is None:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)
    selected = list(keys) if keys else [k["key"] for k in store.metric_keys(run_id)]
    series = {k: store.metric_series(run_id, k, max_points=2**31) for k in selected}
    if as_json:
        json.dump(series, sys.stdout)
        sys.stdout.write("\n")
        return
    writer = csv.writer(sys.stdout)
    writer.writerow(["key", "step", "value", "ts"])
    for k, s in series.items():
        writer.writerows(zip([k] * len(s["steps"]), s["steps"], s["values"], s["ts"]))


@app.command()
def delete(
    run_id: str,
    directory: Optional[Path] = DirOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    local_only: bool = typer.Option(False, "--local-only", help="Keep the cloud copy (if signed in)."),
) -> None:
    """Delete a run and its media files — locally and, when signed in, on the cloud server."""
    from . import credentials

    store = LocalStore(resolve_dir(directory))
    run = store.get_run(run_id)
    creds = None if local_only else credentials.load()
    if run is None and creds is None:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)
    name = run["name"] if run else run_id
    if not yes and not typer.confirm(f"delete run {name} ({run_id})?"):
        raise typer.Exit(0)

    deleted = False
    if run is not None:
        store.delete_run(run_id)
        deleted = True
        console.print(f"[dim]deleted {run_id} locally[/dim]")
    if creds:
        import httpx

        try:
            resp = httpx.delete(
                f"{creds['server']}/api/runs/{run_id}", headers={"x-api-key": creds["api_key"]}, timeout=10
            )
        except httpx.HTTPError as exc:
            console.print(f"[yellow]cloud delete failed ({exc}) — run pandm delete again later[/yellow]")
        else:
            if resp.status_code == 200:
                deleted = True
                console.print(f"[dim]deleted {run_id} on {creds['server']}[/dim]")
            elif resp.status_code != 404:  # 404 = never synced or already gone
                console.print(f"[yellow]cloud delete failed: HTTP {resp.status_code}[/yellow]")
    if not deleted:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)


@app.command()
def login(
    server: str = typer.Argument(DEFAULT_SERVER, help="pandm server URL (default: the hosted pandm cloud)."),
    key: Optional[str] = typer.Option(None, "--key", help="Paste an API key directly (skips the browser)."),
) -> None:
    """Sign in to a pandm server (browser approval, like `gh auth login`)."""
    from . import credentials

    saved = credentials.device_login(server, key=key, echo=lambda m: console.print(m, highlight=False))
    if saved is None:
        raise typer.Exit(1)
    credentials.set_opted_out()  # signed in -> stop offering the login prompt in pandm.init()
    console.print(
        f"[green]logged in as [bold]{saved['login']}[/bold][/green] [dim]({credentials.cred_path()})[/dim]"
    )
    console.print("[dim]pandm.init() now syncs runs to this server; PANDM_NO_SYNC=1 opts out per-run[/dim]")


@app.command()
def logout() -> None:
    """Forget saved credentials (runs stay local-first)."""
    from . import credentials

    console.print("[dim]logged out[/dim]" if credentials.clear() else "[dim]not logged in[/dim]")


@app.command()
def sync(
    run_ids: Optional[list[str]] = typer.Argument(None, help="Specific runs to sync (default: all cloud-tracked runs with pending data)."),
    directory: Optional[Path] = DirOption,
    all_runs: bool = typer.Option(False, "--all", help="Also sync local-only runs that were never cloud-tracked."),
) -> None:
    """Push unsynced local runs to the signed-in server (like `wandb sync`)."""
    from . import credentials
    from .sync import sync_all

    creds = credentials.load()
    if creds is None:
        console.print("[red]not logged in — run pandm login first[/red]")
        raise typer.Exit(1)

    report = sync_all(
        resolve_dir(directory),
        creds["server"],
        creds["api_key"],
        run_ids=list(run_ids) if run_ids else None,
        track_all=all_runs,
        progress=lambda rid, outcome: console.print(f"  [dim]{rid}[/dim] {outcome}"),
    )
    if not report:
        console.print("[dim]nothing to sync[/dim]")
    else:
        synced = sum(1 for _, outcome in report if outcome == "synced")
        console.print(f"[green]{synced}/{len(report)} runs synced[/green] -> {creds['server']}")


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"pandm v{__version__}")
