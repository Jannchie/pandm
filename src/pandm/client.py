"""HTTP backend for cloud mode — mirrors LocalStore's write API.

Deliberately forgiving: if the server is unreachable the SDK warns and drops
data instead of killing the training process, then quietly retries after a
cooldown. The run-creation request is replayed on recovery so a run that
started during an outage still shows up once the server is back.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import httpx

# httpx logs every request at INFO ("HTTP Request: POST …/metrics 200 OK") —
# pure noise inside a training loop that pushes metrics every couple of seconds
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_RETRIES = 3
_COOLDOWN = 30.0  # seconds to back off after the server is deemed unreachable


class RemoteBackend:
    def __init__(self, base_url: str, api_key: str | None = None, transport: Any = None):
        headers = {"x-api-key": api_key} if api_key else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=10, headers=headers, transport=transport
        )
        self._down_until = 0.0
        self._warned = False
        self._created = False
        self._create_payload: dict[str, Any] | None = None

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
        if time.monotonic() < self._down_until:
            return None
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                resp = self._client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001 — network errors of all stripes
                last_exc = exc
                time.sleep(0.3 * (attempt + 1))
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

    def finish_run(self, run_id: str, status: str, finished_at: float) -> bool:
        if not self._ensure_created():
            return False
        resp = self._request(
            "POST",
            f"/api/runs/{run_id}/finish",
            json={"status": status, "finished_at": finished_at},
        )
        return resp is not None
