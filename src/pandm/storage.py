"""Local storage backend: SQLite (WAL) for runs/metrics + plain files for media.

The SDK writes to it directly in local mode; the server reads (and, in cloud
mode, writes) through the same class. WAL mode lets the training process and
the dashboard process share the database safely.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    config      TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT NOT NULL,
    key    TEXT NOT NULL,
    step   INTEGER NOT NULL,
    value  REAL NOT NULL,
    ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_run_key_step ON metrics (run_id, key, step);
CREATE TABLE IF NOT EXISTS media (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL,
    key      TEXT NOT NULL,
    step     INTEGER NOT NULL,
    filename TEXT NOT NULL,
    caption  TEXT,
    ts       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_run_key_step ON media (run_id, key, step);
"""

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")

# a 'running' run whose heartbeat stopped this long ago is presumed crashed
# (the SDK beats every ~15s; computed on read, so it self-heals if the process resumes)
STALE_AFTER = 60.0


def resolve_dir(directory: str | os.PathLike | None = None) -> Path:
    """Data dir resolution: explicit arg > $PANDM_DIR > ./.pandm"""
    if directory:
        return Path(directory)
    env = os.environ.get("PANDM_DIR")
    if env:
        return Path(env)
    return Path.cwd() / ".pandm"


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text).strip("-") or "x"


def _run_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    run = {
        "id": row["id"],
        "project": row["project"],
        "name": row["name"],
        "status": row["status"],
        "config": json.loads(row["config"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }
    if run["status"] == "running" and time.time() - run["updated_at"] > STALE_AFTER:
        run["status"] = "crashed"
    return run


class LocalStore:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.media_root = self.root / "media"
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.root / "pandm.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------- writes

    def create_run(
        self,
        run_id: str,
        project: str,
        name: str,
        config: dict[str, Any],
        created_at: float | None = None,
    ) -> None:
        now = created_at if created_at is not None else time.time()
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO runs (id, project, name, status, config, created_at, updated_at)"
                " VALUES (?, ?, ?, 'running', ?, ?, ?)",
                (run_id, project, name, json.dumps(config, default=str), now, now),
            )
            self._db.commit()

    def log_metrics(self, run_id: str, rows: list[tuple[str, int, float, float]]) -> None:
        """rows: [(key, step, value, ts), ...]"""
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO metrics (run_id, key, step, value, ts) VALUES (?, ?, ?, ?, ?)",
                [(run_id, k, s, v, t) for k, s, v, t in rows],
            )
            self._db.execute(
                "UPDATE runs SET updated_at = ? WHERE id = ?",
                (max(t for *_, t in rows), run_id),
            )
            self._db.commit()

    def log_media(
        self,
        run_id: str,
        key: str,
        step: int,
        data: bytes,
        ext: str = ".png",
        caption: str | None = None,
        ts: float | None = None,
    ) -> str:
        ts = ts if ts is not None else time.time()
        run_dir = self.media_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{_slug(key)}_{step:08d}_{uuid.uuid4().hex[:6]}{ext}"
        (run_dir / filename).write_bytes(data)
        with self._lock:
            self._db.execute(
                "INSERT INTO media (run_id, key, step, filename, caption, ts) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, key, step, filename, caption, ts),
            )
            self._db.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (ts, run_id))
            self._db.commit()
        return filename

    def heartbeat(self, run_id: str, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        with self._lock:
            self._db.execute(
                "UPDATE runs SET updated_at = ? WHERE id = ? AND status = 'running'", (ts, run_id)
            )
            self._db.commit()

    def finish_run(self, run_id: str, status: str = "finished", finished_at: float | None = None) -> None:
        now = finished_at if finished_at is not None else time.time()
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                (status, now, now, run_id),
            )
            self._db.commit()

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM metrics WHERE run_id = ?", (run_id,))
            self._db.execute("DELETE FROM media WHERE run_id = ?", (run_id,))
            self._db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            self._db.commit()
        shutil.rmtree(self.media_root / run_id, ignore_errors=True)

    # -------------------------------------------------------------- reads

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT project, COUNT(*) AS runs, MAX(updated_at) AS last_active"
                " FROM runs GROUP BY project ORDER BY last_active DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_runs(self, project: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if project:
                rows = self._db.execute(
                    "SELECT * FROM runs WHERE project = ? ORDER BY created_at DESC", (project,)
                ).fetchall()
            else:
                rows = self._db.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        runs = [_run_row_to_dict(r) for r in rows]
        summaries = self._summaries([r["id"] for r in runs])
        for run in runs:
            run["summary"] = summaries.get(run["id"], {})
        return runs

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        run = _run_row_to_dict(row)
        run["summary"] = self._summaries([run_id]).get(run_id, {})
        return run

    def _summaries(self, run_ids: list[str]) -> dict[str, dict[str, float]]:
        """Latest logged value per (run, key)."""
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        with self._lock:
            rows = self._db.execute(
                f"""
                SELECT m.run_id, m.key, m.value FROM metrics m
                JOIN (
                    SELECT run_id, key, MAX(rowid) AS mr FROM metrics
                    WHERE run_id IN ({placeholders}) GROUP BY run_id, key
                ) t ON m.rowid = t.mr
                """,
                run_ids,
            ).fetchall()
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            out.setdefault(r["run_id"], {})[r["key"]] = r["value"]
        return out

    def metric_keys(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key, COUNT(*) AS points, MAX(step) AS last_step"
                " FROM metrics WHERE run_id = ? GROUP BY key ORDER BY key",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def metric_series(self, run_id: str, key: str, max_points: int = 1500) -> dict[str, list]:
        """Series for one (run, key), stride-downsampled to ~max_points (always keeps the last point)."""
        with self._lock:
            total = self._db.execute(
                "SELECT COUNT(*) FROM metrics WHERE run_id = ? AND key = ?", (run_id, key)
            ).fetchone()[0]
            if total == 0:
                return {"steps": [], "values": [], "ts": []}
            stride = max(1, math.ceil(total / max_points))
            rows = self._db.execute(
                """
                SELECT step, value, ts FROM (
                    SELECT step, value, ts, ROW_NUMBER() OVER (ORDER BY step, rowid) AS rn
                    FROM metrics WHERE run_id = ? AND key = ?
                ) WHERE (rn - 1) % ? = 0 OR rn = ?
                """,
                (run_id, key, stride, total),
            ).fetchall()
        return {
            "steps": [r["step"] for r in rows],
            "values": [r["value"] for r in rows],
            "ts": [r["ts"] for r in rows],
        }

    def list_media(self, run_id: str, key: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if key:
                rows = self._db.execute(
                    "SELECT key, step, filename, caption, ts FROM media"
                    " WHERE run_id = ? AND key = ? ORDER BY key, step, id",
                    (run_id, key),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT key, step, filename, caption, ts FROM media"
                    " WHERE run_id = ? ORDER BY key, step, id",
                    (run_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def media_path(self, run_id: str, filename: str) -> Path | None:
        path = (self.media_root / run_id / filename).resolve()
        if not str(path).startswith(str(self.media_root.resolve())):
            return None  # path traversal guard
        return path if path.is_file() else None
