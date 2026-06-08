-- Training progress for the dashboard ETA: current step out of total.
-- Either may stay NULL (no total declared -> progress shown without an ETA).
ALTER TABLE runs ADD COLUMN progress REAL;
ALTER TABLE runs ADD COLUMN progress_total REAL;
ALTER TABLE runs ADD COLUMN progress_ts REAL;
