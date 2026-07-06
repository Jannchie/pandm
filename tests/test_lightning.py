"""PyTorch Lightning integration tests.

Lightning ships as two mutually incompatible packages (`pytorch_lightning` and
`lightning.pytorch`). We stub a minimal `pytorch_lightning` so the tests run
without installing the real thing (which drags in torch), exercising runtime
base resolution, lazy run creation, config folding, scalar filtering, and
finalize -> status mapping.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest

from pandm.storage import LocalStore


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / ".pandm"


@pytest.fixture()
def lightning_module(monkeypatch):
    """Import pandm.integrations.lightning against a minimal pytorch_lightning stub."""

    class Logger:  # stand-in for the real ABC
        def __init__(self, *a, **k):
            pass

    def rank_zero_only(fn):  # passthrough (single process in tests)
        return fn

    loggers: Any = types.ModuleType("pytorch_lightning.loggers")
    loggers.Logger = Logger
    utilities: Any = types.ModuleType("pytorch_lightning.utilities")
    utilities.rank_zero_only = rank_zero_only
    pkg: Any = types.ModuleType("pytorch_lightning")
    pkg.loggers = loggers
    pkg.utilities = utilities
    monkeypatch.setitem(sys.modules, "pytorch_lightning", pkg)
    monkeypatch.setitem(sys.modules, "pytorch_lightning.loggers", loggers)
    monkeypatch.setitem(sys.modules, "pytorch_lightning.utilities", utilities)
    sys.modules.pop("pandm.integrations.lightning", None)
    mod = importlib.import_module("pandm.integrations.lightning")
    mod._Logger_base = Logger  # expose for the subclass assertion
    yield mod
    sys.modules.pop("pandm.integrations.lightning", None)


def test_subclasses_installed_base(lightning_module):
    assert issubclass(lightning_module.PandmLogger, lightning_module._Logger_base)


def test_lazy_init_folds_hyperparams_and_logs(lightning_module, data_dir):
    lg = lightning_module.PandmLogger(
        project="proj", name="lit", config={"seed": 1}, directory=data_dir
    )
    assert LocalStore(data_dir).list_runs("proj") == []  # no run until first callback

    lg.log_hyperparams({"lr": 0.1})
    lg.log_metrics({"train/loss": 0.9, "bad": "x"}, step=0)  # string metric skipped
    lg.log_metrics({"train/loss": 0.4}, step=1)
    lg.finalize("success")

    store = LocalStore(data_dir)
    runs = store.list_runs("proj")
    assert len(runs) == 1
    assert runs[0]["name"] == "lit"
    assert runs[0]["config"] == {"seed": 1, "lr": 0.1}
    assert runs[0]["status"] == "finished"
    assert {k["key"] for k in store.metric_keys(runs[0]["id"])} == {"train/loss"}
    assert store.metric_series(runs[0]["id"], "train/loss")["steps"] == [0, 1]


def test_finalize_failed_maps_to_crashed(lightning_module, data_dir):
    lg = lightning_module.PandmLogger(project="proj", directory=data_dir)
    lg.log_metrics({"loss": 1.0}, step=0)
    lg.finalize("failed")
    runs = LocalStore(data_dir).list_runs("proj")
    assert runs[0]["status"] == "crashed"


def test_hyperparams_accepts_namespace(lightning_module, data_dir):
    import argparse

    lg = lightning_module.PandmLogger(project="proj", directory=data_dir)
    lg.log_hyperparams(argparse.Namespace(lr=0.01, bs=32))
    lg.log_metrics({"loss": 1.0}, step=0)
    lg.finalize("success")
    runs = LocalStore(data_dir).list_runs("proj")
    assert runs[0]["config"] == {"lr": 0.01, "bs": 32}


def test_explicit_backend_selection(lightning_module):
    # unknown backend name is rejected up front
    with pytest.raises(ValueError, match="unknown backend"):
        lightning_module._resolve_base("keras")
