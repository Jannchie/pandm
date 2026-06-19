---
name: pandm-inspect
description: Query and analyze machine-learning experiments tracked by pandm — list runs, read a run's config/summary/metrics, compare runs to find the best hyperparameters, read full metric series, and locate logged images. Use when the user asks about past pandm experiments, wants to compare runs, pick a winner, inspect a metric over time, or analyze results stored in a `.pandm/` directory.
---

# Inspecting pandm experiments

pandm stores every run as plain SQLite + PNG under a `.pandm/` directory
(default `./.pandm`, overridable with `--dir` or `$PANDM_DIR`):

```
.pandm/
  pandm.db          # runs, metrics, media metadata (SQLite, WAL mode)
  media/<run_id>/   # the actual PNG files
```

**Use the pandm CLI — it is the query interface.** Every command below takes
`--json` for clean, parseable output (no Rich color codes), so there's no need
to open `pandm.db` with `sqlite3` for ordinary questions. Drop to raw SQL only
for an aggregation the CLI genuinely can't express.

## Finding the CLI

pandm may be on PATH, or only inside a project venv / uv environment. Try these
in order and use the first that prints a version — then prefix every command
below with that launcher:

```sh
pandm version                 # installed on PATH
python -m pandm version       # importable in the active interpreter
uv run pandm version          # project managed by uv (run from the repo)
.venv/bin/pandm version       # project venv (also: ./venv/bin/pandm)
uvx pandm version             # last resort: fetch into an ephemeral env
```

If none resolve, the data is still just files — fall back to raw SQL (bottom).

## Querying runs

```sh
pandm ls --json                                  # all runs, newest first
pandm ls --project mnist --status finished --json
pandm ls --sort-by val/acc --limit 5 --json      # 5 best by val/acc (max), best first
pandm ls --sort-by loss:min --asc --json         # smallest loss first
```

`ls --json` carries each run's `config`, author-written `summary`, and per-metric
`stats` (`{min, max, last, count}` — `.last` is the latest value, `.max`/`.min`
the best). `--sort-by KEY[:min|max|last]` orders by that aggregate (default
`max`, descending; add `--asc` to flip), so **"which run wins" is a single CLI
call** — no series scan, no SQL `MAX()`/`GROUP BY`.

## One run, in depth

```sh
pandm show <run_id> --json     # + metric_keys and media with absolute file paths
pandm export <run_id> --json   # full metric series, all keys
pandm export <run_id> -k train/loss -k val/loss > loss.csv   # CSV (key,step,value,ts)
```

`show --json` returns absolute `path`s for every logged image — open/Read those
directly. `export` is the only way to get full per-step series.

## Comparing runs

```sh
pandm compare <id1> <id2> <id3>          # side-by-side table (config, summary, last metric)
pandm compare <id1> <id2> --json         # config/summary/stats, one value per run, run order preserved
```

In `compare --json`, each `config[k]` / `summary[k]` / `stats[k]` is a list
aligned to the `runs` array — `stats[k][i]` is `{min,max,last,count}` for run `i`.

## Semantics to keep in mind

- **Per-key extrema come from *different* steps.** In `stats[key]`, `max`/`min` are
  each metric's best over the whole run, so `max(spearman)` and `min(mae)` need not be
  the same checkpoint — don't read them as one model's row.
- **`summary[key]`** is an *author-written* run-level scalar (`run.summary({...})`),
  typically the chosen checkpoint's self-consistent metric row — empty unless the
  training code wrote it. Prefer it over stitching per-key extrema when present.
- **`status`** is computed on read: a `running` run whose heartbeat has been
  quiet for >60 s is reported as `crashed` (self-heals if the process resumes).
  So a run can flip to `crashed` between two reads — don't cache it.
- **`progress` / `progress_total`** drive the dashboard ETA; either may be null.

## Raw SQL — last resort only

For an aggregation the CLI can't express (e.g. a join across the `metrics`
table). The DB is WAL mode, so reading a live run is safe.

```sql
-- best val/acc per run, across the whole metrics table
SELECT run_id, MAX(value) AS best FROM metrics
WHERE key = 'val/acc' GROUP BY run_id ORDER BY best DESC;
```

Schema: `runs(id, project, name, status, config /*JSON*/, summary /*JSON*/,
created_at, updated_at, finished_at, progress, progress_total)`,
`metrics(run_id, key, step, value, ts)`,
`media(run_id, key, step, filename, caption, ts)` — file at `media/<run_id>/<filename>`.
