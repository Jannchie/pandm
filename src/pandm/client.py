"""HTTP backend for cloud mode — mirrors LocalStore's write API.

Deliberately forgiving: if the server is unreachable the SDK warns and drops
data instead of killing the training process, then quietly retries after a
cooldown. The run-creation request is replayed on recovery so a run that
started during an outage still shows up once the server is back.
"""

from __future__ import annotations

import sys
import time
from typing import Any

import httpx

_RETRIES = 3
_COOLDOWN = 30.0  # seconds to back off after the server is deemed unreachable


class RemoteBackend:
    def __init__(self, base_url: str, api_key: str | None = None):
        headers = {"x-api-key": api_key} if api_key else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=10, headers=headers)
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

    def create_run(self, run_id: str, project: str, name: str, config: dict[str, Any]) -> None:
        self._create_payload = {
            "id": run_id,
            "project": project,
            "name": name,
            "config": config,
            "created_at": time.time(),
        }
        self._ensure_created()

    def log_metrics(self, run_id: str, rows: list[tuple[str, int, float, float]]) -> None:
        if not self._ensure_created():
            return
        self._request(
            "POST",
            f"/api/runs/{run_id}/metrics",
            json={"rows": [{"key": k, "step": s, "value": v, "ts": t} for k, s, v, t in rows]},
        )

    def log_media(
        self,
        run_id: str,
        key: str,
        step: int,
        data: bytes,
        ext: str,
        caption: str | None,
        ts: float,
    ) -> None:
        if not self._ensure_created():
            return
        self._request(
            "POST",
            f"/api/runs/{run_id}/media",
            files={"file": (f"upload{ext}", data)},
            data={"key": key, "step": str(step), "caption": caption or "", "ts": str(ts)},
        )

    def heartbeat(self, run_id: str, ts: float) -> None:
        if not self._ensure_created():
            return
        self._request("POST", f"/api/runs/{run_id}/heartbeat")

    def finish_run(self, run_id: str, status: str, finished_at: float) -> None:
        if not self._ensure_created():
            return
        self._request(
            "POST",
            f"/api/runs/{run_id}/finish",
            json={"status": status, "finished_at": finished_at},
        )
