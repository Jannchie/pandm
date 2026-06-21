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

Prefer the context manager — it reaches `finish()` (and marks the run `crashed`
on an exception) even if the loop raises:

```python
with pandm.init(project="mnist", config={"lr": 1e-3}) as run:
    run.log({"loss": 0.5})
```

## API

| Call | Purpose |
|---|---|
| `pandm.init(project="default", name=None, config=None, *, description=None, total_steps=None, tags=None, group=None, directory=None, remote=None, api_key=None)` | Start a run; returns a `Run` (also a context manager). `description` = a one-line subtitle; `tags=[...]` = filterable labels; `group=` buckets related runs (a sweep, a multi-process job). |
| `run.log(metrics: dict, step=None)` | Log scalar metrics. `step` defaults to an internal per-run counter. |
| `run.log_image(key, image, step=None, caption=None)` | Log one image. `step` defaults to the latest metric step. |
| `run.log_histogram(key, samples, *, step=None, bins=30, description=None)` | Log a distribution snapshot — drawn over time as a density heatmap. Needs numpy. See *Shaping how a metric renders*. |
| `run.set_progress(current, total=None)` | Report progress in a custom unit (epochs, samples) for the ETA. |
| `run.define_metric(key, *, min=None, max=None, unit=None, goal=None, baseline=None, description=None, panel=None, series=None, band=None, kind="line", x_label=None, y_label=None, x_ticks=None, y_ticks=None)` | Declare how the dashboard renders a metric. See *Shaping how a metric renders*. |
| `run.finish(status="finished")` | End the run. Also runs automatically at process exit. |
| `run.delete()` | Delete this run + its media, locally and (in cloud mode) on the server. |

Module-level `pandm.log(...)`, `pandm.log_image(...)`, `pandm.log_histogram(...)`,
`pandm.set_progress(...)`, `pandm.define_metric(...)`, and `pandm.finish(...)` act on the
most recently started run — convenient when passing the `run` object around is awkward.

## Shaping how a metric renders — `define_metric`

By default each key is its own auto-scaling line chart. `define_metric` declares a
better rendering. Every option is a display hint — you still `log()` plain scalars;
unset means today's behaviour, so it is fully backward-compatible. The spec applies
immediately: locally, and in cloud mode it is pushed live (like progress), so a
running run updates right away. The write never interrupts training.

**Axis & annotation.** Two rules — apply them by default, declaring once before the loop:

1. **Any metric with a known range gets that range.** Pin `min`/`max` whenever the
   bounds are known up front (a `[0,1]` score, a `[-1,1]` correlation, a `[0,100]`
   temperature) — left to auto-fit, the axis rescales to noise and small runs look
   identical. If only one end is known, pin that one.
2. **Any percentage/ratio uses `unit="percent"`.** Win/success rate, accuracy,
   precision, any `[0,1]` proportion — `unit="percent"` is the right call over a bare
   `min=0, max=1`, because it both fixes the `0..1` axis *and* shows `73%` instead of `0.73`.

```python
run.define_metric("eval/win_rate", unit="percent", goal="max", baseline=0.5,
                  description="对局胜率，越高越好（0.5 为随机基线）")
run.define_metric("reward/mean", min=-1, max=1)   # known-range scalar, plain axis
```

- `unit="percent"` fixes the range to `0..1` and formats values as `73%` — use it for **every** proportion.
- `min` / `max` pin the y-axis (either or both); omit them only when the range is genuinely unknown.
- `goal` (`"max"` / `"min"`) marks which run is leading when several overlap.
- `baseline` draws a dashed reference line — chance level (`0.5` for win-rate), a prior SOTA.
- `description` is a one-line note under the chart, for names that don't speak for themselves.
- `x_label` / `y_label` name the axes (e.g. `y_label="Reward"`, `x_label="Episode"`) — they show on every chart type.
- `x_ticks` / `y_ticks` (lists of strings) replace numeric ticks with your own labels, positionally — but **only on a categorical axis**: a `kind="bar"` x-axis or a histogram's bin y-axis. They're ignored on a continuous value/time axis, where ticks stay numeric.

```python
run.define_metric("final/seat", kind="bar", x_ticks=["北", "东", "南", "西"], y_label="胜率")
```

**Multi-line panel (`panel=`)** — group related keys into one chart, one line each
(reward decompositions, loss terms, multi-seat/opponent win rates):

```python
run.define_metric("reward/total",   panel="reward", series="total")
run.define_metric("reward/shaping",  panel="reward")   # series= defaults to the key
run.define_metric("reward/terminal", panel="reward")
# log them as usual: run.log({"reward/total": ..., "reward/shaping": ..., ...}, step)
```

