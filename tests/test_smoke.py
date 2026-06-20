"""End-to-end smoke tests: SDK local writes, server read API, cloud ingest API."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import pandm
from pandm.server import create_app
from pandm.storage import LocalStore


def _png_bytes(color=(80, 120, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


def _get_run(store: LocalStore, run_id: str) -> dict:
    row = store.get_run(run_id)
    assert row is not None
    return row


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / ".pandm"


def test_sdk_local_roundtrip(data_dir):
    run = pandm.init(project="proj", name="test-run", config={"lr": 0.1}, directory=data_dir)
    for step in range(20):
        run.log({"loss": 1.0 / (step + 1), "acc": step / 20}, step=step)
    run.log_image("samples", Image.new("RGB", (16, 16), (255, 0, 0)), step=19, caption="hi")
    run.finish()

    store = LocalStore(data_dir)
    runs = store.list_runs("proj")
    assert len(runs) == 1
    assert runs[0]["name"] == "test-run"
    assert runs[0]["status"] == "finished"
    assert runs[0]["config"] == {"lr": 0.1}
    assert runs[0]["stats"]["loss"]["last"] == pytest.approx(1.0 / 20)

    keys = {k["key"] for k in store.metric_keys(run.id)}
    assert keys == {"loss", "acc"}
    series = store.metric_series(run.id, "loss")
    assert len(series["steps"]) == 20
    assert series["steps"][-1] == 19

    media = store.list_media(run.id)
    assert len(media) == 1
    assert media[0]["caption"] == "hi"
    assert store.media_path(run.id, media[0]["filename"]) is not None


def test_sdk_tags_and_group_roundtrip(data_dir):
    run = pandm.init(
        project="proj", name="tagged", directory=data_dir,
        tags=["baseline", "lr-sweep", "baseline", " trim ", ""], group="exp-42",
    )
    run.finish()

    store = LocalStore(data_dir)
    got = _get_run(store, run.id)
    assert got["group"] == "exp-42"
    assert got["tags"] == ["baseline", "lr-sweep", "trim"]  # de-duped, trimmed, empties dropped

    # a run with neither stays empty, not null
    plain = pandm.init(project="proj", name="plain", directory=data_dir)
    plain.finish()
    assert _get_run(store, plain.id)["tags"] == []
    assert _get_run(store, plain.id)["group"] is None


def test_sdk_auto_step_and_context_manager(data_dir):
    with pandm.init(project="proj", directory=data_dir) as run:
        run.log({"loss": 3.0})
        run.log({"loss": 2.0})
        run.log({"loss": 1.0})
    store = LocalStore(data_dir)
    series = store.metric_series(run.id, "loss")
    assert series["steps"] == [0, 1, 2]
    assert _get_run(store, run.id)["status"] == "finished"


def test_sdk_crash_marks_status(data_dir):
    run = pandm.init(project="proj", directory=data_dir)
    with pytest.raises(ValueError), run:
        run.log({"loss": 1.0})
        raise ValueError("boom")
    assert _get_run(LocalStore(data_dir), run.id)["status"] == "crashed"


def test_server_read_api(data_dir):
    run = pandm.init(project="proj", name="api-run", config={"bs": 32}, directory=data_dir)
    for step in range(10):
        run.log({"loss": float(10 - step)}, step=step)
    run.log_image("img", Image.new("RGB", (8, 8)), step=9)
    run.finish()

    client = TestClient(create_app(data_dir))
    assert client.get("/api/projects").json()[0]["project"] == "proj"

    runs = client.get("/api/runs", params={"project": "proj"}).json()
    assert runs[0]["id"] == run.id
    assert runs[0]["stats"]["loss"]["last"] == 1.0

    series = client.get(f"/api/runs/{run.id}/metrics/loss").json()
    assert series["values"][0] == 10.0

    media = client.get(f"/api/runs/{run.id}/media").json()
    assert len(media) == 1
    img = client.get(media[0]["url"])
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"


def test_ingest_api_with_key(data_dir):
    client = TestClient(create_app(data_dir, api_key="sekrit"))

    # without key -> 401
    assert client.post("/api/runs", json={"project": "p", "name": "n"}).status_code == 401

    headers = {"x-api-key": "sekrit"}
    run_id = client.post(
        "/api/runs", json={"id": "abc12345", "project": "p", "name": "n", "config": {"a": 1}}, headers=headers
    ).json()["id"]
    assert run_id == "abc12345"

    rows = {"rows": [{"key": "loss", "step": i, "value": float(i), "ts": 1.0 + i} for i in range(5)]}
    assert client.post(f"/api/runs/{run_id}/metrics", json=rows, headers=headers).json()["inserted"] == 5

    resp = client.post(
        f"/api/runs/{run_id}/media",
        files={"file": ("x.png", _png_bytes())},
        data={"key": "samples", "step": "4", "caption": "c"},
        headers=headers,
    )
    assert resp.status_code == 200

    assert client.post(f"/api/runs/{run_id}/finish", json={"status": "finished"}, headers=headers).status_code == 200

    # reads stay open without a key
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "finished"
    assert run["stats"]["loss"]["last"] == 4.0

    assert client.delete(f"/api/runs/{run_id}", headers=headers).json()["deleted"] is True
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_delete_project_removes_all_runs(data_dir):
    client = TestClient(create_app(data_dir, api_key="sekrit"))
    headers = {"x-api-key": "sekrit"}
    for i in range(3):
        client.post("/api/runs", json={"id": f"r{i}", "project": "doomed", "name": f"n{i}"}, headers=headers)
    client.post("/api/runs", json={"id": "keep", "project": "other", "name": "n"}, headers=headers)

    # delete requires a key
    assert client.delete("/api/projects/doomed").status_code == 401
    assert client.delete("/api/projects/doomed", headers=headers).json()["deleted"] is True

    projects = {p["project"] for p in client.get("/api/projects").json()}
    assert projects == {"other"}
    assert client.get("/api/runs/r0").status_code == 404
    assert client.get("/api/runs/keep").status_code == 200


def test_uncaught_exception_marks_crashed(data_dir, tmp_path):
    script = tmp_path / "boom.py"
    script.write_text(
        "import pandm\n"
        "run = pandm.init(project='p', name='boom')\n"
        "run.log({'loss': 1.0})\n"
        "raise RuntimeError('boom')\n"
    )
    env = {**os.environ, "PANDM_DIR": str(data_dir)}
    proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True)
    assert proc.returncode != 0
    runs = LocalStore(data_dir).list_runs("p")
    assert len(runs) == 1
    assert runs[0]["status"] == "crashed"


def test_stale_running_run_reported_crashed(data_dir):
    store = LocalStore(data_dir)
    store.create_run("r1", "p", "n", {})
    assert _get_run(store, "r1")["status"] == "running"
    # simulate a hard-killed process: heartbeat stopped beyond the stale threshold
    with store._lock:
        store._db.execute("UPDATE runs SET updated_at = ? WHERE id = 'r1'", (time.time() - 120,))
        store._db.commit()
    assert _get_run(store, "r1")["status"] == "crashed"
    assert store.list_runs()[0]["status"] == "crashed"
    # if the process comes back (e.g. it was just suspended), the status self-heals
    store.heartbeat("r1")
    assert _get_run(store, "r1")["status"] == "running"


def test_downsampling(data_dir):
    store = LocalStore(data_dir)
    store.create_run("r1", "p", "n", {})
    store.log_metrics("r1", [("loss", i, float(i), float(i)) for i in range(10_000)])
    series = store.metric_series("r1", "loss", max_points=500)
    assert len(series["steps"]) <= 502
    assert series["steps"][0] == 0
    assert series["steps"][-1] == 9999  # last point always kept


def test_metric_series_incremental(data_dir):
    store = LocalStore(data_dir)
    store.create_run("r1", "p", "n", {})
    store.log_metrics("r1", [("loss", i, float(i), float(i)) for i in range(20)])

    tail = store.metric_series("r1", "loss", after_step=16)
    assert tail["steps"] == [17, 18, 19]
    assert tail["values"] == [17.0, 18.0, 19.0]
    assert store.metric_series("r1", "loss", after_step=19)["steps"] == []

    client = TestClient(create_app(data_dir))
    resp = client.get("/api/runs/r1/metrics/loss", params={"after_step": 16}).json()
    assert resp["steps"] == [17, 18, 19]


def test_cli_show_and_export(data_dir):
    from typer.testing import CliRunner

    from pandm.cli import app as cli_app

    run = pandm.init(project="p", name="cli-run", config={"lr": 0.1}, directory=data_dir)
    for step in range(5):
        run.log({"loss": float(5 - step), "acc": step / 5}, step=step)
    run.finish()

    runner = CliRunner()
    shown = runner.invoke(cli_app, ["show", run.id, "--dir", str(data_dir)])
    assert shown.exit_code == 0
    assert "cli-run" in shown.output
    assert "loss" in shown.output and "acc" in shown.output
    assert "lr" in shown.output

    csv_out = runner.invoke(cli_app, ["export", run.id, "--dir", str(data_dir)])
    assert csv_out.exit_code == 0
    lines = csv_out.output.strip().splitlines()
    assert lines[0] == "key,step,value,ts"
    assert len(lines) == 1 + 10  # 2 keys x 5 points
    assert lines[1].startswith("acc,0,")

    one_key = runner.invoke(cli_app, ["export", run.id, "-k", "loss", "--json", "--dir", str(data_dir)])
    assert one_key.exit_code == 0
    data = __import__("json").loads(one_key.output)
    assert list(data) == ["loss"]
    assert data["loss"]["steps"] == [0, 1, 2, 3, 4]

    missing = runner.invoke(cli_app, ["export", "nope", "--dir", str(data_dir)])
    assert missing.exit_code == 1


def test_progress_tracking_local(data_dir):
    run = pandm.init(project="p", name="prog", total_steps=100, directory=data_dir)
    for step in range(40):
        run.log({"loss": 1.0}, step=step)
    run.set_progress(50, total=200)  # explicit call overrides the auto unit + total
    run.finish()

    store = LocalStore(data_dir)
    r = _get_run(store, run.id)
    assert r["progress"] == 50
    assert r["progress_total"] == 200
    assert r["progress_ts"] is not None

    api_run = TestClient(create_app(data_dir)).get(f"/api/runs/{run.id}").json()
    assert api_run["progress"] == 50
    assert api_run["progress_total"] == 200


def test_progress_ingest_api(data_dir):
    client = TestClient(create_app(data_dir, api_key="k"))
    headers = {"x-api-key": "k"}
    client.post("/api/runs", json={"id": "p1", "project": "p", "name": "n"}, headers=headers)

    assert client.post("/api/runs/p1/progress", json={"current": 30, "total": 100}, headers=headers).json()["ok"]
    run = client.get("/api/runs/p1").json()
    assert run["progress"] == 30 and run["progress_total"] == 100

    # total omitted -> keeps the previously set total
    client.post("/api/runs/p1/progress", json={"current": 60}, headers=headers)
    run = client.get("/api/runs/p1").json()
    assert run["progress"] == 60 and run["progress_total"] == 100

    # writes still need the key
    assert client.post("/api/runs/p1/progress", json={"current": 1}).status_code == 401


# ---------------------------------------------------------------- resume + stats
# remote=False forces local mode regardless of the dev's saved credentials.


def test_resume_continues_run(data_dir):
    r1 = pandm.init(project="p", id="run-aaaa", config={"lr": 0.1}, directory=data_dir, remote=False)
    for s in range(3):
        r1.log({"loss": float(3 - s)}, step=s)
    r1.finish("crashed")

    store = LocalStore(data_dir)
    assert _get_run(store, "run-aaaa")["status"] == "crashed"

    r2 = pandm.init(project="p", id="run-aaaa", resume=True, directory=data_dir, remote=False)
    assert r2.id == "run-aaaa"
    r2.log({"loss": 0.5})   # auto step continues past the last logged step (3)
    r2.log({"loss": 0.25})  # 4
    r2.finish()

    run = _get_run(store, "run-aaaa")
    assert run["status"] == "finished"
    assert store.metric_series("run-aaaa", "loss")["steps"] == [0, 1, 2, 3, 4]
    assert run["config"] == {"lr": 0.1}  # resume keeps the original config


def test_resume_guards(data_dir):
    pandm.init(project="p", id="dup-1", directory=data_dir, remote=False).finish()
    # reusing an id without resume is refused (would silently append otherwise)
    with pytest.raises(ValueError):
        pandm.init(project="p", id="dup-1", directory=data_dir, remote=False)
    # resume="must" on a missing run is refused
    with pytest.raises(ValueError):
        pandm.init(project="p", id="ghost", resume="must", directory=data_dir, remote=False)
    # resume=True on a missing id just starts fresh
    r = pandm.init(project="p", id="brand-new", resume=True, directory=data_dir, remote=False)
    assert r.id == "brand-new"
    r.finish()


def test_stats_aggregates(data_dir):
    run = pandm.init(project="p", directory=data_dir, remote=False)
    for v in (3.0, 1.0, 2.0, 5.0):
        run.log({"loss": v})
    run.finish()

    stats = _get_run(LocalStore(data_dir), run.id)["stats"]["loss"]
    assert stats["min"] == 1.0 and stats["max"] == 5.0
    assert stats["last"] == 5.0 and stats["count"] == 4


def test_summary_author_scalars(data_dir):
    run = pandm.init(project="p", name="sum", directory=data_dir, remote=False)
    run.log({"loss": 1.0})
    run.summary({"best/spearman": 0.4146, "best/mae": 1.2269, "best/epoch": 7})
    run.summary({"best/spearman": 0.78})  # merges: overwrites one key, keeps the rest
    run.finish()

    r = _get_run(LocalStore(data_dir), run.id)
    assert r["summary"] == {"best/spearman": 0.78, "best/mae": 1.2269, "best/epoch": 7}
    assert r["stats"]["loss"]["last"] == 1.0  # latest metric lives in stats, not summary

    from typer.testing import CliRunner

    from pandm.cli import app as cli_app

    shown = CliRunner().invoke(cli_app, ["show", run.id, "--dir", str(data_dir)])
    assert "SUMMARY" in shown.output and "best/mae" in shown.output


def test_finish_ingests_summary(data_dir):
    client = TestClient(create_app(data_dir, api_key="k"))
    headers = {"x-api-key": "k"}
    client.post("/api/runs", json={"id": "s1", "project": "p", "name": "n"}, headers=headers)

    body = {"status": "finished", "summary": {"best/spearman": 0.773, "best/epoch": 7}}
    assert client.post("/api/runs/s1/finish", json=body, headers=headers).status_code == 200
    run = client.get("/api/runs/s1").json()
    assert run["summary"] == {"best/spearman": 0.773, "best/epoch": 7}


def test_define_metric_local(data_dir):
    run = pandm.init(
        project="p", name="dm", description="MNIST 基线扫参", directory=data_dir, remote=False
    )
    run.define_metric("eval/win_rate", unit="percent", goal="max", baseline=0.5, description="对局胜率，越高越好")
    run.define_metric("acc", min=0, max=1)  # a bounded [0,1] score
    with pytest.raises(ValueError):
        run.define_metric("bad", goal="up")  # goal must be 'max' or 'min'
    run.log({"eval/win_rate": 0.7, "acc": 0.9})
    run.finish()

    r = _get_run(LocalStore(data_dir), run.id)
    assert r["description"] == "MNIST 基线扫参"  # run subtitle, written in the user's language
    meta = r["metric_meta"]
    assert meta["eval/win_rate"] == {
        "min": 0.0,
        "max": 1.0,
        "unit": "percent",
        "goal": "max",
        "baseline": 0.5,
        "description": "对局胜率，越高越好",
    }
    assert meta["acc"] == {"min": 0.0, "max": 1.0}  # unit="percent" not set -> just the fixed range
    assert "bad" not in meta  # the rejected call never reached the store


def test_define_metric_panel_band_kind(data_dir):
    """panel / series / band / kind ride along in metric_meta untouched — the backend
    is oblivious to them, the dashboard renders from them (multi-line panels, CI bands,
    bar charts)."""
    run = pandm.init(project="p", name="rich", directory=data_dir, remote=False)
    run.define_metric("reward/total", panel="reward", series="total")
    run.define_metric("reward/shaping", panel="reward")
    run.define_metric("eval/win_rate", unit="percent", band=True)
    run.define_metric("eval/return", band={"lo": "eval/return_p05", "hi": "eval/return_p95"})
    for s in range(4):
        run.define_metric(f"final/seat{s}", panel="seat_winrate", kind="bar")
    run.finish()

    meta = _get_run(LocalStore(data_dir), run.id)["metric_meta"]
    assert meta["reward/total"] == {"panel": "reward", "series": "total"}
    assert meta["reward/shaping"] == {"panel": "reward"}
    assert meta["eval/win_rate"] == {"min": 0.0, "max": 1.0, "unit": "percent", "band": True}
    assert meta["eval/return"]["band"] == {"lo": "eval/return_p05", "hi": "eval/return_p95"}
    assert meta["final/seat0"] == {"panel": "seat_winrate", "kind": "bar"}


def test_define_metric_rejects_bad_args(data_dir):
    run = pandm.init(project="p", directory=data_dir, remote=False)
    with pytest.raises(ValueError):
        run.define_metric("x", band={"lo": "x_lo"})  # band dict needs both lo and hi
    with pytest.raises(ValueError):
        run.define_metric("x", kind="heatmap")  # kind must be line/bar/scatter
    run.define_metric("x", kind="line")  # the default kind is implicit, not stored
    run.finish()
    meta = _get_run(LocalStore(data_dir), run.id)["metric_meta"]
    assert "x" not in meta  # only rejected/no-op calls happened, nothing reached the store


def test_finish_ingests_metric_meta(data_dir):
    client = TestClient(create_app(data_dir, api_key="k"))
    headers = {"x-api-key": "k"}
    client.post("/api/runs", json={"id": "m1", "project": "p", "name": "n"}, headers=headers)

    spec = {"win_rate": {"min": 0, "max": 1, "unit": "percent", "goal": "max", "baseline": 0.5}}
    body = {"status": "finished", "metric_meta": spec}
    assert client.post("/api/runs/m1/finish", json=body, headers=headers).status_code == 200
    assert client.get("/api/runs/m1").json()["metric_meta"] == spec


def test_log_histogram_local(data_dir):
    np = pytest.importorskip("numpy")
    run = pandm.init(project="p", name="hist", directory=data_dir, remote=False)
    for step in range(3):
        run.log_histogram("dist/reward", np.random.randn(500) + step, step=step, bins=20)
    run.log_histogram("adv", ([1, 2, 3], [0.0, 1.0, 2.0, 3.0]), step=3)  # precomputed (counts, edges)
    run.log_histogram("empty", [], step=4)  # nothing finite -> silently skipped
    run.finish()

    store = LocalStore(data_dir)
    assert {k["key"] for k in store.histogram_keys(run.id)} == {"dist/reward", "adv"}
    series = store.histogram_series(run.id, "dist/reward")
    assert series["steps"] == [0, 1, 2]
    assert len(series["counts"][0]) == 20  # n bins
    assert len(series["bins"][0]) == 21  # n+1 edges
    adv = store.histogram_series(run.id, "adv")
    assert adv["counts"][0] == [1, 2, 3] and adv["bins"][0] == [0.0, 1.0, 2.0, 3.0]


def test_histogram_seq_dedup(data_dir):
    store = LocalStore(data_dir)
    store.create_run("r", "p", "n", {})
    n = store.log_histograms_seq("r", [("k", 0, [0.0, 1.0], [2], 1.0, 1), ("k", 1, [0.0, 1.0], [3], 2.0, 2)])
    assert n == 2
    # a replay (seq ≤ watermark) is dropped; only the fresh seq=3 lands
    n2 = store.log_histograms_seq("r", [("k", 1, [0.0, 1.0], [3], 2.0, 2), ("k", 2, [0.0, 1.0], [5], 3.0, 3)])
    assert n2 == 1
    assert store.histogram_series("r", "k")["steps"] == [0, 1, 2]


def test_histogram_ingest_and_read_api(data_dir):
    client = TestClient(create_app(data_dir, api_key="k"))
    headers = {"x-api-key": "k"}
    client.post("/api/runs", json={"id": "h1", "project": "p", "name": "n"}, headers=headers)

    rows = {"rows": [
        {"key": "dist", "step": 0, "bins": [0.0, 1.0, 2.0], "counts": [3, 5], "ts": 1.0},
        {"key": "dist", "step": 1, "bins": [0.0, 1.0, 2.0], "counts": [4, 4], "ts": 2.0},
    ]}
    assert client.post("/api/runs/h1/histograms", json=rows, headers=headers).json()["inserted"] == 2

    keys = client.get("/api/runs/h1/histograms").json()
    assert keys[0]["key"] == "dist" and keys[0]["points"] == 2
    series = client.get("/api/runs/h1/histograms/dist").json()
    assert series["steps"] == [0, 1] and series["counts"] == [[3, 5], [4, 4]]

    # writes still need the key
    assert client.post("/api/runs/h1/histograms", json=rows).status_code == 401


def test_config_accepts_non_dict_types(data_dir):
    """config can be a dataclass / Namespace / pydantic model / plain object, not
    just a dict — each is normalized to a dict (private attrs dropped)."""
    import argparse
    from dataclasses import dataclass

    @dataclass
    class Cfg:
        lr: float = 1e-3
        bs: int = 64

    class Obj:
        def __init__(self) -> None:
            self.lr = 0.5
            self._secret = "x"  # leading-underscore attrs are not config

    cases = [
        (Cfg(), {"lr": 1e-3, "bs": 64}),
        (argparse.Namespace(lr=1e-3, bs=64), {"lr": 1e-3, "bs": 64}),
        (Obj(), {"lr": 0.5}),
    ]
    try:
        from pydantic import BaseModel

        class M(BaseModel):
            lr: float = 1e-3

        cases.append((M(), {"lr": 1e-3}))
    except ImportError:
        pass

    for cfg, expected in cases:
        run = pandm.init(project="p", config=cfg, directory=data_dir, remote=False)
        run.finish()
        assert _get_run(LocalStore(data_dir), run.id)["config"] == expected


def test_run_delete_local(data_dir):
    """run.delete() removes the run, its metrics, and its media — for cleaning up
    throwaway smoke-test runs without shelling out to `pandm delete`."""
    from pandm.sdk import _active_runs

    run = pandm.init(project="smoke", name="t", directory=data_dir, remote=False)
    run.log({"loss": 1.0})
    run.log_image("img", _png_bytes())
    rid = run.id
    run.delete()

    store = LocalStore(data_dir)
    assert store.get_run(rid) is None
    assert not (data_dir / "media" / rid).exists()
    assert run not in _active_runs  # won't be touched by the atexit finisher


def test_init_prints_run_banner(data_dir, capsys):
    run = pandm.init(project="p", name="banner-run", directory=data_dir, remote=False)
    run.finish()
    err = capsys.readouterr().err
    assert run.id in err and "banner-run" in err  # id surfaced for `pandm delete`, name to recognize


def test_run_banner_cloud_url(capsys):
    """In cloud mode the banner is a clickable dashboard deep link (wandb-style)."""
    from pandm.credentials import Remote
    from pandm.sdk import _print_run_banner

    class _R:
        id = "abcd1234"
        name = "t"
        project = "my proj"

    _print_run_banner(_R(), Remote("dual", "https://x.test", "k"))  # type: ignore[arg-type]
    err = capsys.readouterr().err
    assert "abcd1234" in err
    assert "https://x.test/?project=my%20proj&runs=abcd1234" in err


def test_meta_endpoint_merges_live(data_dir):
    client = TestClient(create_app(data_dir, api_key="k"))
    headers = {"x-api-key": "k"}
    client.post("/api/runs", json={"id": "mt1", "project": "p", "name": "n"}, headers=headers)

    # pushed while the run is still running — the dashboard sees specs without waiting for finish
    r = client.post(
        "/api/runs/mt1/meta",
        json={"metric_meta": {"win_rate": {"min": 0, "max": 1, "unit": "percent"}}},
        headers=headers,
    )
    assert r.status_code == 200
    run = client.get("/api/runs/mt1").json()
    assert run["status"] == "running"  # not finished
    assert run["metric_meta"]["win_rate"] == {"min": 0, "max": 1, "unit": "percent"}

    # a second push merges (last write wins per key), never clobbers
    client.post("/api/runs/mt1/meta", json={"metric_meta": {"loss": {"goal": "min"}}}, headers=headers)
    assert set(client.get("/api/runs/mt1").json()["metric_meta"]) == {"win_rate", "loss"}
