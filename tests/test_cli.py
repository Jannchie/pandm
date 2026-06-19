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
        run = pandm.init(project="mnist", name=name, config={"lr": lr}, directory=data_dir)
        for step in range(5):
            run.log({"val/acc": 0.5 + 0.1 * i + 0.01 * step, "loss": 1.0 - 0.1 * i}, step=step)
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
    assert len(_json(["ls", "-d", str(store_dir), "--status", "finished", "--json"])) == 3
    assert _json(["ls", "-d", str(store_dir), "--status", "running", "--json"]) == []


def test_ls_sort_by_metric_best_first(store_dir):
    runs = _json(["ls", "-d", str(store_dir), "--sort-by", "val/acc", "--json"])
    assert [r["name"] for r in runs] == ["c", "b", "a"]


def test_ls_sort_ascending_and_limit(store_dir):
    runs = _json(["ls", "-d", str(store_dir), "--sort-by", "val/acc", "--asc", "--limit", "1", "--json"])
    assert [r["name"] for r in runs] == ["a"]


def test_ls_sort_bad_aggregate_errors(store_dir):
    result = runner.invoke(app, ["ls", "-d", str(store_dir), "--sort-by", "val/acc:avg", "--json"])
    assert result.exit_code == 2


def test_show_json_has_metric_keys_and_media(store_dir):
    rid = _json(["ls", "-d", str(store_dir), "--json"])[0]["id"]
    run = _json(["show", "-d", str(store_dir), rid, "--json"])
    assert {k["key"] for k in run["metric_keys"]} == {"val/acc", "loss"}
    assert "media" in run  # empty here, but the key is always present for tooling


def test_compare_json_aligns_values_to_runs(store_dir):
    ids = [r["id"] for r in _json(["ls", "-d", str(store_dir), "--json"]) if r["name"] in ("a", "c")]
    cmp = _json(["compare", "-d", str(store_dir), *ids, "--json"])
    order = [r["name"] for r in cmp["runs"]]
    lrs = dict(zip(order, cmp["config"]["lr"]))
    assert lrs == {"a": 0.01, "c": 0.001}


def test_compare_missing_run_exits_nonzero(store_dir):
    result = runner.invoke(app, ["compare", "-d", str(store_dir), "nope1", "nope2", "--json"])
    assert result.exit_code == 1


def test_export_json_returns_series(store_dir):
    rid = _json(["ls", "-d", str(store_dir), "--json"])[0]["id"]
    series = _json(["export", "-d", str(store_dir), rid, "-k", "loss", "--json"])
    assert series["loss"]["steps"] == [0, 1, 2, 3, 4]
