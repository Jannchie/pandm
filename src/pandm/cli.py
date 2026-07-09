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
)
console = Console()

DirOption = typer.Option(
    None, "--dir", "-d", help="Data directory (default: ./.pandm or $PANDM_DIR)."
)
JsonOption = typer.Option(
    False, "--json", help="Emit machine-readable JSON instead of a table."
)


def _banner(url: str, data_dir: Path, mode: str) -> None:
    console.print(f"\n[bold]pandm[/bold] [dim]v{__version__}[/dim] · {mode}")
    console.print(f"[bold cyan]{url}[/bold cyan] [dim]· data: {data_dir}[/dim]\n")


def _emit_json(obj: object) -> None:
    """Print parseable JSON to stdout (no Rich markup, default=str for any leftovers)."""
    import json
    import sys

    json.dump(obj, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _sort_runs(runs: list[dict], sort_by: str | None, ascending: bool) -> list[dict]:
    """Order runs by a metric aggregate, e.g. `--sort-by val/acc` (max) or `loss:min`.

    The aggregate is read from each run's precomputed `stats[key]` ({min,max,last,count}),
    so no series scan is needed. Runs missing the key sort to the end either way.
    """
    if not sort_by:
        return runs
    key, _, agg = sort_by.partition(":")
    agg = agg or "max"
    if agg not in ("min", "max", "last"):
        console.print(
            f"[red]bad --sort-by aggregate {agg!r} — use min, max or last[/red]"
        )
        raise typer.Exit(2)

    def metric_value(run: dict):
        return run.get("stats", {}).get(key, {}).get(agg)

    present = [r for r in runs if metric_value(r) is not None]
    missing = [r for r in runs if metric_value(r) is None]
    present.sort(key=metric_value, reverse=not ascending)
    return present + missing


@app.command()
def ui(
    directory: Optional[Path] = DirOption,
    port: int = typer.Option(7878, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the dashboard in a browser."
    ),
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
        None,
        "--api-key",
        envvar="PANDM_API_KEY",
        help="Require x-api-key on write endpoints.",
    ),
) -> None:
    """Start a pandm server for cloud deployment (SDKs report via `pandm login` or PANDM_REMOTE)."""
    import os as _os

    import uvicorn

    from .server import create_app

    data_dir = resolve_dir(directory)
    multi_user = bool(
        _os.environ.get("GITHUB_CLIENT_ID") and _os.environ.get("GITHUB_CLIENT_SECRET")
    )
    mode = (
        "multi-user (GitHub OAuth)"
        if multi_user
        else "server mode" + (" · api-key on" if api_key else "")
    )
    _banner(f"http://{host}:{port}", data_dir, mode)
    uvicorn.run(
        create_app(data_dir, api_key=api_key), host=host, port=port, log_level="info"
    )


