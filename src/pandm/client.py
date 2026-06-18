"""HTTP backend for cloud mode — mirrors LocalStore's write API.

Deliberately forgiving: if the server is unreachable the SDK warns and drops
data instead of killing the training process, then quietly retries after a
cooldown. The run-creation request is replayed on recovery so a run that
started during an outage still shows up once the server is back.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from typing import Any, Iterator

import httpx

# httpx logs every request at INFO ("HTTP Request: POST …/metrics 200 OK") —
# pure noise inside a training loop that pushes metrics every couple of seconds
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_RETRIES = 3
_COOLDOWN = 30.0  # seconds to back off after the server is deemed unreachable
_DEFAULT_TIMEOUT = 10.0  # per-request HTTP timeout; override with PANDM_SYNC_TIMEOUT


def _env_float(name: str, default: float) -> float:
    """Read a positive float from the environment, ignoring blank/garbage values."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val > 0 else default


class RemoteBackend:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        transport: Any = None,
        timeout: float | None = None,
    ):
        headers = {"x-api-key": api_key} if api_key else {}
        self._timeout = timeout if timeout is not None else _env_float("PANDM_SYNC_TIMEOUT", _DEFAULT_TIMEOUT)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=self._timeout, headers=headers, transport=transport
        )
        self._down_until = 0.0
        self._deadline: float | None = None  # set by deadline(): a wall-clock cap on a burst of calls
        self._warned = False
        self._created = False
        self._create_payload: dict[str, Any] | None = None
        self._summary: dict[str, Any] = {}  # author scalars, sent with finish (§ no separate endpoint)

    @contextlib.contextmanager
    def deadline(self, budget: float) -> Iterator[None]:
        """Bound every request issued inside the block to `budget` seconds of wall
        clock. Past the budget, requests short-circuit to failure instead of
        blocking — so finish()/resume() on the training thread can never wedge it.
        Whatever doesn't make it in time is left for the local store / `pandm sync`."""
        prev = self._deadline
        self._deadline = time.monotonic() + budget
        try:
            yield
        finally:
            self._deadline = prev

    def _budget_left(self) -> float | None:
        return None if self._deadline is None else self._deadline - time.monotonic()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
        if time.monotonic() < self._down_until:
            return None
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            left = self._budget_left()
            if left is not None:
                if left <= 0:
                    break  # budget spent — fail fast rather than block the caller
                kwargs["timeout"] = min(self._timeout, left)  # shrink the read timeout to fit
            try:
                resp = self._client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001 — network errors of all stripes
                last_exc = exc
                left = self._budget_left()
                if left is not None and left <= 0:
                    break
                backoff = 0.3 * (attempt + 1)
                time.sleep(backoff if left is None else min(backoff, left))
        if last_exc is not None:  # a tripped deadline that never reached the wire isn't an outage
            self._down_until = time.monotonic() + _COOLDOWN
            if not self._warned:
                self._warned = True
                print(
                    f"pandm: cannot reach remote server ({last_exc}); "
                    f"retrying every {int(_COOLDOWN)}s, data logged while offline is dropped",
                    file=sys.stderr,
                )
        return None

    def _ensure_created(self) -> bool:
        """Replay run creation if it failed earlier (e.g. server was down at init)."""
        if self._created:
            return True
        if self._create_payload is None:
            return False
        if self._request("POST", "/api/runs", json=self._create_payload) is not None:
            self._created = True
        return self._created

    def create_run(
        self, run_id: str, project: str, name: str, config: dict[str, Any], created_at: float | None = None
    ) -> None:
        self._create_payload = {
            "id": run_id,
            "project": project,
            "name": name,
            "config": config,
            "created_at": created_at if created_at is not None else time.time(),
        }
        self._ensure_created()

    def run_exists(self, run_id: str) -> bool:
        """Direct read — deliberately NOT routed through _request, so a legitimate
        404 ('no such run') isn't mistaken for an outage and doesn't trip retries."""
        if time.monotonic() < self._down_until:
            return False
        left = self._budget_left()
        if left is not None and left <= 0:
            return False
        timeout = self._timeout if left is None else min(self._timeout, left)
        try:
            return self._client.get(f"/api/runs/{run_id}", timeout=timeout).status_code == 200
        except Exception:  # noqa: BLE001 — unreachable -> treat as absent, caller starts fresh
            return False

    def resume_run(self, run_id: str) -> int:
        """Reopen the run server-side; returns the step to continue from (-1 if none)."""
        resp = self._request("POST", f"/api/runs/{run_id}/resume")
        return resp.json().get("max_step", -1) if resp is not None else -1

    def log_metrics(self, run_id: str, rows: list[tuple]) -> bool:
        """rows: (key, step, value, ts) or (key, step, value, ts, seq) — seq enables
        idempotent re-push from the sync cursor. Returns True if the server acked."""
        if not self._ensure_created():
            return False
        payload = []
        for row in rows:
            item = {"key": row[0], "step": row[1], "value": row[2], "ts": row[3]}
            if len(row) > 4:
                item["seq"] = row[4]
            payload.append(item)
        return self._request("POST", f"/api/runs/{run_id}/metrics", json={"rows": payload}) is not None

    def log_media(
        self,
        run_id: str,
        key: str,
        step: int,
        data: bytes,
        ext: str,
        caption: str | None,
        ts: float,
        media_seq: int | None = None,
    ) -> bool:
        if not self._ensure_created():
            return False
        form = {"key": key, "step": str(step), "caption": caption or "", "ts": str(ts)}
        if media_seq is not None:
            form["media_seq"] = str(media_seq)
        resp = self._request(
            "POST",
            f"/api/runs/{run_id}/media",
            files={"file": (f"upload{ext}", data)},
            data=form,
        )
        return resp is not None

    def heartbeat(self, run_id: str, ts: float) -> bool:
        if not self._ensure_created():
            return False
        return self._request("POST", f"/api/runs/{run_id}/heartbeat") is not None

    def update_progress(self, run_id: str, current: float, total: float | None, ts: float) -> bool:
        if not self._ensure_created():
            return False
        payload: dict[str, Any] = {"current": current, "ts": ts}
        if total is not None:
            payload["total"] = total
        return self._request("POST", f"/api/runs/{run_id}/progress", json=payload) is not None

    def set_summary(self, run_id: str, values: dict[str, Any]) -> bool:
        """Stash author scalars in memory; they ride along with finish_run so the
        run-level summary lands as part of the run's terminal state, no extra endpoint."""
        self._summary.update(values)
        return True

    def finish_run(
        self, run_id: str, status: str, finished_at: float, summary: dict[str, Any] | None = None
    ) -> bool:
        if not self._ensure_created():
            return False
        # remote-only stashes via set_summary; dual/sync pass the local row explicitly
        summary = summary if summary is not None else self._summary
        body: dict[str, Any] = {"status": status, "finished_at": finished_at}
        if summary:
            body["summary"] = summary
        resp = self._request("POST", f"/api/runs/{run_id}/finish", json=body)
        return resp is not None
