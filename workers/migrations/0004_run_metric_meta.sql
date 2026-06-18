-- Per-metric display specs (run.define_metric(...)): {key: {min,max,unit,goal,
-- baseline}}. Author-declared, set once, attached when the run finishes. Tells the
-- dashboard how to render a metric (fixed axis, percent, baseline line, goal).
ALTER TABLE runs ADD COLUMN metric_meta TEXT NOT NULL DEFAULT '{}';
