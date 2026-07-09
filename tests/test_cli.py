"""CLI query surface: ls/show/compare/export with --json, filtering and metric sort."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import pandm
from pandm.cli import app

runner = CliRunner()


@pytest.fixture
def store_dir(tmp_path):
    """Three finished runs with a clear val/acc ranking c > b > a."""
    data_dir = tmp_path / ".pandm"
    for i, (lr, name) in enumerate([(0.01, "a"), (0.1, "b"), (0.001, "c")]):
        run = pandm.init(
            project="mnist", name=name, config={"lr": lr}, directory=data_dir
        )
        for step in range(5):
            run.log(
                {"val/acc": 0.5 + 0.1 * i + 0.01 * step, "loss": 1.0 - 0.1 * i},
                step=step,
            )
        run.summary({"best_acc": round(0.5 + 0.1 * i + 0.04, 3)})
        run.finish()
    return data_dir


def _json(args: list[str]):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def test_ls_json_carries_config_and_stats(store_dir):
    runs = _json(["ls", "-d", str(store_dir), "--json"])
    assert {r["name"] for r in runs} == {"a", "b", "c"}
    c = next(r for r in runs if r["name"] == "c")
    assert c["config"]["lr"] == 0.001
    assert c["summary"]["best_acc"] == 0.74
    assert set(c["stats"]["val/acc"]) >= {"min", "max", "last", "count"}


def test_ls_status_filter(store_dir):
    assert (
        len(_json(["ls", "-d", str(store_dir), "--status", "finished", "--json"])) == 3
    )
    assert _json(["ls", "-d", str(store_dir), "--status", "running", "--json"]) == []


def test_ls_sort_by_metric_best_first(store_dir):
    runs = _json(["ls", "-d", str(store_dir), "--sort-by", "val/acc", "--json"])
    assert [r["name"] for r in runs] == ["c", "b", "a"]


def test_ls_sort_ascending_and_limit(store_dir):
    runs = _json(
        [
            "ls",
            "-d",
            str(store_dir),
            "--sort-by",
            "val/acc",
            "--asc",
            "--limit",
            "1",
            "--json",
        ]
    )
    assert [r["name"] for r in runs] == ["a"]


def test_ls_sort_bad_aggregate_errors(store_dir):
    result = runner.invoke(
        app, ["ls", "-d", str(store_dir), "--sort-by", "val/acc:avg", "--json"]
    )
    assert result.exit_code == 2


def test_show_json_has_metric_keys_and_media(store_dir):
    rid = _json(["ls", "-d", str(store_dir), "--json"])[0]["id"]
    run = _json(["show", "-d", str(store_dir), rid, "--json"])
    assert {k["key"] for k in run["metric_keys"]} == {"val/acc", "loss"}
    assert "media" in run  # empty here, but the key is always present for tooling


def test_compare_json_aligns_values_to_runs(store_dir):
    ids = [
        r["id"]
        for r in _json(["ls", "-d", str(store_dir), "--json"])
        if r["name"] in ("a", "c")
    ]
    cmp = _json(["compare", "-d", str(store_dir), *ids, "--json"])
    order = [r["name"] for r in cmp["runs"]]
    lrs = dict(zip(order, cmp["config"]["lr"]))
    assert lrs == {"a": 0.01, "c": 0.001}


def test_compare_missing_run_exits_nonzero(store_dir):
    result = runner.invoke(
        app, ["compare", "-d", str(store_dir), "nope1", "nope2", "--json"]
    )
    assert result.exit_code == 1


def test_export_json_returns_series(store_dir):
    rid = _json(["ls", "-d", str(store_dir), "--json"])[0]["id"]
    series = _json(["export", "-d", str(store_dir), rid, "-k", "loss", "--json"])
    assert series["loss"]["steps"] == [0, 1, 2, 3, 4]


# ------------------------------------------------- projects / tag / edit / finish


def test_projects_lists_counts(store_dir):
    rows = _json(["projects", "-d", str(store_dir), "--json"])
    assert rows == [
        {"project": "mnist", "runs": 3, "last_active": rows[0]["last_active"]}
    ]


def test_tag_and_ls_tag_filter(store_dir):
    rid = _json(["ls", "-d", str(store_dir), "--json"])[0]["id"]
    result = runner.invoke(app, ["tag", rid, "best", "wip", "-d", str(store_dir)])
    assert result.exit_code == 0, result.stdout
    assert [
        r["id"] for r in _json(["ls", "-d", str(store_dir), "-t", "best", "--json"])
    ] == [rid]

    result = runner.invoke(app, ["tag", rid, "--rm", "wip", "-d", str(store_dir)])
    assert result.exit_code == 0, result.stdout
    assert _json(["ls", "-d", str(store_dir), "-t", "wip", "--json"]) == []
    assert _json(["ls", "-d", str(store_dir), "-t", "best", "--json"])[0]["tags"] == [
        "best"
    ]


def test_export_histograms_json_and_csv(store_dir):
    from pandm.storage import LocalStore

    store = LocalStore(store_dir)
    rid = store.list_runs()[0]["id"]
    store.log_histogram(rid, "w", 0, [0.0, 0.5, 1.0], [3, 7], ts=1.0)

    data = _json(["export", rid, "-d", str(store_dir), "--histograms", "--json"])
    assert data["w"]["steps"] == [0] and data["w"]["counts"] == [[3, 7]]

    result = runner.invoke(app, ["export", rid, "-d", str(store_dir), "--histograms"])
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "key,step,bin_lo,bin_hi,count,ts"
    assert len(lines) == 3  # one row per bin
    assert lines[1].split(",") == ["w", "0", "0.0", "0.5", "3", "1.0"]


def test_edit_renames_and_moves_project(store_dir):
    rid = _json(["ls", "-d", str(store_dir), "--json"])[0]["id"]
    result = runner.invoke(
        app, ["edit", rid, "--name", "gold", "-P", "prod", "-d", str(store_dir)]
    )
    assert result.exit_code == 0, result.stdout
    run = _json(["show", rid, "-d", str(store_dir), "--json"])
    assert (run["name"], run["project"]) == ("gold", "prod")


def test_edit_without_changes_exits_2(store_dir):
    rid = _json(["ls", "-d", str(store_dir), "--json"])[0]["id"]
    assert runner.invoke(app, ["edit", rid, "-d", str(store_dir)]).exit_code == 2


def test_finish_stale_persists_crash(store_dir):
    import time

    from pandm.storage import LocalStore

    store = LocalStore(store_dir)
    store.create_run("stale0000001", "mnist", "zombie", {})
    with store._lock:  # age the heartbeat past STALE_AFTER
        store._db.execute(
            "UPDATE runs SET updated_at = ? WHERE id = 'stale0000001'",
            (time.time() - 300,),
        )
        store._db.commit()

    result = runner.invoke(app, ["finish", "--stale", "-d", str(store_dir)])
    assert result.exit_code == 0, result.stdout
    run = store.get_run("stale0000001")
    assert run is not None
    assert run["status"] == "crashed" and run["finished_at"] is not None
    # healthy finished runs were left alone
    assert len(_json(["ls", "-d", str(store_dir), "-s", "finished", "--json"])) == 3


def test_finish_explicit_id_sets_status(store_dir):
    from pandm.storage import LocalStore

    store = LocalStore(store_dir)
    store.create_run("live00000001", "mnist", "live", {})
    result = runner.invoke(
        app, ["finish", "live00000001", "-s", "crashed", "-d", str(store_dir)]
    )
    assert result.exit_code == 0, result.stdout
    run = store.get_run("live00000001")
    assert run is not None and run["status"] == "crashed"


def test_finish_needs_ids_or_stale(store_dir):
    assert runner.invoke(app, ["finish", "-d", str(store_dir)]).exit_code == 2


# ------------------------------------------------------------ bulk delete


def test_delete_by_status_batch(store_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # logged out
    result = runner.invoke(
        app, ["delete", "-s", "finished", "-P", "mnist", "--yes", "-d", str(store_dir)]
    )
    assert result.exit_code == 0, result.stdout
    assert _json(["ls", "-d", str(store_dir), "--json"]) == []


def test_delete_whole_project(store_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = runner.invoke(
        app, ["delete", "-P", "mnist", "--yes", "-d", str(store_dir)]
    )
    assert result.exit_code == 0, result.stdout
    assert _json(["projects", "-d", str(store_dir), "--json"]) == []


def test_delete_rejects_ids_mixed_with_filters(store_dir):
    result = runner.invoke(
        app, ["delete", "someid", "-P", "mnist", "--yes", "-d", str(store_dir)]
    )
    assert result.exit_code == 2


def test_delete_nothing_selected_exits_2(store_dir):
    assert runner.invoke(app, ["delete", "--yes", "-d", str(store_dir)]).exit_code == 2


# ------------------------------------------------------------------ ingest


def test_ingest_csv_creates_finished_run(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("PANDM_NO_SYNC", "1")
    csv_file = tmp_path / "metrics.csv"
    csv_file.write_text("epoch,acc,note\n0,0.5,x\n1,0.6,y\n")
    data_dir = tmp_path / ".pandm"

    result = runner.invoke(
        app,
        [
            "ingest",
            str(csv_file),
            "-P",
            "csvproj",
            "--step-column",
            "epoch",
            "-t",
            "imported",
            "-d",
            str(data_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "2 rows ingested" in result.stdout

    from pandm.storage import LocalStore

    runs = LocalStore(data_dir).list_runs("csvproj")
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "finished" and run["tags"] == ["imported"]
    series = _json(["export", run["id"], "-d", str(data_dir), "-k", "acc", "--json"])
    assert series["acc"]["steps"] == [0, 1] and series["acc"]["values"] == [0.5, 0.6]


def test_ingest_missing_file_exits_1(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("PANDM_NO_SYNC", "1")
    result = runner.invoke(
        app, ["ingest", str(tmp_path / "nope.csv"), "-d", str(tmp_path / ".pandm")]
    )
    assert result.exit_code == 1


# ------------------------------------------------------------------ whoami


def test_whoami_logged_out(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = runner.invoke(app, ["whoami", "-d", str(tmp_path / ".pandm")])
    assert result.exit_code == 1
    result = runner.invoke(app, ["whoami", "--json", "-d", str(tmp_path / ".pandm")])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["logged_in"] is False