@app.command("ls")
def list_runs(
    project: Optional[str] = typer.Option(
        None, "--project", "-P", help="Filter to one project."
    ),
    status: Optional[str] = typer.Option(
        None, "--status", "-s", help="Filter by status (running, finished, crashed)."
    ),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Keep only runs carrying this tag (repeat to AND)."
    ),
    sort_by: Optional[str] = typer.Option(
        None,
        "--sort-by",
        help="Order by a metric aggregate, e.g. 'val/acc' (max) or 'loss:min'.",
    ),
    ascending: bool = typer.Option(
        False, "--asc", help="Sort ascending (smallest first)."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n", help="Keep only the first N runs."
    ),
    as_json: bool = JsonOption,
    directory: Optional[Path] = DirOption,
) -> None:
    """List runs — filter by project/status, sort by a metric, with --json for tooling."""
    store = LocalStore(resolve_dir(directory))
    runs = store.list_runs(project)
    if status:
        runs = [r for r in runs if r["status"] == status]
    if tags:
        runs = [r for r in runs if all(t in r["tags"] for t in tags)]
    runs = _sort_runs(runs, sort_by, ascending)
    if limit is not None:
        runs = runs[:limit]
    if as_json:
        _emit_json(runs)
        return
    if not runs:
        console.print(
            "[dim]no runs yet — call pandm.init() in your training script[/dim]"
        )
        return

    import datetime as dt

    status_style = {"running": "bold cyan", "finished": "green", "crashed": "red"}
    table = Table(box=None, header_style="bold dim", pad_edge=False)
    for col in ("ID", "NAME", "PROJECT", "STATUS", "TAGS", "CREATED", "DURATION"):
        table.add_column(col)
    for run in runs:
        created = dt.datetime.fromtimestamp(run["created_at"]).strftime(
            "%Y-%m-%d %H:%M"
        )
        end = run["finished_at"] or run["updated_at"]
        mins, secs = divmod(int(max(0, end - run["created_at"])), 60)
        hours, mins = divmod(mins, 60)
        duration = (
            f"{hours}h{mins:02d}m"
            if hours
            else (f"{mins}m{secs:02d}s" if mins else f"{secs}s")
        )
        run_status = run["status"]
        if run_status == "running" and run["progress"] and run["progress_total"]:
            run_status += f" {run['progress'] / run['progress_total']:.0%}"
        table.add_row(
            f"[dim]{run['id']}[/dim]",
            run["name"],
            run["project"],
            f"[{status_style.get(run['status'], 'white')}]{run_status}[/]",
            f"[dim]{', '.join(run['tags'])}[/dim]",
            created,
            duration,
        )
    console.print(table)


@app.command()
def projects(
    as_json: bool = JsonOption,
    directory: Optional[Path] = DirOption,
) -> None:
    """List projects with run counts, most recently active first."""
    import datetime as dt

    store = LocalStore(resolve_dir(directory))
    rows = store.list_projects()
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print(
            "[dim]no runs yet — call pandm.init() in your training script[/dim]"
        )
        return
    table = Table(box=None, header_style="bold dim", pad_edge=False)
    for col in ("PROJECT", "RUNS", "LAST ACTIVE"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            row["project"],
            str(row["runs"]),
            dt.datetime.fromtimestamp(row["last_active"]).strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@app.command()
def tag(
    run_id: str,
    add: Optional[list[str]] = typer.Argument(None, help="Tags to add."),
    remove: Optional[list[str]] = typer.Option(
        None, "--rm", help="Tags to remove (repeatable)."
    ),
    clear: bool = typer.Option(False, "--clear", help="Drop every tag first."),
    directory: Optional[Path] = DirOption,
) -> None:
    """Add or remove tags on a run — `pandm tag <id> good-lr --rm wip`.

    Edits local metadata only; a cloud copy keeps its original tags."""
    store = LocalStore(resolve_dir(directory))
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)
    tags = [] if clear else list(run["tags"])
    tags = [t for t in tags if t not in (remove or [])]
    tags += [t for t in (add or []) if t not in tags]
    store.update_run_meta(run_id, tags=tags)
    updated = store.get_run(run_id)
    shown = ", ".join(updated["tags"] if updated else tags) or "[dim](none)[/dim]"
    console.print(f"{run['name']} [dim]({run_id})[/dim] tags: {shown}")


@app.command()
def show(
    run_id: str,
    as_json: bool = JsonOption,
    directory: Optional[Path] = DirOption,
) -> None:
    """Show a run's config, summary and logged metrics (--json adds metric keys + media paths)."""
    import datetime as dt

    store = LocalStore(resolve_dir(directory))
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)
    if as_json:
        run["metric_keys"] = store.metric_keys(run_id)  # [{key, points, last_step}]
        media = store.list_media(run_id)
        for m in media:
            path = store.media_path(run_id, m["filename"])
            m["path"] = (
                str(path) if path else None
            )  # absolute path to open/Read the PNG
        run["media"] = media
        _emit_json(run)
        return
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
    if run["summary"]:
        console.print("\n[bold dim]SUMMARY[/bold dim]")
        for k, v in sorted(run["summary"].items()):
            console.print(f"  [dim]{k}[/dim] = {v}")
    keys = store.metric_keys(run_id)
    if keys:
        console.print("\n[bold dim]METRICS[/bold dim]")
        table = Table(box=None, header_style="bold dim", pad_edge=False)
        for col in ("KEY", "POINTS", "LAST STEP", "LAST VALUE", "MIN", "MAX"):
            table.add_column(col)
        fmt = lambda v: f"{v:.6g}" if v is not None else "-"  # noqa: E731
        for k in keys:
            stat = run["stats"].get(k["key"], {})
            table.add_row(
                f"[dim]{k['key']}[/dim]",
                str(k["points"]),
                str(k["last_step"]),
                fmt(stat.get("last")),
                fmt(stat.get("min")),
                fmt(stat.get("max")),
            )
        console.print(table)
    media = store.list_media(run_id)
    if media:
        console.print(f"\n[dim]{len(media)} media files — pandm ui to browse[/dim]")


