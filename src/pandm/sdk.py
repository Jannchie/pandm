"""The pandm SDK.

    import pandm

    run = pandm.init(project="mnist", config={"lr": 1e-3})
    run.log({"loss": 0.42, "acc": 0.91}, step=10)
    run.log_image("samples", img, step=10, caption="epoch 1")
    run.finish()

Local mode (default) writes straight to `.pandm/`. Set `PANDM_REMOTE` (or pass
`remote=`) to report to a `pandm server` over HTTP instead — or sign in with
`pandm login` to get dual-write: local stays the source of truth and a
background thread syncs to the server, backfilling whatever was logged offline.
"""

from __future__ import annotations

import atexit
import io
import math
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .storage import LocalStore, new_run_id, resolve_dir

_FLUSH_INTERVAL = 0.5
_FLUSH_THRESHOLD = 256
_HEARTBEAT_INTERVAL = 15.0  # keep updated_at fresh even when nothing is logged

_ADJECTIVES = (
    "amber", "brisk", "calm", "dapper", "eager", "fuzzy", "gentle", "hazy",
    "ivory", "jolly", "keen", "lunar", "mellow", "nimble", "opal", "plucky",
    "quiet", "rustic", "swift", "tidal", "vivid", "wistful", "zesty",
)
_NOUNS = (
    "aurora", "breeze", "comet", "dune", "ember", "fjord", "glade", "harbor",
    "iris", "jade", "koi", "lagoon", "meadow", "nebula", "orchid", "pine",
    "quartz", "river", "summit", "thicket", "umbra", "valley", "willow",
)

_active_runs: list["Run"] = []
_atexit_registered = False
_crashed = False  # set by the excepthook so atexit knows how the process died


def _generate_name() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{random.randint(1, 99)}"


def _register_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_finish_all)
        prev_hook = sys.excepthook

        def _excepthook(exc_type: Any, exc: Any, tb: Any) -> None:
            global _crashed
            _crashed = True
            prev_hook(exc_type, exc, tb)

        sys.excepthook = _excepthook
        _atexit_registered = True


def _finish_all() -> None:
    for run in list(_active_runs):
        run.finish("crashed" if _crashed else "finished")


def init(
    project: str = "default",
    name: str | None = None,
    config: dict[str, Any] | None = None,
    *,
    directory: str | os.PathLike | None = None,
    remote: str | bool | None = None,
    api_key: str | None = None,
) -> "Run":
    """Start a new run. Returns a :class:`Run`; also usable as a context manager.

    `remote=` / `PANDM_REMOTE` -> remote-only; saved `pandm login` credentials
    -> dual-write (local + sync); `remote=False` / `PANDM_NO_SYNC` -> local-only.
    """
    from .credentials import resolve_remote

    resolved = resolve_remote(remote, api_key)
    if resolved.mode == "remote_only":
        from .client import RemoteBackend

        backend: Any = RemoteBackend(resolved.url, resolved.api_key)  # type: ignore[arg-type]
    elif resolved.mode == "dual":
        from .sync import DualBackend

        backend = DualBackend(resolve_dir(directory), resolved.url, resolved.api_key)  # type: ignore[arg-type]
    else:
        backend = LocalStore(resolve_dir(directory))
    run = Run(backend, project=project, name=name, config=config or {})
    _active_runs.append(run)
    _register_atexit()
    return run


def log(metrics: dict[str, Any], step: int | None = None) -> None:
    """Log to the most recently started run (convenience, mirrors run.log)."""
    _current().log(metrics, step=step)


def log_image(key: str, image: Any, step: int | None = None, caption: str | None = None) -> None:
    """Log an image to the most recently started run."""
    _current().log_image(key, image, step=step, caption=caption)


def finish(status: str = "finished") -> None:
    """Finish the most recently started run."""
    _current().finish(status)


def _current() -> "Run":
    if not _active_runs:
        raise RuntimeError("no active run — call pandm.init() first")
    return _active_runs[-1]


