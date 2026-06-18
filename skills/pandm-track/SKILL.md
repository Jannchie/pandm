---
name: pandm-track
description: Record machine-learning experiment metrics, images, distributions, hyperparameters, and training progress to pandm — a local-first, account-free wandb/tensorboard alternative that writes to a `.pandm/` SQLite + PNG store. Use when instrumenting a training or evaluation loop, logging scalar metrics or images from a Python script, building richer charts (multi-line panels, confidence bands, bar charts, histograms — especially for RL), reporting an ETA, or saving a run's config so it can later be compared in the pandm dashboard.
---

# Recording experiments with pandm

pandm tracks ML runs locally: `pandm.init()` starts a run, `run.log()` writes
scalar metrics, `run.log_image()` writes images. Everything lands in `.pandm/`
next to the script (plain SQLite + PNG, no account, no daemon). The same code
reports to a shared server when one env var is set — see *Modes* below.

Requires the package: `pip install pandm` (import name is `pandm`).

## Minimal loop

```python
import pandm

run = pandm.init(
    project="mnist",
    name="lr1e-3-bs64",                    # recommended: a short name that says what's unique about this run (see Naming below)
    config={"lr": 1e-3, "batch_size": 64}, # hyperparameters — anything JSON-able
    total_steps=1000,                      # optional; lets the dashboard show an ETA
)
for step in range(1000):
    loss, acc = train_step()
    run.log({"train/loss": loss, "train/acc": acc}, step=step)
    if step % 100 == 0:
        run.log_image("samples", sample_grid, step=step, caption=f"step {step}")
run.finish()
```

Prefer the context manager — it finishes the run (and marks it `crashed` on an
exception) even if the loop raises:

```python
with pandm.init(project="mnist", config={"lr": 1e-3}) as run:
    run.log({"loss": 0.5})
```

## API

| Call | Purpose |
|---|---|
| `pandm.init(project="default", name=None, config=None, *, description=None, total_steps=None, directory=None, remote=None, api_key=None)` | Start a run; returns a `Run` (also a context manager). `description` = a one-line subtitle. |
| `run.log(metrics: dict, step=None)` | Log scalar metrics. `step` defaults to an internal per-run counter. |
| `run.log_image(key, image, step=None, caption=None)` | Log one image. `step` defaults to the latest metric step. |
| `run.log_histogram(key, samples, *, step=None, bins=30)` | Log a distribution snapshot — drawn over time as a density heatmap. Needs numpy. See *Richer charts*. |
| `run.set_progress(current, total=None)` | Report progress in a custom unit (epochs, samples) for the ETA. |
| `run.define_metric(key, *, min=None, max=None, unit=None, goal=None, baseline=None, description=None, panel=None, series=None, band=None, kind="line")` | Declare how the dashboard renders a metric — fixed axis, percent, baseline, goal, subtitle, **and** multi-line panels / CI bands / bar charts. See *Richer charts*. |
| `run.finish(status="finished")` | End the run. Also runs automatically at process exit. |
| `run.delete()` | Delete this run + its media, locally and (in cloud mode) on the server. For cleaning up throwaway runs. |

Module-level `pandm.log(...)`, `pandm.log_image(...)`, `pandm.log_histogram(...)`,
`pandm.set_progress(...)`, `pandm.define_metric(...)`, and `pandm.finish(...)` act on the
most recently started run — convenient when passing the `run` object around is awkward.

## Declaring how a metric should look

Anything bounded — a win/success rate, accuracy, precision, any `[0,1]` score —
reads better on a fixed axis than on one that auto-rescales to noise. Say so once,
before the loop: `define_metric` pins the y-axis, can show `73%` instead of `0.73`,
draws a baseline, and marks the leading run when you compare several.

```python
run.define_metric("eval/win_rate", unit="percent", goal="max", baseline=0.5,
                  description="对局胜率，越高越好（0.5 为随机基线）")
run.define_metric("acc", min=0, max=1)          # bounded score, plain 0..1 axis
```

