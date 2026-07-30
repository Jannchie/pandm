-- Resume-aware training time. A run is a sequence of launch *segments*;
-- active_seconds sums the wall-clock span of every segment before the current
-- one, and segment_started_at marks where the current segment began (= created_at
-- on create, reset to the resume time on each reopen). Total training time =
-- active_seconds + (COALESCE(finished_at, updated_at) - COALESCE(segment_started_at, created_at)),
-- so the idle gap between a finish/crash and the next resume is never counted.
-- Legacy rows keep active_seconds = 0 and a NULL segment_started_at, which
-- collapses the formula back to (COALESCE(finished_at, updated_at) - created_at).
ALTER TABLE runs ADD COLUMN active_seconds REAL NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN segment_started_at REAL;
