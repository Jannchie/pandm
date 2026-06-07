"""Accelerate integration tests.

The unit tests stub out `accelerate.tracking` (the real package drags in
torch), exercising the tracker's own logic: lazy run creation, config merging,
scalar filtering, image fan-out. A final test runs the real `Accelerator` flow
when accelerate happens to be installed.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest
from PIL import Image

from pandm.storage import LocalStore


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / ".pandm"


@pytest.fixture()
def tracker_module(monkeypatch):
    """Import pandm.integrations.accelerate against a minimal accelerate stub."""
    tracking: Any = types.ModuleType("accelerate.tracking")

    class GeneralTracker:
        main_process_only = True

        def __init__(self, _blank=False):
            pass

        def start(self):
            pass

    def on_main_process(fn):
        return fn

    tracking.GeneralTracker = GeneralTracker
    tracking.on_main_process = on_main_process
    pkg: Any = types.ModuleType("accelerate")
    pkg.tracking = tracking
    monkeypatch.setitem(sys.modules, "accelerate", pkg)
    monkeypatch.setitem(sys.modules, "accelerate.tracking", tracking)
    sys.modules.pop("pandm.integrations.accelerate", None)
    yield importlib.import_module("pandm.integrations.accelerate")
    # next import must rebind against whatever accelerate is actually installed
    sys.modules.pop("pandm.integrations.accelerate", None)


def test_lazy_init_merges_config(tracker_module, data_dir):
    tracker = tracker_module.PandmTracker(project="proj", name="acc", config={"seed": 1}, directory=data_dir)
    assert LocalStore(data_dir).list_runs("proj") == []  # no run until first callback

    tracker.store_init_configuration({"lr": 0.1})
    tracker.log({"loss": 2.0, "note": "a string"}, step=0)  # string metric skipped
    tracker.log_images({"samples": Image.new("RGB", (8, 8))}, step=0, caption="hi")
    tracker.finish()

    store = LocalStore(data_dir)
    runs = store.list_runs("proj")
    assert len(runs) == 1
    assert runs[0]["name"] == "acc"
    assert runs[0]["config"] == {"seed": 1, "lr": 0.1}
    assert runs[0]["status"] == "finished"
    assert {k["key"] for k in store.metric_keys(runs[0]["id"])} == {"loss"}
    media = store.list_media(runs[0]["id"])
    assert len(media) == 1
    assert media[0]["caption"] == "hi"


def test_log_without_config_still_creates_run(tracker_module, data_dir):
    # init_trackers(config=None) never calls store_init_configuration
    tracker = tracker_module.PandmTracker(project="proj", directory=data_dir)
    tracker.log({"loss": 1.0}, step=0)
    tracker.finish()
    runs = LocalStore(data_dir).list_runs("proj")
    assert len(runs) == 1
    assert runs[0]["config"] == {}


def test_finish_without_run_is_noop(tracker_module, data_dir):
    tracker = tracker_module.PandmTracker(project="proj", directory=data_dir)
    tracker.finish()
    assert LocalStore(data_dir).list_runs("proj") == []


def test_tracker_property_unwraps_run(tracker_module, data_dir):
    tracker = tracker_module.PandmTracker(project="proj", directory=data_dir)
    run = tracker.tracker
    run.log_image("img", Image.new("RGB", (4, 4)), step=3, caption="direct")
    tracker.finish()
    assert LocalStore(data_dir).list_media(run.id)[0]["caption"] == "direct"


def test_image_list_fans_out(tracker_module, data_dir):
    tracker = tracker_module.PandmTracker(project="proj", directory=data_dir)
    tracker.log_images({"grid": [Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4))]}, step=1)
    tracker.finish()
    runs = LocalStore(data_dir).list_runs("proj")
    assert len(LocalStore(data_dir).list_media(runs[0]["id"])) == 2


def test_real_accelerate_end_to_end(data_dir):
    accelerate = pytest.importorskip("accelerate")
    sys.modules.pop("pandm.integrations.accelerate", None)
    tracker_module = importlib.import_module("pandm.integrations.accelerate")

    tracker = tracker_module.PandmTracker(project="proj", name="real", directory=data_dir)
    accelerator = accelerate.Accelerator(log_with=tracker)
    accelerator.init_trackers("ignored-project-name", config={"lr": 0.1})
    accelerator.log({"loss": 1.0}, step=0)
    run = accelerator.get_tracker("pandm", unwrap=True)
    run.log_image("samples", Image.new("RGB", (8, 8)), step=0, caption="hi")
    accelerator.end_training()

    store = LocalStore(data_dir)
    runs = store.list_runs("proj")
    assert len(runs) == 1
    assert runs[0]["name"] == "real"
    assert runs[0]["config"] == {"lr": 0.1}
    assert runs[0]["status"] == "finished"
    assert len(store.list_media(runs[0]["id"])) == 1