class Run:
    def __init__(self, backend: Any, project: str, name: str | None, config: dict[str, Any]):
        self.id = new_run_id()
        self.project = project
        self.name = name or _generate_name()
        self.config = dict(config)
        self._backend = backend
        self._buffer: list[tuple[str, int, float, float]] = []
        self._buf_lock = threading.Lock()
        self._finished = False
        self._step = 0
        self._last_activity = time.time()
        backend.create_run(self.id, project, self.name, self.config)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name=f"pandm-{self.id}")
        self._thread.start()

    # ----------------------------------------------------------- logging

    def log(self, metrics: dict[str, Any], step: int | None = None) -> None:
        """Log scalar metrics. `step` defaults to an internal per-run counter."""
        if self._finished:
            raise RuntimeError(f"run {self.id} is already finished")
        ts = time.time()
        if step is None:
            step = self._step
        step = int(step)
        self._step = max(self._step, step + 1)
        # non-finite values (NaN/Inf) are dropped — they would poison JSON responses
        rows = [
            (str(k), step, fv, ts)
            for k, v in metrics.items()
            if math.isfinite(fv := float(v))
        ]
        with self._buf_lock:
            self._buffer.extend(rows)
            should_flush = len(self._buffer) >= _FLUSH_THRESHOLD
        if should_flush:
            self._flush()

    def log_image(self, key: str, image: Any, step: int | None = None, caption: str | None = None) -> None:
        """Log an image: PIL Image, numpy/torch array (HWC or CHW), file path, or raw bytes."""
        if self._finished:
            raise RuntimeError(f"run {self.id} is already finished")
        if step is None:
            step = max(0, self._step - 1)  # align with the latest metric step
        data, ext = _encode_image(image)
        self._flush()  # keep metric/media ordering roughly consistent
        self._backend.log_media(self.id, str(key), int(step), data, ext, caption, time.time())

    def finish(self, status: str = "finished") -> None:
        if self._finished:
            return
        self._finished = True
        self._stop.set()
        self._thread.join(timeout=5)
        self._flush()
        self._backend.finish_run(self.id, status, time.time())
        if self in _active_runs:
            _active_runs.remove(self)

    # ---------------------------------------------------------- plumbing

    def _flush_loop(self) -> None:
        while not self._stop.wait(_FLUSH_INTERVAL):
            self._flush()
            # heartbeat during idle stretches (long validation, data stalls),
            # so a dead process is distinguishable from a quiet one
            now = time.time()
            if now - self._last_activity >= _HEARTBEAT_INTERVAL:
                self._last_activity = now
                try:
                    self._backend.heartbeat(self.id, now)
                except Exception:  # noqa: BLE001 — heartbeats must never kill training
                    pass

    def _flush(self) -> None:
        with self._buf_lock:
            rows, self._buffer = self._buffer, []
        if rows:
            self._backend.log_metrics(self.id, rows)
            self._last_activity = time.time()

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.finish("crashed" if exc_type is not None else "finished")

    def __repr__(self) -> str:
        return f"Run(id={self.id!r}, name={self.name!r}, project={self.project!r})"


def _encode_image(image: Any) -> tuple[bytes, str]:
    """Normalize the various accepted image types into (bytes, ext)."""
    if isinstance(image, (str, Path)):
        path = Path(image)
        ext = path.suffix.lower() or ".png"
        return path.read_bytes(), ext
    if isinstance(image, (bytes, bytearray)):
        return bytes(image), ".png"

    from PIL import Image

    if isinstance(image, Image.Image):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue(), ".png"

    if hasattr(image, "detach"):  # torch tensor -> numpy, without importing torch
        image = image.detach().cpu().numpy()

    if hasattr(image, "__array_interface__") or type(image).__module__.startswith("numpy"):
        import numpy as np  # pyright: ignore[reportMissingImports] -- present whenever the user passes an ndarray

        arr = np.asarray(image)
        if arr.ndim == 3 and arr.shape[0] in (1, 3, 4) and arr.shape[2] not in (1, 3, 4):
            arr = arr.transpose(1, 2, 0)  # CHW -> HWC
        if arr.ndim == 3 and arr.shape[2] == 1:
            arr = arr[:, :, 0]
        if arr.dtype.kind == "f":
            arr = arr * 255 if arr.max() <= 1.0 else arr
        arr = arr.clip(0, 255).astype("uint8")
        pil = Image.fromarray(arr)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue(), ".png"

    raise TypeError(f"unsupported image type: {type(image)!r}")