@app.command()
def export(
    run_id: str,
    keys: Optional[list[str]] = typer.Option(
        None, "--key", "-k", help="Metric keys to export (default: all)."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit a JSON object instead of CSV rows."
    ),
    histograms: bool = typer.Option(
        False,
        "--histograms",
        help="Export logged distributions instead of scalar metrics.",
    ),
    directory: Optional[Path] = DirOption,
) -> None:
    """Export full metric series to stdout — CSV (key,step,value,ts) or JSON.

    With --histograms, exports every binned distribution instead: JSON keeps the
    raw {steps, bins, counts} arrays; CSV flattens to one row per bin
    (key,step,bin_lo,bin_hi,count,ts)."""
    import csv
    import json
    import sys

    store = LocalStore(resolve_dir(directory))
    if store.get_run(run_id) is None:
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)

    if histograms:
        selected = (
            list(keys) if keys else [k["key"] for k in store.histogram_keys(run_id)]
        )
        hists = {
            k: store.histogram_series(run_id, k, max_steps=2**31) for k in selected
        }
        if as_json:
            json.dump(hists, sys.stdout)
            sys.stdout.write("\n")
            return
        writer = csv.writer(sys.stdout)
        writer.writerow(["key", "step", "bin_lo", "bin_hi", "count", "ts"])
        for k, s in hists.items():
            for step, edges, counts, ts in zip(
                s["steps"], s["bins"], s["counts"], s["ts"]
            ):
                for lo, hi, count in zip(edges, edges[1:], counts):
                    writer.writerow([k, step, lo, hi, count, ts])
        return

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
def ingest(
    csv_path: Path = typer.Argument(
        ..., help="Metrics CSV another trainer writes (e.g. output/metrics.csv)."
    ),
    project: str = typer.Option("default", "--project", "-P"),
    name: Optional[str] = typer.Option(
        None, "--name", help="Run name (default: auto-generated)."
    ),
    step_column: Optional[str] = typer.Option(
        None,
        "--step-column",
        help="Column holding the metric step (e.g. 'epoch'); omit for a row counter.",
    ),
    prefix: str = typer.Option(
        "", "--prefix", help="Prepended to every metric key (e.g. 'val/')."
    ),
    include: Optional[list[str]] = typer.Option(
        None, "--include", help="Only log these source columns (repeatable)."
    ),
    exclude: Optional[list[str]] = typer.Option(
        None, "--exclude", help="Skip these source columns (repeatable)."
    ),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Tag the created run (repeatable)."
    ),
    watch: bool = typer.Option(
        False, "--watch", help="Keep following the file for new rows until Ctrl-C."
    ),
    interval: float = typer.Option(
        5.0, "--interval", help="Poll interval in seconds with --watch."
    ),
    directory: Optional[Path] = DirOption,
) -> None:
    """Turn a metrics CSV into a pandm run without writing Python.

    One-shot by default: every numeric row becomes a logged step and the run
    finishes. With --watch, the run stays live and new rows appear as the
    trainer appends them — point it at the CSV before or during training."""
    import threading as _threading

    import pandm

    if not watch and not csv_path.is_file():
        console.print(f"[red]{csv_path} not found[/red]")
        raise typer.Exit(1)
    run = pandm.init(
        project=project,
        name=name,
        config={"source_csv": str(csv_path)},
        tags=list(tags) if tags else None,
        directory=directory,
    )
    kwargs: dict = {
        "step_column": step_column,
        "include": list(include) if include else None,
        "exclude": list(exclude) if exclude else None,
        "prefix": prefix,
    }
    try:
        if watch:
            run.watch_csv(csv_path, interval=interval, **kwargs)
            console.print(
                f"[dim]following {csv_path} every {interval:g}s — Ctrl-C to finish[/dim]"
            )
            _threading.Event().wait()
        else:
            rows = run.ingest_csv(csv_path, **kwargs)
            console.print(f"[green]{rows} rows ingested[/green] -> run {run.id}")
    except KeyboardInterrupt:
        console.print("[dim]stopping — draining trailing rows[/dim]")
    finally:
        run.finish()


