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
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    project        TEXT NOT NULL DEFAULT 'default',
    name           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running',
    config         TEXT NOT NULL DEFAULT '{}',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    finished_at    REAL,
    user_id        INTEGER,
    -- training progress for ETA: current step out of total (either may be NULL)
    progress       REAL,
    progress_total REAL,
    progress_ts    REAL
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
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id  INTEGER NOT NULL UNIQUE,
    login      TEXT NOT NULL,
    name       TEXT,
    avatar_url TEXT,
    api_key    TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
-- client side: per-run upload cursor + advisory lease; a row here marks the
-- run as cloud-tracked (pandm sync ignores runs without one unless told to)
CREATE TABLE IF NOT EXISTS sync_state (
    run_id        TEXT PRIMARY KEY,
    metrics_rowid INTEGER NOT NULL DEFAULT 0,
    media_id      INTEGER NOT NULL DEFAULT 0,
    status_synced INTEGER NOT NULL DEFAULT 0,
    lease_owner   TEXT,
    lease_expires REAL
);
-- server side: highest client-local seq durably ingested per run, so
-- at-least-once re-pushes from the sync cursor never duplicate rows
CREATE TABLE IF NOT EXISTS sync_progress (
    run_id             TEXT PRIMARY KEY,
    last_metrics_rowid INTEGER NOT NULL DEFAULT 0,
    last_media_id      INTEGER NOT NULL DEFAULT 0
);
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
        "user_id": row["user_id"],
        "progress": row["progress"],
        "progress_total": row["progress_total"],
        "progress_ts": row["progress_ts"],
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
            self._migrate()
            self._db.commit()

    def _migrate(self) -> None:
        """Idempotent migrations for databases created by older versions."""
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(runs)").fetchall()}
        if "user_id" not in cols:
            self._db.execute("ALTER TABLE runs ADD COLUMN user_id INTEGER")
        for col in ("progress", "progress_total", "progress_ts"):
            if col not in cols:
                self._db.execute(f"ALTER TABLE runs ADD COLUMN {col} REAL")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_id)")

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
        user_id: int | None = None,
    ) -> None:
        now = created_at if created_at is not None else time.time()
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO runs (id, project, name, status, config, created_at, updated_at, user_id)"
                " VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
                (run_id, project, name, json.dumps(config, default=str), now, now, user_id),
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

    def update_progress(
        self, run_id: str, current: float, total: float | None = None, ts: float | None = None
    ) -> None:
        """Record training progress (also doubles as a heartbeat). `total=None`
        keeps whatever total was set before — the loop need only resend it on change."""
        ts = ts if ts is not None else time.time()
        with self._lock:
            if total is None:
                self._db.execute(
                    "UPDATE runs SET progress = ?, progress_ts = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'running'",
                    (current, ts, ts, run_id),
                )
            else:
                self._db.execute(
                    "UPDATE runs SET progress = ?, progress_total = ?, progress_ts = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'running'",
                    (current, total, ts, ts, run_id),
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

    def list_projects(self, user_id: int | None = None) -> list[dict[str, Any]]:
        where = "WHERE user_id = ?" if user_id is not None else ""
        params = (user_id,) if user_id is not None else ()
        with self._lock:
            rows = self._db.execute(
                f"SELECT project, COUNT(*) AS runs, MAX(updated_at) AS last_active"
                f" FROM runs {where} GROUP BY project ORDER BY last_active DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def list_runs(self, project: str | None = None, user_id: int | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM runs {where} ORDER BY created_at DESC", params
            ).fetchall()
        runs = [_run_row_to_dict(r) for r in rows]
        summaries = self._summaries([r["id"] for r in runs])
        for run in runs:
            run["summary"] = summaries.get(run["id"], {})
        return runs

    def get_run(self, run_id: str, user_id: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        run = _run_row_to_dict(row)
        if user_id is not None and run["user_id"] != user_id:
            return None  # not yours — indistinguishable from absent
        run["summary"] = self._summaries([run_id]).get(run_id, {})
        return run

    def run_owner(self, run_id: str) -> int | None:
        """user_id of the run's owner, or None (unowned local run / missing run)."""
        with self._lock:
            row = self._db.execute("SELECT user_id FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row["user_id"] if row else None

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

    def metric_series(
        self, run_id: str, key: str, max_points: int = 1500, after_step: int | None = None
    ) -> dict[str, list]:
        """Series for one (run, key), stride-downsampled to ~max_points (always keeps the last point).

        `after_step` switches to an incremental tail read (steps strictly above it,
        no sampling) so live charts can append instead of re-reading the history.
        """
        if after_step is not None:
            with self._lock:
                rows = self._db.execute(
                    "SELECT step, value, ts FROM metrics"
                    " WHERE run_id = ? AND key = ? AND step > ? ORDER BY step, rowid",
                    (run_id, key, after_step),
                ).fetchall()
            return {
                "steps": [r["step"] for r in rows],
                "values": [r["value"] for r in rows],
                "ts": [r["ts"] for r in rows],
            }
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

    # -------------------------------------------------------------- users

    def upsert_user(self, github_id: int, login: str, name: str | None, avatar_url: str | None) -> dict[str, Any]:
        """Create-or-refresh a user from a GitHub profile; api_key is minted once on create."""
        with self._lock:
            self._db.execute(
                "INSERT INTO users (github_id, login, name, avatar_url, api_key, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(github_id) DO UPDATE SET"
                "   login = excluded.login, name = excluded.name, avatar_url = excluded.avatar_url",
                (github_id, login, name, avatar_url, secrets.token_urlsafe(32), time.time()),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM users WHERE github_id = ?", (github_id,)).fetchone()
        return dict(row)

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_api_key(self, api_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()
        return dict(row) if row else None

    def rotate_api_key(self, user_id: int) -> str:
        key = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute("UPDATE users SET api_key = ? WHERE id = ?", (key, user_id))
            self._db.commit()
        return key

    # ----------------------------------------- ingest watermark (server side)
    # Synced batches carry the client-local rowid per row ("seq"); rows at or
    # below the stored watermark are replays of an already-committed push.

    def log_metrics_seq(self, run_id: str, rows: list[tuple[str, int, float, float, int]]) -> int:
        """rows: [(key, step, value, ts, seq), ...] — returns how many were fresh."""
        if not rows:
            return 0
        with self._lock:
            row = self._db.execute(
                "SELECT last_metrics_rowid FROM sync_progress WHERE run_id = ?", (run_id,)
            ).fetchone()
            last = row["last_metrics_rowid"] if row else 0
            fresh = [r for r in rows if r[4] > last]
            if fresh:
                self._db.executemany(
                    "INSERT INTO metrics (run_id, key, step, value, ts) VALUES (?, ?, ?, ?, ?)",
                    [(run_id, k, s, v, t) for k, s, v, t, _ in fresh],
                )
                self._db.execute(
                    "INSERT INTO sync_progress (run_id, last_metrics_rowid) VALUES (?, ?)"
                    " ON CONFLICT(run_id) DO UPDATE SET last_metrics_rowid = excluded.last_metrics_rowid",
                    (run_id, max(r[4] for r in fresh)),
                )
                self._db.execute(
                    "UPDATE runs SET updated_at = ? WHERE id = ?",
                    (max(r[3] for r in fresh), run_id),
                )
            self._db.commit()
        return len(fresh)

    def claim_media_seq(self, run_id: str, media_id: int) -> bool:
        """True if this client-local media id is fresh (advances the watermark)."""
        with self._lock:
            row = self._db.execute(
                "SELECT last_media_id FROM sync_progress WHERE run_id = ?", (run_id,)
            ).fetchone()
            last = row["last_media_id"] if row else 0
            if media_id <= last:
                return False
            self._db.execute(
                "INSERT INTO sync_progress (run_id, last_media_id) VALUES (?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET last_media_id = excluded.last_media_id",
                (run_id, media_id),
            )
            self._db.commit()
        return True

    # ------------------------------------------- sync cursor (client side)

    def ensure_sync_state(self, run_id: str) -> None:
        """Mark a run as cloud-tracked (no-op if already tracked)."""
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO sync_state (run_id) VALUES (?)", (run_id,))
            self._db.commit()

    def get_sync_state(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sync_state WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def advance_sync_cursor(
        self, run_id: str, metrics_rowid: int | None = None, media_id: int | None = None, status_synced: bool | None = None
    ) -> None:
        sets, params = [], []
        if metrics_rowid is not None:
            sets.append("metrics_rowid = ?")
            params.append(metrics_rowid)
        if media_id is not None:
            sets.append("media_id = ?")
            params.append(media_id)
        if status_synced is not None:
            sets.append("status_synced = ?")
            params.append(int(status_synced))
        if not sets:
            return
        with self._lock:
            self._db.execute(f"UPDATE sync_state SET {', '.join(sets)} WHERE run_id = ?", (*params, run_id))
            self._db.commit()

    def claim_sync_lease(self, run_id: str, owner: str, ttl: float = 60.0) -> bool:
        """Advisory per-run lock so a live uploader and `pandm sync` don't race."""
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "UPDATE sync_state SET lease_owner = ?, lease_expires = ?"
                " WHERE run_id = ? AND (lease_owner IS NULL OR lease_owner = ? OR lease_expires < ?)",
                (owner, now + ttl, run_id, owner, now),
            )
            self._db.commit()
        return cur.rowcount == 1

    def release_sync_lease(self, run_id: str, owner: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sync_state SET lease_owner = NULL, lease_expires = NULL"
                " WHERE run_id = ? AND lease_owner = ?",
                (run_id, owner),
            )
            self._db.commit()

    def unsynced_metrics(self, run_id: str, after_rowid: int, limit: int = 1000) -> list[dict[str, Any]]:
        """Committed metric rows past the cursor, oldest first, with their local rowid as seq."""
        with self._lock:
            rows = self._db.execute(
                "SELECT rowid AS seq, key, step, value, ts FROM metrics"
                " WHERE run_id = ? AND rowid > ? ORDER BY rowid LIMIT ?",
                (run_id, after_rowid, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def unsynced_media(self, run_id: str, after_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, key, step, filename, caption, ts FROM media"
                " WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                (run_id, after_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def runs_needing_sync(self) -> list[str]:
        """Cloud-tracked runs with unsynced rows, media, or final status."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT s.run_id FROM sync_state s JOIN runs r ON r.id = s.run_id
                WHERE s.metrics_rowid < COALESCE((SELECT MAX(rowid) FROM metrics m WHERE m.run_id = s.run_id), 0)
                   OR s.media_id < COALESCE((SELECT MAX(id) FROM media md WHERE md.run_id = s.run_id), 0)
                   OR (r.status != 'running' AND s.status_synced = 0)
                ORDER BY r.created_at
                """
            ).fetchall()
        return [r["run_id"] for r in rows]
