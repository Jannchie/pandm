-- data_rev: bumped only when a run's series/media data changes (ingest, media,
-- finish) — never by heartbeat/progress. Keys the edge cache for run-scoped
-- reads, so an active-but-idle run keeps serving charts from cache instead of
-- invalidating every poll cycle.
ALTER TABLE runs ADD COLUMN data_rev REAL NOT NULL DEFAULT 0;
UPDATE runs SET data_rev = updated_at;