@app.command()
def compare(
    run_ids: list[str] = typer.Argument(..., help="Two or more run IDs to compare."),
    as_json: bool = JsonOption,
    directory: Optional[Path] = DirOption,
) -> None:
    """Compare config, summary and per-metric stats across runs, side by side."""
    store = LocalStore(resolve_dir(directory))
    runs = []
    for rid in run_ids:
        run = store.get_run(rid)
        if run is None:
            console.print(f"[red]run {rid} not found[/red]")
            raise typer.Exit(1)
        runs.append(run)

    config_keys = sorted({k for r in runs for k in r["config"]})
    summary_keys = sorted({k for r in runs for k in r["summary"]})
    stat_keys = sorted({k for r in runs for k in r["stats"]})

    if as_json:
        _emit_json(
            {
                "runs": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "project": r["project"],
                        "status": r["status"],
                    }
                    for r in runs
                ],
                "config": {k: [r["config"].get(k) for r in runs] for k in config_keys},
                "summary": {
                    k: [r["summary"].get(k) for r in runs] for k in summary_keys
                },
                # stats[key][i] = {min, max, last, count} for run i — .last is the latest value
                "stats": {k: [r["stats"].get(k) for r in runs] for k in stat_keys},
            }
        )
        return

    fmt = lambda v: (  # noqa: E731
        f"{v:.6g}" if isinstance(v, (int, float)) else ("-" if v is None else str(v))
    )
    table = Table(box=None, header_style="bold dim", pad_edge=False)
    table.add_column("")
    for r in runs:
        table.add_column(f"{r['name']}\n[dim]{r['id']}[/dim]")
    table.add_row("[dim]status[/dim]", *[r["status"] for r in runs])
    for label, keys, getter in (
        ("CONFIG", config_keys, lambda r, k: r["config"].get(k)),
        ("SUMMARY", summary_keys, lambda r, k: r["summary"].get(k)),
        (
            "METRIC (last)",
            stat_keys,
            lambda r, k: (r["stats"].get(k) or {}).get("last"),
        ),
    ):
        if not keys:
            continue
        table.add_section()
        table.add_row(f"[bold dim]{label}[/bold dim]")
        for k in keys:
            table.add_row(f"[dim]{k}[/dim]", *[fmt(getter(r, k)) for r in runs])
    console.print(table)


