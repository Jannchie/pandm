-- Free-form run organization (run.init(tags=..., group=...)). tags is a JSON array
-- of labels used to filter; group_name buckets related runs (a sweep, a
-- multi-process job). `group` is a SQL keyword, hence the column name.
ALTER TABLE runs ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';
ALTER TABLE runs ADD COLUMN group_name TEXT;
