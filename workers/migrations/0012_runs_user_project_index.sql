-- The dashboard polls /api/runs?project=… every few seconds; with only the
-- user_id index every poll read all of a user's runs and filtered in SQLite,
-- which is what blew through D1's daily row-read budget.
CREATE INDEX IF NOT EXISTS idx_runs_user_project ON runs (user_id, project);