- `unit="percent"` defaults the range to `0..1` and formats values as percentages.
- `min` / `max` pin the y-axis (either or both); omit them and the axis fits the data.
- `goal` (`"max"` / `"min"`) marks which run is currently leading when several overlap.
- `baseline` draws a dashed reference line — chance level (`0.5` for win-rate), a prior SOTA.
- `description` is a one-line note shown under the chart, for metrics whose name doesn't speak for itself.

Reach for it whenever "good" has a known scale (RL win/success rates, classification
accuracy, anything in `0..1`). The spec applies immediately — locally, and in cloud
mode it is pushed live to the server (like progress), so a running run shows the fixed
axis right away. The backend write never interrupts training.

## Richer charts: panels, confidence bands, bars, distributions

By default each metric key is its own line chart. Four `define_metric` options (plus
`log_histogram`) cover the shapes that line-per-key can't — especially for RL. All are
**author-declared display hints**: you still `log()` plain scalars; the dashboard renders
them differently. Unset means today's behaviour, so they're fully backward-compatible.

**Multi-line panel (`panel=`)** — group related keys into one chart, one line each.
Ideal for reward decompositions, loss terms, multi-seat/opponent win rates:

```python
run.define_metric("reward/total",   panel="reward", series="total")
run.define_metric("reward/shaping",  panel="reward")   # series= defaults to the key
run.define_metric("reward/terminal", panel="reward")
# log them as usual: run.log({"reward/total": ..., "reward/shaping": ..., ...}, step)
```

Keys sharing a `panel` value render together with a legend. When several runs are
selected for comparison the panel falls back to one chart per key (coloured by run), so
run-vs-run reading still works.

**Confidence band (`band=`)** — a mean line with a shaded interval, for noisy evals
where you need to tell signal from sampling noise. Log three scalars (mean + bounds);
`band=True` pairs the metric with its `_lo` / `_hi` siblings by name:

```python
run.define_metric("eval/win_rate", unit="percent", goal="max", band=True)
# each eval: log the mean and the interval bounds together, at the same step
run.log({"eval/win_rate": m, "eval/win_rate_lo": lo, "eval/win_rate_hi": hi}, step=step)
```

