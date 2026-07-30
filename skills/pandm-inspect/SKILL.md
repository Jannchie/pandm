---
name: pandm-inspect
description: Query and analyze machine-learning experiments tracked by pandm — list runs, read a run's config/summary/metrics, compare runs to find the best hyperparameters, read the display specs the training code declared (which metric is the experiment's judge, which invariants must hold), read full metric series, and locate logged images. Reads a local `.pandm/` SQLite + PNG store directly; fully offline, no network, account, or server required. Use when the user asks about past pandm experiments, wants to compare runs, pick a winner, check whether a run broke an invariant, inspect a metric over time, or analyze results stored in a `.pandm/` directory.
---

# Inspecting pandm experiments

pandm stores every run as plain SQLite + PNG under a `.pandm/` directory
(default `./.pandm`, overridable with `--dir` or `$PANDM_DIR`). **Everything you
need is in that local directory** — inspecting runs never touches the network,
needs no account, and works fully offline (even for runs that were also mirrored
to a shared server):

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
pandm projects --json                            # projects, run counts, last active
pandm ls --json                                  # all runs, newest first
pandm ls --project mnist --status finished --json
pandm ls --tag baseline --tag lr-sweep --json    # runs carrying every listed tag
pandm ls --sort-by val/acc --limit 5 --json      # 5 best by val/acc (max), best first
pandm ls --sort-by loss:min --asc --json         # smallest loss first
```

`ls --json` carries each run's `config`, author-written `summary`, per-metric
`stats` (`{min, max, last, count}` — `.last` is the latest value, `.max`/`.min`
the best), and `metric_meta` (what the training code declared about each key —
see below). `--sort-by KEY[:min|max|last]` orders by that aggregate (default
`max`, descending; add `--asc` to flip), so **"which run wins" is a single CLI
call** — no series scan, no SQL `MAX()`/`GROUP BY`.

## One run, in depth

```sh
pandm show <run_id> --json     # + metric_keys and media with absolute file paths
pandm export <run_id> --json   # full metric series, all keys
pandm export <run_id> -k train/loss -k val/loss > loss.csv   # CSV (key,step,value,ts)
pandm export <run_id> --histograms --json                    # logged distributions
```

`show --json` returns absolute `path`s for every logged image — open/Read those
directly. `export` is the only way to get full per-step series; `--histograms`
switches it to the binned distributions (`{steps, bins, counts}` per key in JSON,
one row per bin in CSV: `key,step,bin_lo,bin_hi,count,ts`).

## Which metrics decide — read `metric_meta` first

A run that logs 30 keys usually has 3 that decide anything, and the training code
already said which ones. `metric_meta` is that declaration: a per-key spec, keyed
exactly like `stats`, riding along on every `ls --json` / `show --json` row.

```json
"metric_meta": {
  "vs_baseline/avg_rank":      {"importance": "primary", "goal": "min", "description": "对基线的平均顺位"},
  "game/budget_exceeded_rate": {"alarm": {"ok": 0}, "unit": "percent"},
  "pool/0/rank":               {"kind": "table", "row": "anchor", "series": "平均顺位"},
  "dropped_rows":              {"importance": "debug"}
}
```

Read the fields below. The rest (`kind`, `min`/`max`, `axis`, `scale`, `band`,
`x_label`, ticks) only says how the dashboard draws the key — nothing an analysis needs.

- **`importance`** — `"primary"` marks the keys the experiment is judged on, `"debug"`
  the plumbing (sample counts, dropped rows). Lead with the primary keys; don't blend
  30 metrics into a verdict, and don't headline a debug counter because it moved most.
- **`goal`** — which direction is better. `--sort-by` defaults to `max`, so a
  `goal="min"` metric needs `--sort-by KEY:min --asc` to put the best run first.
- **`alarm`** — an invariant the author says must hold. Check it before calling any
  run a winner (below).
- **`description`** — the author's own one-line account of the metric, in the language
  they wrote it in. Quote it; don't re-infer the meaning from the key name.
- **`unit: "percent"`** — the stored value is a `0..1` proportion; report `73%`, not `0.73`.
- **`row` / `series`** — human labels for keys whose names are indices. `pool/0/rank`
  with `row="anchor"` is "average rank against the *anchor* opponent"; `pool/0` on its
  own tells the reader nothing. Keys sharing a `panel` are one figure's worth of data,
  not N independent findings.

**Alarms gate the verdict.** They come from `stats[key]`, which already spans the whole
run — no series scan:

| declared | violated when |
|---|---|
| `{"ok": v}` | `stats.min != v` or `stats.max != v` |
| `{"max": v}` | `stats.max > v` |
| `{"min": v}` | `stats.min < v` |

A tripped alarm usually invalidates the headline number rather than merely denting it:
`budget_exceeded_rate > 0` means some games were silently truncated, so the win rate is
a *biased* sample, not a slightly noisier one. Report it as a caveat on the result — not
as one more metric that happens to look bad.

`metric_meta` is optional and often `{}` — undeclared keys, and runs recorded before the
author declared anything, carry nothing. Fall back to treating keys equally: absence
means "not said", never "not important". A key is never renamed for display reasons, so
`metric_meta` keys always join `stats` / `summary` / `metrics.key` by exact name.

## Comparing runs

```sh
pandm compare <id1> <id2> <id3>          # side-by-side table (config, summary, last metric)
pandm compare <id1> <id2> --json         # config/summary/stats, one value per run, run order preserved
```

In `compare --json`, each `config[k]` / `summary[k]` / `stats[k]` is a list
aligned to the `runs` array — `stats[k][i]` is `{min,max,last,count}` for run `i`.
It carries no `metric_meta`; take the specs from `ls --json` (which returns every
run's, in one call) and use them to pick which rows of the comparison matter.

## Semantics to keep in mind

- **Per-key extrema come from *different* steps.** In `stats[key]`, `max`/`min` are
  each metric's best over the whole run, so `max(spearman)` and `min(mae)` need not be
  the same checkpoint — don't read them as one model's row.
- **`summary[key]`** is an *author-written* run-level scalar (`run.summary({...})`),
  typically the chosen checkpoint's self-consistent metric row — empty unless the
  training code wrote it. Prefer it over stitching per-key extrema when present.
- **`status`** is computed on read: a `running` run whose heartbeat has been
  quiet for >60 s is reported as `crashed`, so a run can flip between two reads —
  don't cache it, and it un-flips if the process resumes. **`finished_at` separates
  the two kinds of `crashed`:** set = the run reported its own death (an uncaught
  exception, an explicit `finish("crashed")`); null = nobody reported anything and
  the status is a read-time inference — `kill -9`, the OOM killer, an evicted pod.
  The dashboard shows that second case as **stale**, not crashed. Say which you mean:
  "crashed" reads as a bug in the training code, while a stale run usually means the
  machine died and the code is fine. `pandm finish --stale` turns the inference into
  a stored verdict.
- **`progress` / `progress_total`** drive the dashboard ETA; either may be null.
- **`tags`** is a list of free-form labels and **`group`** buckets related runs
  (a sweep, a multi-process job); both may be empty.

## Raw SQL — last resort only

For an aggregation the CLI can't express (e.g. a join across the `metrics`
table). The DB is WAL mode, so reading a live run is safe.

```sql
-- best val/acc per run, across the whole metrics table
SELECT run_id, MAX(value) AS best FROM metrics
WHERE key = 'val/acc' GROUP BY run_id ORDER BY best DESC;
```

Schema: `runs(id, project, name, description, status, config /*JSON*/,
summary /*JSON*/, metric_meta /*JSON*/, tags /*JSON*/, group_name, created_at,
updated_at, finished_at, progress, progress_total)`,
`metrics(run_id, key, step, value, ts)`,
`histograms(run_id, key, step, bins /*JSON*/, counts /*JSON*/, ts)`,
`media(run_id, key, step, filename, caption, ts)` — file at `media/<run_id>/<filename>`.