def _cloud_delete(creds: dict, path: str) -> bool:
    """DELETE one resource on the signed-in server; 404 counts as already gone."""
    import httpx

    try:
        resp = httpx.delete(
            f"{creds['server']}{path}",
            headers={"x-api-key": creds["api_key"]},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        console.print(
            f"[yellow]cloud delete failed ({exc}) — run pandm delete again later[/yellow]"
        )
        return False
    if resp.status_code == 200:
        return True
    if resp.status_code != 404:  # 404 = never synced or already gone
        console.print(f"[yellow]cloud delete failed: HTTP {resp.status_code}[/yellow]")
    return False


@app.command()
def delete(
    run_ids: Optional[list[str]] = typer.Argument(None, help="Run ids to delete."),
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-P",
        help="Delete a whole project (with --status: only its matching runs).",
    ),
    status: Optional[str] = typer.Option(
        None, "--status", "-s", help="Delete runs with this status, e.g. crashed."
    ),
    directory: Optional[Path] = DirOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    local_only: bool = typer.Option(
        False, "--local-only", help="Keep the cloud copy (if signed in)."
    ),
) -> None:
    """Delete runs and their media — by id, --project or --status.

    Deletes locally and, when signed in, on the cloud server too
    (--local-only keeps the cloud copy)."""
    from . import credentials

    if run_ids and (project or status):
        console.print("[red]give run ids or --project/--status filters, not both[/red]")
        raise typer.Exit(2)
    if not run_ids and not project and not status:
        console.print(
            "[red]nothing selected — give run ids, --project or --status[/red]"
        )
        raise typer.Exit(2)

    store = LocalStore(resolve_dir(directory))
    creds = None if local_only else credentials.load()

    if project and not status:  # whole project: one local + one cloud call
        count = len(store.list_runs(project))
        if count == 0 and creds is None:
            console.print(f"[red]no runs in project {project}[/red]")
            raise typer.Exit(1)
        if not yes and not typer.confirm(
            f"delete project {project} ({count} local runs)?"
        ):
            raise typer.Exit(0)
        store.delete_project(project)
        console.print(f"[dim]deleted {count} runs locally[/dim]")
        if creds and _cloud_delete(creds, f"/api/projects/{project}"):
            console.print(f"[dim]deleted project {project} on {creds['server']}[/dim]")
        return

    if run_ids:
        targets = list(run_ids)
    else:
        targets = [
            r["id"]
            for r in store.list_runs(project)
            if status is None or r["status"] == status
        ]
        if not targets:
            console.print("[dim]no matching runs[/dim]")
            return
    if len(targets) == 1:
        run = store.get_run(targets[0])
        label = f"run {run['name'] if run else targets[0]} ({targets[0]})"
    else:
        label = f"{len(targets)} runs"
    if not yes and not typer.confirm(f"delete {label}?"):
        raise typer.Exit(0)

    missed = []
    for rid in targets:
        deleted = False
        if store.run_exists(rid):
            store.delete_run(rid)
            deleted = True
            console.print(f"[dim]deleted {rid} locally[/dim]")
        if creds and _cloud_delete(creds, f"/api/runs/{rid}"):
            deleted = True
            console.print(f"[dim]deleted {rid} on {creds['server']}[/dim]")
        if not deleted:
            missed.append(rid)
    if missed:
        console.print(f"[red]not found: {', '.join(missed)}[/red]")
        raise typer.Exit(1)


@app.command()
def edit(
    run_id: str,
    name: Optional[str] = typer.Option(None, "--name", help="Rename the run."),
    project: Optional[str] = typer.Option(
        None, "--project", "-P", help="Move the run to another project."
    ),
    description: Optional[str] = typer.Option(
        None, "--description", help="Replace the description."
    ),
    group: Optional[str] = typer.Option(
        None, "--group", help="Set the group ('' clears it)."
    ),
    directory: Optional[Path] = DirOption,
) -> None:
    """Rename a run, move it to another project, or reword its description/group.

    Edits local metadata only; a synced cloud copy keeps its original values."""
    if name is None and project is None and description is None and group is None:
        console.print(
            "[red]nothing to change — pass --name/--project/--description/--group[/red]"
        )
        raise typer.Exit(2)
    store = LocalStore(resolve_dir(directory))
    if not store.update_run_meta(
        run_id, name=name, project=project, description=description, group=group
    ):
        console.print(f"[red]run {run_id} not found[/red]")
        raise typer.Exit(1)
    run = store.get_run(run_id)
    if run:
        console.print(
            f"{run['name']} [dim]({run_id})[/dim] · project {run['project']}"
            + (f" · group {run['group']}" if run["group"] else "")
        )


@app.command()
def login(
    server: str = typer.Argument(
        DEFAULT_SERVER, help="pandm server URL (default: the hosted pandm cloud)."
    ),
    key: Optional[str] = typer.Option(
        None, "--key", help="Paste an API key directly (skips the browser)."
    ),
) -> None:
    """Sign in to a pandm server (browser approval, like `gh auth login`)."""
    from . import credentials

    saved = credentials.device_login(
        server, key=key, echo=lambda m: console.print(m, highlight=False)
    )
    if saved is None:
        raise typer.Exit(1)
    credentials.set_opted_out()  # signed in -> stop offering the login prompt in pandm.init()
    console.print(
        f"[green]logged in as [bold]{saved['login']}[/bold][/green] [dim]({credentials.cred_path()})[/dim]"
    )
    console.print(
        "[dim]pandm.init() now syncs runs to this server; PANDM_NO_SYNC=1 opts out per-run[/dim]"
    )


