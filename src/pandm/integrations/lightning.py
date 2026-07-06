"""PyTorch Lightning integration.

    from pandm.integrations.lightning import PandmLogger

    logger = PandmLogger(project="mnist", name="baseline", config={"lr": 1e-3})
    trainer = Trainer(logger=logger)
    trainer.fit(model)

    self.log("train/loss", loss)   # inside your LightningModule — pandm follows it

`PandmLogger` takes the same arguments as :func:`pandm.init`. The run is created
lazily on the first `log_hyperparams` / `log_metrics` call, so hyperparameters
Lightning captures (via `save_hyperparameters()` or `Trainer(..., logger=...)`)
are folded into the run config before it is written.

Lightning ships as two independent, mutually incompatible packages — the old
`pytorch_lightning` and the newer `lightning.pytorch` — whose `Logger` base
classes a `Trainer` will *not* accept across the divide. `PandmLogger` detects
which one is importable at runtime and subclasses that. When both are installed,
the base is chosen at import time and must match the `Trainer` you use: set
`PANDM_LIGHTNING_BACKEND=pytorch_lightning` (or `=lightning`) before importing to
force the choice instead of relying on detection order.

For images, histograms, or run-level summaries, reach the underlying run:

    logger.experiment.log_image("samples", image, step=step, caption=prompt)
    logger.experiment.summary({"best/acc": 0.99, "best/epoch": 7})
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..sdk import _coerce_config, _coerce_scalars, init

logger = logging.getLogger(__name__)


def _resolve_base(backend: str | None) -> tuple[type, Any]:
    """Return the (Logger base class, rank_zero_only) pair for the installed Lightning.

    `backend` forces `"pytorch_lightning"` or `"lightning"`; otherwise the standalone
    `pytorch_lightning` is tried first (it's what several trainers pin), then the
    namespaced `lightning.pytorch`.
    """

    def _pl() -> tuple[type, Any]:
        from pytorch_lightning.loggers import (  # pyright: ignore[reportMissingImports] -- optional dep
            Logger,
        )
        from pytorch_lightning.utilities import (  # pyright: ignore[reportMissingImports] -- optional dep
            rank_zero_only,
        )

        return Logger, rank_zero_only

    def _lightning() -> tuple[type, Any]:
        from lightning.pytorch.loggers import (  # pyright: ignore[reportMissingImports] -- optional dep
            Logger,
        )
        from lightning.pytorch.utilities import (  # pyright: ignore[reportMissingImports] -- optional dep
            rank_zero_only,
        )

        return Logger, rank_zero_only

    if backend == "pytorch_lightning":
        return _pl()
    if backend == "lightning":
        return _lightning()
    if backend is not None:
        raise ValueError(
            f"unknown backend {backend!r}; use 'pytorch_lightning' or 'lightning'"
        )
    try:
        return _pl()
    except ImportError:
        try:
            return _lightning()
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "pandm.integrations.lightning requires pytorch_lightning or "
                "lightning: pip install lightning"
            ) from e


# selected once at import; the chosen base decides which Trainer will accept the logger
_Base, rank_zero_only = _resolve_base(os.environ.get("PANDM_LIGHTNING_BACKEND") or None)


class PandmLogger(_Base):  # type: ignore[valid-type,misc]
    """A Lightning `Logger` backed by pandm. Takes the same arguments as :func:`pandm.init`."""

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
        self._name = name or project
        self._run: Any = None

    def _ensure_run(self) -> Any:
        if self._run is None:
            self._run = init(config=self._config, **self._init_kwargs)
        return self._run

    @property
    def experiment(self) -> Any:
        """The underlying :class:`pandm.Run` — for images, histograms, summaries."""
        return self._ensure_run()

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        # drives the checkpoint subdirectory; use the pandm run id once it exists
        return self._run.id if self._run is not None else ""

    @rank_zero_only
    def log_hyperparams(self, params: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        """Fold hyperparameters into the run config (before the run is created)."""
        values = _coerce_config(params)  # dict / Namespace / AttributeDict -> plain dict
        if self._run is not None:
            logger.warning(
                "pandm run already started — ignoring late config keys %s", list(values)
            )
            return
        self._config.update(values)

    @rank_zero_only
    def log_metrics(self, metrics: dict[str, Any], step: int | None = None) -> None:
        """Log scalar metrics. Non-numeric values are skipped; `step` follows Lightning."""
        scalars = _coerce_scalars(metrics)
        if scalars:
            self._ensure_run().log(scalars, step=step)

    @rank_zero_only
    def finalize(self, status: str = "success") -> None:
        """Finish the run when training ends (Lightning passes 'success'/'failed')."""
        if self._run is not None:
            self._run.finish("finished" if status == "success" else "crashed")

    @rank_zero_only
    def save(self) -> None:  # Lightning calls this periodically; pandm streams already
        pass