Or name the bounds explicitly: `band={"lo": "eval/ret_p05", "hi": "eval/ret_p95"}`.
Log the three at the **same step** (they're matched by step for the shaded fill).

**Bar chart (`kind="bar"`)** — category comparison instead of a time axis: per-seat final
win rates, terminal-reason counts, per-opponent results. Bars take each key's latest
value (or its `run.summary` scalar):

```python
for s in range(4):
    run.define_metric(f"final/seat{s}", panel="seat_winrate", kind="bar", series=f"seat{s}")
```

**Distribution over time (`run.log_histogram`)** — the *shape* of a distribution as it
evolves, not just its mean: episode-reward spread (bimodal? long-tailed?), action
distribution (policy collapse?), advantage spread. pandm bins the samples client-side
(numpy) and stores only the `O(bins)` edges + counts, so the payload is tiny regardless
of sample count. Drawn as a step×bin density heatmap under a *distributions* section:

```python
run.log_histogram("dist/episode_reward", episode_rewards, step=step, bins=30)
# already have a histogram? pass it precomputed: log_histogram(key, (counts, edges), step=step)
```

For a confidence-band helper that aggregates raw samples into `mean/_lo/_hi`, and a full
RL example exercising all four, see `examples/train_demo.py` (`simulate_rl`).

## Subtitles: describe runs and metrics in the reader's language

A reader scanning the dashboard often doesn't know what `eval/win_rate` or a run named
`ppo-7` means. Give them a one-line subtitle:

- **Run** — `pandm.init(..., description="...")`: what this run is (the idea being tested,
  the key knob, the dataset). Shown under the run name in the sidebar.
- **Metric** — `define_metric(key, description="...")`: what the metric measures and which
  way is good. Shown under the chart.

> Write both in the **language the user is conversing with you in**, not English by
> default — the description exists for that reader. Keep it to one plain line; the name,
> axis, and config already carry the mechanics, so don't restate them.

## Clean up throwaway runs

Every `pandm.init()` creates a run — including the quick ones spun up just to
smoke-test that logging works. In cloud mode those land on the shared server and
pile up as clutter. **If you created a run only to test, delete it when done.**

On init pandm prints the run's id and URL so you can find it again:

```
pandm: run "baseline" [a1b2c3d4] -> https://pandm.jannchie.com/?project=mnist&runs=a1b2c3d4
```

Remove a test run with that id:

- in the same script: `run.delete()` — drops the run, its metrics, and media,
  locally and (when signed in) on the server.
- from the shell: `pandm delete <id> -y` — `-y` skips the confirm prompt;
  `--local-only` keeps the cloud copy.

Don't leave smoke-test runs behind on a cloud/shared server.

## Behaviour that matters

- **Name every run distinctively.** Omit `name=` and pandm falls back to a
  timestamp (`2026-06-10_14:30:52`) — unique, but unreadable once a handful of
  runs sit side by side in the dashboard. You know what this run is *for*, so say
  it: pass a short name that captures what sets this run apart from its siblings —
  the swept knob (`lr-3e-4`, `bs-128`), the variant (`resnet50-aug`, `no-warmup`),
  or the intent (`fix-grad-clip`). Re-running the same config? Append a short
  suffix (`lr-3e-4-v2`) so the two don't read identically. Runs are keyed by id,
  not name, so collisions are legal — but the dashboard lists by name, so they
  cost you at a glance.
- **`step` is optional.** Omit it and pandm uses a per-run counter that advances
  by one per `log()` call. If you pass `step`, keep it monotonic per key — the
  dashboard plots against it.
- **Group keys with `/`.** `train/loss`, `val/loss`, `lr` — the dashboard groups
  by the prefix before the slash.
- **NaN / Inf are dropped silently.** Guard or sanitize values you actually need;
  a metric that is sometimes non-finite will have gaps, not errors.
- **Images** accept a PIL `Image`, a numpy/torch array (HWC *or* CHW is detected;
  float arrays in `[0,1]` are auto-scaled to `[0,255]`), a file path, or raw PNG
  bytes. Don't pre-convert tensors — pass them through.
- **Run status** is `running` → `finished` or `crashed`. Uncaught exceptions
  (via `sys.excepthook`) and hard kills (`kill -9`, OOM — detected when the 15 s
  heartbeat goes quiet for 60 s) become `crashed`. Always reach `finish()` /
  exit the `with` block on the happy path so it lands as `finished`.
- **ETA:** pass `total_steps=` and progress follows your `log(step=...)`
  automatically; for other units call `run.set_progress(current, total)`.

## Modes (the training code never changes)

| Goal | How |
|---|---|
| Local only (default) | nothing — writes to `./.pandm`. |
| Different data dir | `PANDM_DIR=/path` env var, or `init(directory=...)`. |
| Local **and** a shared server | `pandm login <url>` once per machine → `init()` dual-writes and syncs in the background, backfilling anything logged offline. |
| Remote only (no local copy) | `PANDM_REMOTE=<url>` + `PANDM_API_KEY=...`, or `init(remote=..., api_key=...)`. |
| Force local even when signed in | `init(remote=False)` or `PANDM_NO_SYNC=1`. |

## Hugging Face Accelerate

Accelerate only resolves strings for its built-in trackers, so pass an instance:

```python
from accelerate import Accelerator
from pandm.integrations.accelerate import PandmTracker

accelerator = Accelerator(log_with=PandmTracker(project="mnist", name="baseline"))
accelerator.init_trackers("mnist", config={"lr": 1e-3})
accelerator.log({"loss": 0.42}, step=10)            # -> run.log
accelerator.end_training()                          # -> run.finish
# images: accelerator.get_tracker("pandm", unwrap=True).log_image("samples", img, step=step)
```

## Verify it landed

```sh
pandm ls                 # the new run shows up here
pandm show <run_id>      # config + per-metric last value
pandm ui                 # http://127.0.0.1:7878 — live charts
```

A full working example lives in the repo at `examples/train_demo.py`. To read
runs back programmatically, use the **pandm-inspect** skill.
