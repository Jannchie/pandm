"""Dual-write mode: local SQLite stays the source of truth and doubles as the
upload queue. A background Uploader pushes committed rows to the server by
cursor (sync_state table); every row carries its local rowid so the server can
drop replays (sync_progress watermark). Data logged offline is backfilled on
reconnect — or later by `pandm sync` for runs whose process already exited.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .client import _DEFAULT_TIMEOUT, _env_float, RemoteBackend
from .storage import LocalStore

_BATCH = 500
_PUMP_INTERVAL = 2.0
_LEASE_TTL = 60.0
_REMOTE_HEARTBEAT_EVERY = 30.0  # only beat remotely when the pump has been idle this long
_FINISH_DRAIN_BUDGET = 4.0  # default seconds to flush the tail before giving up to `pandm sync`


def _lease_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:4]}"


def pump_run(store: LocalStore, remote: RemoteBackend, run_id: str) -> bool:
    """Push pending metrics + media for one run. Returns True when fully drained.

    The caller must hold the sync lease. Cursor only advances after the server
    acks, so a crash here re-pushes — which the server's watermark dedupes.
    """
    state = store.get_sync_state(run_id)
    if state is None:
        return True
    while True:
        rows = store.unsynced_metrics(run_id, state["metrics_rowid"], _BATCH)
        if not rows:
            break
        ok = remote.log_metrics(
            run_id, [(r["key"], r["step"], r["value"], r["ts"], r["seq"]) for r in rows]
        )
        if not ok:
            return False
        state["metrics_rowid"] = rows[-1]["seq"]
        store.advance_sync_cursor(run_id, metrics_rowid=state["metrics_rowid"])

    for item in store.unsynced_media(run_id, state["media_id"], limit=_BATCH):
        path = store.media_path(run_id, item["filename"])
        if path is not None:  # missing file: skip it but still advance past it
            ok = remote.log_media(
                run_id,
                item["key"],
                item["step"],
                path.read_bytes(),
                Path(item["filename"]).suffix or ".png",
                item["caption"],
                item["ts"],
                media_seq=item["id"],
            )
            if not ok:
                return False
        store.advance_sync_cursor(run_id, media_id=item["id"])
    return True


class Uploader:
    """Background sync thread for one live run."""

    def __init__(self, root: Path, remote: RemoteBackend, run_id: str, create: tuple[str, str, dict, float]):
        self.store = LocalStore(root)  # own connection — never contends with the SDK's flush path
        self.remote = remote
        self.run_id = run_id
        self._create = create  # (project, name, config, created_at): replayed in-thread, never blocks init
        self.owner = _lease_owner()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_contact = 0.0
        self._pending_progress: tuple[float, float | None, float] | None = None  # latest unpushed (current, total, ts)
        self._pending_meta: dict[str, Any] | None = None  # latest unpushed display specs (define_metric)
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"pandm-sync-{run_id}")
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def set_progress(self, current: float, total: float | None, ts: float) -> None:
        self._pending_progress = (current, total, ts)  # overwrite: only the newest matters
        self._wake.set()

    def set_meta(self, specs: dict[str, Any]) -> None:
        self._pending_meta = {**(self._pending_meta or {}), **specs}  # accumulate until pushed
        self._wake.set()

    def _push_progress(self) -> None:
        prog = self._pending_progress
        if prog is None:
            return
        if self.remote.update_progress(self.run_id, *prog):
            if self._pending_progress == prog:  # leave a newer value queued for the next pump
                self._pending_progress = None
            self._last_contact = time.monotonic()

    def _push_meta(self) -> None:
        meta = self._pending_meta
        if not meta:
            return
        if self.remote.set_metric_meta(self.run_id, meta):
            if self._pending_meta == meta:  # leave newer specs queued for the next pump
                self._pending_meta = None
            self._last_contact = time.monotonic()

    def _pump(self) -> bool:
        if not self.store.claim_sync_lease(self.run_id, self.owner, _LEASE_TTL):
            return False  # someone else (pandm sync?) is pushing this run
        drained = pump_run(self.store, self.remote, self.run_id)
        if drained:
            self._last_contact = time.monotonic()
        return drained

    def _loop(self) -> None:
        project, name, config, created_at = self._create
        self.remote.create_run(self.run_id, project, name, config, created_at)
        while not self._stop.is_set():
            self._wake.wait(timeout=_PUMP_INTERVAL)
            self._wake.clear()
            try:
                self._pump()
                self._push_progress()
                self._push_meta()  # define_metric specs, pushed live like progress
                # keep the server's staleness detection fed during quiet stretches
                if time.monotonic() - self._last_contact >= _REMOTE_HEARTBEAT_EVERY:
                    if self.remote.heartbeat(self.run_id, time.time()):
                        self._last_contact = time.monotonic()
            except Exception:  # noqa: BLE001 — sync must never kill training
                pass

    def finish(self, status: str, finished_at: float) -> None:
        """Drain the tail, then push the final status — never the other way round.

        Hard-bounded by PANDM_FINISH_TIMEOUT: a slow or wedged server can never hold
        up process exit. The budget caps both how long we wait for the background
        thread to wind down and the synchronous drain that follows; whatever doesn't
        make it stays local for `pandm sync` to reconcile.
        """
        budget = _env_float("PANDM_FINISH_TIMEOUT", _FINISH_DRAIN_BUDGET)
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=budget)
        run = self.store.get_run(self.run_id)
        summary = run["summary"] if run else {}  # author scalars ride along with finish
        metric_meta = run["metric_meta"] if run else {}  # so do per-metric display specs
        try:
            if not self._thread.is_alive():  # still wedged → leave the tail to `pandm sync`
                deadline = time.monotonic() + budget
                with self.remote.deadline(budget):  # bound the requests, not just the loop
                    while time.monotonic() < deadline:
                        if self._pump():
                            if self.remote.finish_run(self.run_id, status, finished_at, summary, metric_meta):
                                self.store.advance_sync_cursor(self.run_id, status_synced=True)
                            break
                        time.sleep(0.2)  # offline backoff; don't spin the deadline away
        except Exception:  # noqa: BLE001
            pass  # tail stays local; `pandm sync` reconciles later
        finally:
            self.store.release_sync_lease(self.run_id, self.owner)
            self.store.close()


class DualBackend:
    """SDK backend for logged-in users: synchronous local writes + background upload."""

    def __init__(self, root: Path, server: str, api_key: str | None, transport: Any = None):
        self.local = LocalStore(root)
        self._root = root
        self._server = server
        self._api_key = api_key
        self._transport = transport
        self._uploader: Uploader | None = None

    def create_run(self, run_id: str, project: str, name: str, config: dict[str, Any]) -> None:
        now = time.time()
        self.local.create_run(run_id, project, name, config, created_at=now)
        self.local.ensure_sync_state(run_id)
        remote = RemoteBackend(self._server, self._api_key, transport=self._transport)
        self._uploader = Uploader(self._root, remote, run_id, create=(project, name, config, now))

    def run_exists(self, run_id: str) -> bool:
        return self.local.run_exists(run_id)  # local-first: resume continues the local run

    def resume_run(self, run_id: str) -> int:
        step = self.local.resume_run(run_id)
        # flip the cloud copy back to running too, but only if it was ever synced —
        # run_exists is a plain GET, so a never-synced run doesn't trip retry warnings.
        # Bounded best-effort: a slow server must not delay the resumed run's start.
        remote = RemoteBackend(self._server, self._api_key, transport=self._transport)
        with remote.deadline(_env_float("PANDM_SYNC_TIMEOUT", _DEFAULT_TIMEOUT)):
            if remote.run_exists(run_id):
                try:
                    remote.resume_run(run_id)
                except Exception:  # noqa: BLE001 — remote catches up on finish; local is the truth
                    pass
        return step

    def log_metrics(self, run_id: str, rows: list[tuple[str, int, float, float]]) -> None:
        self.local.log_metrics(run_id, rows)
        if self._uploader:
            self._uploader.notify()

    def log_media(
        self, run_id: str, key: str, step: int, data: bytes, ext: str, caption: str | None, ts: float
    ) -> None:
        self.local.log_media(run_id, key, step, data, ext, caption, ts)
        if self._uploader:
            self._uploader.notify()

    def heartbeat(self, run_id: str, ts: float) -> None:
        self.local.heartbeat(run_id, ts)  # remote beats are throttled inside the uploader loop

    def update_progress(self, run_id: str, current: float, total: float | None, ts: float) -> None:
        self.local.update_progress(run_id, current, total, ts)
        if self._uploader:
            self._uploader.set_progress(current, total, ts)  # remote push throttled in the uploader loop

    def set_summary(self, run_id: str, values: dict[str, Any]) -> None:
        self.local.set_summary(run_id, values)  # remote upload rides along with finish (§ no separate endpoint)

    def set_metric_meta(self, run_id: str, specs: dict[str, Any]) -> None:
        self.local.set_metric_meta(run_id, specs)
        if self._uploader:
            self._uploader.set_meta(specs)  # pushed live in the uploader loop, like progress

    def finish_run(self, run_id: str, status: str, finished_at: float) -> None:
        self.local.finish_run(run_id, status, finished_at)
        if self._uploader:
            self._uploader.finish(status, finished_at)
            self._uploader = None


def _sync_one(
    store: LocalStore, server: str, api_key: str | None, owner: str, run_id: str, transport: Any
) -> str:
    run = store.get_run(run_id)
    if run is None:
        return "not found"
    if not store.claim_sync_lease(run_id, owner, _LEASE_TTL):
        return "busy (live uploader holds the lease)"
    try:
        remote = RemoteBackend(server, api_key, transport=transport)
        remote.create_run(run_id, run["project"], run["name"], run["config"], run["created_at"])
        if run["metric_meta"]:
            remote.set_metric_meta(run_id, run["metric_meta"])  # display specs, even while still running
        if not pump_run(store, remote, run_id):
            return "server unreachable"
        if run["status"] != "running":
            if remote.finish_run(
                run_id,
                run["status"],
                run["finished_at"] or run["updated_at"],
                run["summary"],
                run["metric_meta"],
            ):
                store.advance_sync_cursor(run_id, status_synced=True)
        return "synced"
    finally:
        store.release_sync_lease(run_id, owner)


def sync_all(
    root: Path,
    server: str,
    api_key: str | None,
    run_ids: list[str] | None = None,
    track_all: bool = False,
    transport: Any = None,
    progress: Callable[[str, str], None] | None = None,
) -> list[tuple[str, str]]:
    """Backfill unsynced runs (`pandm sync`). Returns [(run_id, outcome), ...].

    Only cloud-tracked runs (those with a sync_state row) are considered unless
    `run_ids` opts specific runs in or `track_all` opts in every local run.
    """
    store = LocalStore(root)
    owner = _lease_owner()
    report: list[tuple[str, str]] = []
    try:
        if run_ids:
            for run_id in run_ids:
                store.ensure_sync_state(run_id)
        elif track_all:
            for run in store.list_runs():
                store.ensure_sync_state(run["id"])
        targets = run_ids or store.runs_needing_sync()

        for run_id in targets:
            outcome = _sync_one(store, server, api_key, owner, run_id, transport)
            report.append((run_id, outcome))
            if progress:
                progress(run_id, outcome)
    finally:
        store.close()
    return report