Keys sharing a `panel` render together with a legend. Comparing several runs falls
back to one chart per key (coloured by run), so run-vs-run reading still works.

**Confidence band (`band=`)** — a mean line with a shaded interval, for noisy evals.
`band=True` pairs the metric with its `_lo` / `_hi` siblings by name (or name them
explicitly: `band={"lo": "eval/ret_p05", "hi": "eval/ret_p95"}`). Log the three at
the **same step** — they're matched by step for the fill:

```python
run.define_metric("eval/win_rate", unit="percent", goal="max", band=True)
run.log({"eval/win_rate": m, "eval/win_rate_lo": lo, "eval/win_rate_hi": hi}, step=step)
```

**Bar chart (`kind="bar"`)** — category comparison instead of a time axis (per-seat
final win rates, terminal-reason counts, per-opponent results). Bars take each key's
latest value (or its `run.summary` scalar):

```python
for s in range(4):
    run.define_metric(f"final/seat{s}", panel="seat_winrate", kind="bar", series=f"seat{s}")
```

**Distribution over time (`run.log_histogram`)** — the *shape* of a distribution as
it evolves (episode-reward spread, action distribution, advantage spread), not just
its mean. pandm bins client-side (numpy) and stores only the `O(bins)` edges + counts,
so the payload is tiny regardless of sample count. Drawn as a step×bin density heatmap,
sorted into the same prefix section as its siblings (`dist/*` lands under *dist*):

```python
run.log_histogram("dist/episode_reward", episode_rewards, step=step, bins=30,
                  description="每个 eval 的 episode 回报分布")  # one-line subtitle, optional
# already have a histogram? pass it precomputed: log_histogram(key, (counts, edges), step=step)
# label the bin (y) axis categorically: define_metric(key, y_ticks=["低","中","高"], y_label="区间")
```

For a band helper that aggregates raw samples into `mean/_lo/_hi`, and a full RL
example exercising all four, see `examples/train_demo.py` (`simulate_rl`).

> Write `description`s in the **language you're conversing with the user in**, not
> English by default — they exist for that reader. `init(description=...)` says what a
> run tests (the idea, the key knob, the dataset); `define_metric(description=...)` says
> what a metric measures and which way is good. One plain line; the name, axis, and
> config already carry the mechanics, so don't restate them.

## Clean up throwaway runs

Every `pandm.init()` creates a run — including the quick ones spun up just to smoke-test
that logging works. In cloud mode those land on the shared server and pile up as clutter.
**If you created a run only to test, delete it when done.** init prints the run's id and
URL so you can find it again:

```
pandm: run "baseline" [a1b2c3d4] -> https://pandm.jannchie.com/?project=mnist&runs=a1b2c3d4
```

- in the same script: `run.delete()` — drops the run, its metrics, and media, locally
  and (when signed in) on the server.
- from the shell: `pandm delete <id> -y` — `-y` skips the confirm; `--local-only` keeps
  the cloud copy.

## Behaviour that matters

- **Name every run distinctively.** Without `name=` pandm falls back to a timestamp
  (`2026-06-10_14:30:52`) — unique, but unreadable side by side. Pass a short name for
  what sets this run apart: the swept knob (`lr-3e-4`, `bs-128`), the variant
  (`resnet50-aug`, `no-warmup`), or the intent (`fix-grad-clip`). Re-running the same
  config? Add a suffix (`lr-3e-4-v2`). Runs are keyed by id, not name, so collisions are
  legal — but the dashboard lists by name, so they cost you at a glance.
- **`step` is optional.** Omit it and pandm uses a per-run counter (+1 per `log()` call).
  Pass it and keep it monotonic per key — the dashboard plots against it.
- **Group keys with `/`.** `train/loss`, `val/loss`, `lr` — the dashboard groups by the
  prefix before the slash.
- **NaN / Inf are dropped silently.** Guard values you actually need; a sometimes-nonfinite
  metric gets gaps, not errors.
- **Images** accept a PIL `Image`, a numpy/torch array (HWC *or* CHW is detected; float
  arrays in `[0,1]` are auto-scaled to `[0,255]`), a file path, or raw PNG bytes — don't
  pre-convert tensors.
- **Run status** is `running` → `finished` or `crashed`. Uncaught exceptions (via
  `sys.excepthook`) and hard kills (`kill -9`, OOM — the 15 s heartbeat quiet for 60 s)
  become `crashed`. Reach `finish()` / exit the `with` block for `finished`.
- **ETA:** pass `total_steps=` and progress follows your `log(step=...)` automatically;
  for other units call `run.set_progress(current, total)`.

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
</content>
</invoke>
