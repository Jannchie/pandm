-- Materialized per-key stats {key: {min,max,count,last}}, maintained by the
-- RunStore Durable Object and written through to this row on every ingest batch.
-- NULL marks a pre-DO ("legacy") run whose series still live in the D1 metrics/
-- histograms tables: those reads fall back to the aggregate-scan path. A new run
-- is created with stats = '{}', so a non-NULL stats column means "served by a DO".
ALTER TABLE runs ADD COLUMN stats TEXT;
