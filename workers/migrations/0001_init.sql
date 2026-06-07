-- pandm cloud schema: mirrors src/pandm/storage.py minus client-only tables.
-- metrics keeps its implicit rowid; the sync watermark references the
-- CLIENT's local rowid (sent as `seq`), not anything server-side.

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id  INTEGER NOT NULL UNIQUE,
    login      TEXT NOT NULL,
    name       TEXT,
    avatar_url TEXT,
    api_key    TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    config      TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    finished_at REAL,
    user_id     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_id);

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

-- ingest dedup watermark: highest client-local seq durably committed per run
CREATE TABLE IF NOT EXISTS sync_progress (
    run_id             TEXT PRIMARY KEY,
    last_metrics_rowid INTEGER NOT NULL DEFAULT 0,
    last_media_id      INTEGER NOT NULL DEFAULT 0
);

-- `pandm login` device flow (Workers have no shared memory across isolates)
CREATE TABLE IF NOT EXISTS cli_auth (
    user_code    TEXT PRIMARY KEY,
    device_token TEXT NOT NULL UNIQUE,
    api_key      TEXT,
    created_at   REAL NOT NULL
);
