-- One-line human note on a run (run.init(description=...)). Author-written, set at
-- creation, shown as a subtitle in the dashboard so a reader knows what the run is.
ALTER TABLE runs ADD COLUMN description TEXT NOT NULL DEFAULT '';