@app.command()
def logout() -> None:
    """Forget saved credentials (runs stay local-first)."""
    from . import credentials

    console.print(
        "[dim]logged out[/dim]" if credentials.clear() else "[dim]not logged in[/dim]"
    )


@app.command()
def sync(
    run_ids: Optional[list[str]] = typer.Argument(
        None,
        help="Specific runs to sync (default: all cloud-tracked runs with pending data).",
    ),
    directory: Optional[Path] = DirOption,
    all_runs: bool = typer.Option(
        False, "--all", help="Also sync local-only runs that were never cloud-tracked."
    ),
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
        console.print(
            f"[green]{synced}/{len(report)} runs synced[/green] -> {creds['server']}"
        )


@app.command()
def pull(
    run_ids: Optional[list[str]] = typer.Argument(
        None, help="Runs to pull (default: every cloud run missing locally)."
    ),
    project: Optional[str] = typer.Option(
        None, "--project", "-P", help="Only pull runs from this project."
    ),
    directory: Optional[Path] = DirOption,
) -> None:
    """Download runs from the signed-in server (the reverse of `pandm sync`).

    For analyzing on a different machine: metrics, histograms, media, config
    and summary all land in the local store. Runs that already exist locally
    are skipped, and pulled runs are marked synced so `pandm sync` won't push
    them back."""
    from pathlib import PurePosixPath

    import httpx

    from . import credentials

    creds = credentials.load()
    if creds is None:
        console.print("[red]not logged in — run pandm login first[/red]")
        raise typer.Exit(1)
    store = LocalStore(resolve_dir(directory))
    client = httpx.Client(
        base_url=creds["server"],
        headers={"x-api-key": creds["api_key"]},
        timeout=30,
    )

    def fetch(path: str, **params) -> httpx.Response:
        resp = client.get(path, params=params or None)
        resp.raise_for_status()
        return resp

    try:
        if run_ids:
            remote_runs = []
            for rid in run_ids:
                resp = client.get(f"/api/runs/{rid}")
                if resp.status_code == 404:
                    console.print(
                        f"[red]run {rid} not found on {creds['server']}[/red]"
                    )
                    raise typer.Exit(1)
                resp.raise_for_status()
                remote_runs.append(resp.json())
        else:
            remote_runs = fetch(
                "/api/runs", **({"project": project} if project else {})
            ).json()
            if project:
                remote_runs = [r for r in remote_runs if r["project"] == project]

        pulled = 0
        for run in remote_runs:
            rid = run["id"]
            if store.run_exists(rid):
                console.print(f"  [dim]{rid} exists locally — skipped[/dim]")
                continue
            store.create_run(
                rid,
                run["project"],
                run["name"],
                run["config"],
                created_at=run["created_at"],
                description=run["description"],
                tags=run["tags"],
                group=run["group"],
            )
            for meta in fetch(f"/api/runs/{rid}/metrics").json():
                key = meta["key"]
                s = fetch(f"/api/runs/{rid}/metrics/{key}", max_points=2**31 - 1).json()
                store.log_metrics(
                    rid,
                    list(
                        zip([key] * len(s["steps"]), s["steps"], s["values"], s["ts"])
                    ),
                )
            for meta in fetch(f"/api/runs/{rid}/histograms").json():
                key = meta["key"]
                s = fetch(
                    f"/api/runs/{rid}/histograms/{key}", max_steps=2**31 - 1
                ).json()
                for step, bins, counts, ts in zip(
                    s["steps"], s["bins"], s["counts"], s["ts"]
                ):
                    store.log_histogram(rid, key, step, bins, counts, ts=ts)
            for item in fetch(f"/api/runs/{rid}/media").json():
                data = fetch(item["url"]).content
                store.log_media(
                    rid,
                    item["key"],
                    item["step"],
                    data,
                    ext=PurePosixPath(item["filename"]).suffix or ".png",
                    caption=item["caption"],
                    ts=item["ts"],
                )
            store.set_summary(rid, run["summary"])
            store.set_metric_meta(rid, run["metric_meta"])
            if run["status"] == "running" and run["progress"] is not None:
                store.update_progress(
                    rid, run["progress"], run["progress_total"], ts=run["progress_ts"]
                )
            if run["status"] != "running":
                store.finish_run(
                    rid, run["status"], run["finished_at"] or run["updated_at"]
                )
            store.mark_fully_synced(rid)  # the server already has all of this
            pulled += 1
            console.print(f"  [dim]{rid}[/dim] pulled ({run['name']})")
        console.print(
            f"[green]{pulled}/{len(remote_runs)} runs pulled[/green] <- {creds['server']}"
        )
    except httpx.HTTPError as exc:
        console.print(f"[red]pull failed: {exc}[/red]")
        raise typer.Exit(1)
    finally:
        client.close()


@app.command()
def whoami(
    as_json: bool = JsonOption,
    directory: Optional[Path] = DirOption,
) -> None:
    """Show the signed-in server/account and how many runs still have data to push."""
    from . import credentials

    creds = credentials.load()
    if creds is None:
        if as_json:
            _emit_json({"logged_in": False})
        else:
            console.print(
                "[dim]not logged in — runs stay local; pandm login to sync[/dim]"
            )
        raise typer.Exit(1)
    pending = LocalStore(resolve_dir(directory)).runs_needing_sync()
    if as_json:
        _emit_json(
            {
                "logged_in": True,
                "server": creds["server"],
                "login": creds.get("login"),
                "pending_runs": pending,
            }
        )
        return
    console.print(
        f"[green]{creds.get('login') or '(api key)'}[/green] @ {creds['server']}"
        f" [dim]({credentials.cred_path()})[/dim]"
    )
    if pending:
        console.print(
            f"[yellow]{len(pending)} runs with unpushed data[/yellow] — pandm sync"
        )
    else:
        console.print("[dim]everything synced[/dim]")


@app.command()
def finish(
    run_ids: Optional[list[str]] = typer.Argument(
        None, help="Runs to finalize (or use --stale)."
    ),
    status: str = typer.Option(
        "finished", "--status", "-s", help="Final status: finished or crashed."
    ),
    stale: bool = typer.Option(
        False,
        "--stale",
        help="Persist 'crashed' for every run whose process died without finish().",
    ),
    directory: Optional[Path] = DirOption,
) -> None:
    """Persist a final status for runs that never called finish().

    A killed training process leaves its run 'running' in the database; the
    dashboard and ls already display it as crashed once the heartbeat expires,
    but --stale (or an explicit run id) writes that verdict down for good."""
    if status not in ("finished", "crashed"):
        console.print(f"[red]bad --status {status!r} — use finished or crashed[/red]")
        raise typer.Exit(2)
    store = LocalStore(resolve_dir(directory))
    if stale:
        # displayed-crashed without a finished_at == heartbeat expired, never finalized
        targets = [
            r
            for r in store.list_runs()
            if r["status"] == "crashed" and r["finished_at"] is None
        ]
        for r in targets:
            store.finish_run(r["id"], "crashed", finished_at=r["updated_at"])
            console.print(f"  [dim]{r['id']}[/dim] marked crashed ({r['name']})")
        console.print(
            f"[green]{len(targets)} stale runs finalized[/green]"
            if targets
            else "[dim]no stale runs[/dim]"
        )
        return
    if not run_ids:
        console.print("[red]give run ids or --stale[/red]")
        raise typer.Exit(2)
    for rid in run_ids:
        run = store.get_run(rid)
        if run is None:
            console.print(f"[red]run {rid} not found[/red]")
            raise typer.Exit(1)
        store.finish_run(rid, status)
        console.print(f"  [dim]{rid}[/dim] -> {status}")


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"pandm v{__version__}")
