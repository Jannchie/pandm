"""CSV ingestion tests: Run.ingest_csv (one-shot) and Run.watch_csv (live tail).

These exercise the framework-agnostic path for closed-source trainers (rfdetr,
YOLO, ...) that only dump a metrics.csv and never expose a logger seat.
"""

from __future__ import annotations

import time

import pytest

import pandm
from pandm.storage import LocalStore


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / ".pandm"


def test_ingest_csv_dedup_and_full_table_rewrite(tmp_path, data_dir):
    csvp = tmp_path / "metrics.csv"
    csvp.write_text("epoch,train/loss,val/acc,note\n0,1.5,0.40,warmup\n1,0.9,0.55,warmup\n")

    run = pandm.init(project="p", name="csv", directory=data_dir, remote=False)
    assert run.ingest_csv(csvp, step_column="epoch") == 2
    assert run.ingest_csv(csvp, step_column="epoch") == 0  # nothing new

    # rfdetr rewrites the whole table each epoch, appending the new row at the bottom
    csvp.write_text(
        "epoch,train/loss,val/acc,note\n"
        "0,1.5,0.40,warmup\n1,0.9,0.55,warmup\n2,0.6,0.63,warmup\n"
    )
    assert run.ingest_csv(csvp, step_column="epoch") == 1
    run.finish()

    store = LocalStore(data_dir)
    loss = store.metric_series(run.id, "train/loss")
    assert loss["steps"] == [0, 1, 2]
    assert loss["values"] == [1.5, 0.9, 0.6]
    # non-numeric "note" column is skipped, not stored as a metric
    assert store.metric_series(run.id, "note")["steps"] == []


def test_ingest_csv_include_exclude_and_prefix(tmp_path, data_dir):
    csvp = tmp_path / "m.csv"
    csvp.write_text("step,a,b,c\n0,1,2,3\n")
    run = pandm.init(project="p", directory=data_dir, remote=False)
    run.ingest_csv(csvp, step_column="step", include=["a", "c"], prefix="val/")
    run.finish()

    store = LocalStore(data_dir)
    keys = {k["key"] for k in store.metric_keys(run.id)}
    assert keys == {"val/a", "val/c"}  # b excluded, prefix applied
    assert store.metric_series(run.id, "val/a")["steps"] == [0]


def test_ingest_csv_missing_file_is_noop(tmp_path, data_dir):
    run = pandm.init(project="p", directory=data_dir, remote=False)
    assert run.ingest_csv(tmp_path / "nope.csv", step_column="epoch") == 0
    run.finish()


def test_watch_csv_tails_live_and_finish_drains(tmp_path, data_dir):
    csvp = tmp_path / "metrics.csv"
    csvp.write_text("epoch,loss\n")

    run = pandm.init(project="p", name="w", directory=data_dir, remote=False)
    run.watch_csv(csvp, step_column="epoch", interval=0.05)

    lines = ["epoch,loss\n"]
    for e in range(5):
        lines.append(f"{e},{1.0 / (e + 1):.3f}\n")
        csvp.write_text("".join(lines))
        time.sleep(0.03)

    run.finish()  # must drain rows written since the last poll

    store = LocalStore(data_dir)
    assert store.metric_series(run.id, "loss")["steps"] == [0, 1, 2, 3, 4]


def test_ingest_csv_after_finish_raises(tmp_path, data_dir):
    csvp = tmp_path / "m.csv"
    csvp.write_text("epoch,loss\n0,1.0\n")
    run = pandm.init(project="p", directory=data_dir, remote=False)
    run.finish()
    with pytest.raises(RuntimeError):
        run.ingest_csv(csvp, step_column="epoch")
