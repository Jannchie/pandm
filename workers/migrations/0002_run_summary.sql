-- Materialized last-value-per-key summary, maintained by the ingest path.
-- NULL marks pre-migration rows: reads fall back to the aggregate query and
-- backfill the column once; ingest leaves NULL alone so a partial patch can
-- never shadow history.
ALTER TABLE runs ADD COLUMN summary TEXT;
