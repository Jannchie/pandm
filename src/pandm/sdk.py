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
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .storage import LocalStore, new_run_id, resolve_dir

_FLUSH_INTERVAL = 0.5
_FLUSH_THRESHOLD = 256
_HEARTBEAT_INTERVAL = 15.0  # keep updated_at fresh even when nothing is logged

_active_runs: list["Run"] = []
_atexit_registered = False
_crashed = False  # set by the excepthook so atexit knows how the process died
_login_offered = False  # one login offer per process, however many init() calls follow


def _generate_name() -> str:
    """Default run name: a sortable local timestamp like 2026-06-10_14:30:52.
    Pass name= to override. (Runs are still uniquely keyed by their id.)"""
    return time.strftime("%Y-%m-%d_%H:%M:%S")


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


def _is_interactive() -> bool:
    """A human is at the terminal and can answer a prompt without blocking a
    backgrounded run. Factored out so tests can drive both branches."""
    return bool(sys.stdin) and sys.stdin.isatty() and sys.stderr.isatty()


def _maybe_offer_login(resolved: Any, remote: str | bool | None, api_key: str | None) -> Any:
    """A run is about to go local-only. Unless the user has opted out, offer to
    sign in: an interactive terminal gets a prompt (and, if they choose, an inline
    login that upgrades *this* run to dual-write); a non-interactive one gets a
    single hint on stderr. Never blocks a non-interactive run, never raises.

    Returns the Remote to use — upgraded to dual if the user logs in here, else
    the unchanged local `resolved`.
    """
    global _login_offered
    from . import credentials

    # explicit local intent, silenced, or already dismissed -> never offer
    if (
        remote is False
        or os.environ.get("PANDM_NO_SYNC")
        or os.environ.get("PANDM_SILENT")
        or credentials.is_opted_out()
    ):
        return resolved
    if _login_offered:
        return resolved
    _login_offered = True

    if not _is_interactive():
        print(
            "pandm: not logged in — this run is saved locally to .pandm. "
            "Run `pandm login` to sync to the cloud (PANDM_SILENT=1 silences this).",
            file=sys.stderr,
        )
        return resolved

    try:
        print("\npandm: you're not logged in; this run will be saved locally only.", file=sys.stderr)
        print(
            "  [l] log in and sync to the cloud   "
            "[k] keep local, don't ask again   "
            "[Enter] not now",
            file=sys.stderr,
        )
        choice = input("pandm> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return resolved

    if choice in ("l", "login", "y", "yes"):
        saved = credentials.device_login(
            credentials.DEFAULT_SERVER, echo=lambda m: print(m, file=sys.stderr)
        )
        if saved:
            credentials.set_opted_out()  # signed in -> stop nagging on later runs
            print(
                f"pandm: signed in as {saved['login']} — this run will sync to {saved['server']}.",
                file=sys.stderr,
            )
            return credentials.resolve_remote(remote, api_key)  # now resolves to dual
        print("pandm: not signed in; continuing locally.", file=sys.stderr)
        return resolved
    if choice in ("k", "keep"):
        credentials.set_opted_out()
        print(
            "pandm: staying local. Run `pandm login` whenever you like; "
            "PANDM_SILENT=1 also silences this.",
            file=sys.stderr,
        )
        return resolved
    return resolved  # "not now" — ask again next process


def init(
    project: str = "default",
    name: str | None = None,
    config: dict[str, Any] | None = None,
    *,
    id: str | None = None,
    resume: bool | str = False,
    total_steps: int | None = None,
    directory: str | os.PathLike | None = None,
    remote: str | bool | None = None,
    api_key: str | None = None,
) -> "Run":
    """Start a new run. Returns a :class:`Run`; also usable as a context manager.

    `total_steps=` declares the training length so the dashboard can estimate an
    ETA: progress then tracks the latest `log(step=...)` automatically. For other
    units (epochs, samples) call `run.set_progress(current, total)` instead.

    `id=` sets the run id (otherwise random). `resume=` continues an existing run
    under that id — `True`/`"allow"` reopens it if present (else starts fresh),
    `"must"` errors if it's missing; a fresh `id=` that already exists errors
    unless `resume` is set. A resumed run flips back to `running` and its auto
    step counter continues past the last logged step (its original config is kept).

    `remote=` / `PANDM_REMOTE` -> remote-only; saved `pandm login` credentials
    -> dual-write (local + sync); `remote=False` / `PANDM_NO_SYNC` -> local-only.
    """
    from .credentials import resolve_remote

    resolved = resolve_remote(remote, api_key)
    if resolved.mode == "local":
        resolved = _maybe_offer_login(resolved, remote, api_key)
    if resolved.mode == "remote_only":
        from .client import RemoteBackend

        backend: Any = RemoteBackend(resolved.url, resolved.api_key)  # type: ignore[arg-type]
    elif resolved.mode == "dual":
        from .sync import DualBackend

        backend = DualBackend(resolve_dir(directory), resolved.url, resolved.api_key)  # type: ignore[arg-type]
    else:
        backend = LocalStore(resolve_dir(directory))
    run = Run(
        backend, project=project, name=name, config=config or {},
        total_steps=total_steps, run_id=id, resume=resume,
    )
    _active_runs.append(run)
    _register_atexit()
    return run


def log(metrics: dict[str, Any], step: int | None = None) -> None:
    """Log to the most recently started run (convenience, mirrors run.log)."""
    _current().log(metrics, step=step)


def log_image(key: str, image: Any, step: int | None = None, caption: str | None = None) -> None:
    """Log an image to the most recently started run."""
    _current().log_image(key, image, step=step, caption=caption)


def set_progress(current: float, total: float | None = None) -> None:
    """Report training progress on the most recently started run (mirrors run.set_progress)."""
    _current().set_progress(current, total=total)


def finish(status: str = "finished") -> None:
    """Finish the most recently started run."""
    _current().finish(status)


def _current() -> "Run":
    if not _active_runs:
        raise RuntimeError("no active run — call pandm.init() first")
    return _active_runs[-1]


class Run:
    def __init__(
        self,
        backend: Any,
        project: str,
        name: str | None,
        config: dict[str, Any],
        total_steps: int | None = None,
        run_id: str | None = None,
        resume: bool | str = False,
    ):
        self.id = run_id or new_run_id()
        self.project = project
        self.name = name or _generate_name()
        self.config = dict(config)
        self._backend = backend
        self._buffer: list[tuple[str, int, float, float]] = []
        self._buf_lock = threading.Lock()
        self._finished = False
        self._step = 0
        self._last_activity = time.time()
        # progress for ETA: tracks the latest step when total_steps is set, or is
        # driven explicitly by set_progress(). Reported (throttled) by the flush loop.
        self._progress_current: float | None = None
        self._progress_total: float | None = float(total_steps) if total_steps else None
        self._progress_dirty = False
        # resume: "allow" reopens an existing run (else fresh); "must" requires it;
        # False refuses to reuse an existing id. The auto step counter continues
        # past the last logged step so a resumed loop doesn't overwrite history.
        mode = resume if isinstance(resume, str) else ("allow" if resume else False)
        if mode == "never":
            mode = False
        existed = bool(run_id) and backend.run_exists(self.id)
        if existed and not mode:
            raise ValueError(
                f"run {self.id!r} already exists — pass resume=True to continue it, or use a new id"
            )
        if mode == "must" and not existed:
            raise ValueError(f"resume='must' but run {self.id!r} was not found")
        backend.create_run(self.id, project, self.name, self.config)
        if existed and mode:
            self._step = int(backend.resume_run(self.id)) + 1
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
            if self._progress_total is not None:  # total declared -> progress follows the step
                self._progress_current = step + 1  # steps completed through this one
                self._progress_dirty = True
            should_flush = len(self._buffer) >= _FLUSH_THRESHOLD
        if should_flush:
            self._flush()

    def set_progress(self, current: float, total: float | None = None) -> None:
        """Report training progress for the dashboard ETA. `current`/`total` are in
        any unit you choose (step, epoch, sample); `total` may be omitted to reuse
        the last one. Throttled to the flush interval — call it freely each loop."""
        if self._finished:
            raise RuntimeError(f"run {self.id} is already finished")
        with self._buf_lock:
            self._progress_current = float(current)
            if total is not None:
                self._progress_total = float(total)
            self._progress_dirty = True

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
        self._report_progress()  # push the final progress before the status flips
        self._backend.finish_run(self.id, status, time.time())
        if self in _active_runs:
            _active_runs.remove(self)

    # ---------------------------------------------------------- plumbing

    def _flush_loop(self) -> None:
        while not self._stop.wait(_FLUSH_INTERVAL):
            self._flush()
            self._report_progress()
            # heartbeat during idle stretches (long validation, data stalls),
            # so a dead process is distinguishable from a quiet one
            now = time.time()
            if now - self._last_activity >= _HEARTBEAT_INTERVAL:
                self._last_activity = now
                try:
                    self._backend.heartbeat(self.id, now)
                except Exception:  # noqa: BLE001 — heartbeats must never kill training
                    pass

    def _report_progress(self) -> None:
        """Push the latest progress if it changed since the last report (throttled
        to the flush cadence). A no-op until set_progress / total_steps drives it."""
        with self._buf_lock:
            if not self._progress_dirty:
                return
            current, total = self._progress_current, self._progress_total
            self._progress_dirty = False
        if current is None:
            return
        try:
            self._backend.update_progress(self.id, current, total, time.time())
            self._last_activity = time.time()
        except Exception:  # noqa: BLE001 — progress is best-effort, never kill training
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
