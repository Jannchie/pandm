-- Distributions over time (run.log_histogram): one row per (key, step) holding
-- pre-binned edges + counts (JSON), O(bins) regardless of sample count. The
-- dashboard draws the series as a step×bin density heatmap. Mirrors the Python
-- store's histograms table; keeps its implicit rowid for the sync watermark.
CREATE TABLE IF NOT EXISTS histograms (
    run_id TEXT NOT NULL,
    key    TEXT NOT NULL,
    step   INTEGER NOT NULL,
    bins   TEXT NOT NULL,   -- JSON: n+1 bin edges
    counts TEXT NOT NULL,   -- JSON: n per-bin counts
    ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_histograms_run_key_step ON histograms (run_id, key, step);

-- highest client-local histogram seq durably committed per run (ingest dedup)
ALTER TABLE sync_progress ADD COLUMN last_histograms_rowid INTEGER NOT NULL DEFAULT 0;
