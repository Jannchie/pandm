"""Hugging Face Accelerate integration.

    from accelerate import Accelerator
    from pandm.integrations.accelerate import PandmTracker

    tracker = PandmTracker(project="mnist", name="baseline")
    accelerator = Accelerator(log_with=tracker)
    accelerator.init_trackers("mnist", config={"lr": 1e-3})
    accelerator.log({"loss": 0.42}, step=10)
    accelerator.end_training()

Accelerate only resolves strings like `log_with="wandb"` for its built-in
trackers, so `PandmTracker` must be passed as an instance. The project and run
name come from the constructor (same arguments as :func:`pandm.init`) — the
`project_name` argument of `init_trackers` is not forwarded to custom trackers.

For images and anything else beyond scalar metrics, unwrap the raw run:

    run = accelerator.get_tracker("pandm", unwrap=True)
    run.log_image("samples", image, step=step, caption=prompt)
    run.summary({"best/spearman": 0.773, "best/epoch": 7})  # run-level scalars at the end
"""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    from accelerate.tracking import GeneralTracker, on_main_process  # pyright: ignore[reportMissingImports] -- optional dependency
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "pandm.integrations.accelerate requires the `accelerate` package: pip install accelerate"
    ) from e

from ..sdk import Run, init

logger = logging.getLogger(__name__)


class PandmTracker(GeneralTracker):
    """An Accelerate tracker backed by pandm. Takes the same arguments as :func:`pandm.init`.

    The run is created lazily on the first `store_init_configuration` / `log`
    call, so the hyperparameters Accelerate passes via `init_trackers(config=...)`
    are merged into the run config before it is written.
    """

    name = "pandm"
    requires_logging_directory = False

    def __init__(
        self,
        project: str = "default",
        name: str | None = None,
        config: dict[str, Any] | None = None,
        *,
        directory: str | os.PathLike | None = None,
        remote: str | bool | None = None,
        api_key: str | None = None,
    ):
        super().__init__()
        self._init_kwargs: dict[str, Any] = {
            "project": project,
            "name": name,
            "directory": directory,
            "remote": remote,
            "api_key": api_key,
        }
        self._config: dict[str, Any] = dict(config or {})
        self._run: Run | None = None

    @property
    def tracker(self) -> Run:
        """The underlying :class:`pandm.Run` (for `accelerator.get_tracker("pandm", unwrap=True)`)."""
        return self._ensure_run()

    def _ensure_run(self) -> Run:
        if self._run is None:
            self._run = init(config=self._config, **self._init_kwargs)
        return self._run

    @on_main_process
    def store_init_configuration(self, values: dict) -> None:
        """Fold `values` into the run config and create the run."""
        if self._run is not None:
            # the run config is written at creation; too late to amend it
            logger.warning("pandm run already started — ignoring late config keys %s", list(values))
            return
        self._config.update(values)
        self._ensure_run()

    @on_main_process
    def log(self, values: dict, step: int | None = None, **kwargs: Any) -> None:  # noqa: ARG002 -- protocol signature
        """Log scalar metrics. Non-numeric values (e.g. strings) are skipped."""
        scalars = {}
        for k, v in values.items():
            try:
                scalars[k] = float(v)
            except (TypeError, ValueError):
                continue  # pandm stores scalar metrics only
        if scalars:
            self._ensure_run().log(scalars, step=step)

    @on_main_process
    def log_images(self, values: dict, step: int | None = None, **kwargs: Any) -> None:
        """Log a dict of `key -> image` (or list / batched array of images).

        Mirrors the built-in trackers' convention; `kwargs` (e.g. `caption=`)
        are forwarded to :meth:`pandm.Run.log_image`.
        """
        run = self._ensure_run()
        for key, value in values.items():
            if getattr(value, "ndim", None) == 4:  # batched NCHW/NHWC array
                images = list(value)
            elif isinstance(value, (list, tuple)):
                images = value
            else:
                images = [value]
            for image in images:
                run.log_image(key, image, step=step, **kwargs)

    @on_main_process
    def finish(self) -> None:
        """Called by `accelerator.end_training()`."""
        if self._run is not None:
            self._run.finish()
