---
name: pandm-track
description: Record machine-learning experiment metrics, images, distributions, hyperparameters, and training progress to pandm — a local-first, offline-by-default, account-free wandb/tensorboard alternative that writes to a local `.pandm/` SQLite + PNG store. Needs no network, server, login, or API key; a shared server is strictly optional, and once one is configured the same code auto-pushes to it in the background. Use whenever you write OR edit pandm instrumentation: instrumenting a training/eval loop, logging scalar metrics or images, adding/renaming/regrouping a logged metric, changing what a script reports, shaping how a chart renders (titles, subtitles, units, axis labels, panels, confidence bands, bar/histogram charts — especially for RL), reporting an ETA, or saving a run's config to compare in the dashboard. Read this before touching any `pandm.init` / `run.log` / `run.define_metric` call — it sets the titling discipline every run and every metric needs.
---

# Recording experiments with pandm

pandm tracks ML runs locally: `pandm.init()` starts a run, `run.log()` writes
scalar metrics, `run.log_image()` writes images. Everything lands in `.pandm/`
next to the script (plain SQLite + PNG, no account, no daemon).

**No network is required.** pandm runs entirely offline by default — no server,
no login, no API key, no internet. It works the same on an air-gapped box as on
a laptop. The local `.pandm/` write is the source of truth and never depends on a
network.

**If a remote is configured, the same code also pushes to it — automatically.**
Once a remote is available (you ran `pandm login <url>` on the machine, or set
`PANDM_REMOTE`/`PANDM_API_KEY`), every run dual-writes: local first, then synced
to the shared server in the background, backfilling anything logged while
offline. You don't change the training code or re-opt-in per run — pandm just
uses the remote when it's there and stays fully local when it isn't. So: cloud is
opt-in to *set up*, but automatic once set up. Don't add cloud setup, network
checks, or credentials yourself unless the user asks for the shared dashboard —
see *Modes* below.

Requires only the package: `pip install pandm` (import name is `pandm`).

## Minimal loop

```python
import pandm

run = pandm.init(
    project="mnist",
    name="lr1e-3-bs64",                    # TITLE: what's unique about this run
    description="基线：小 LR + 大 batch，验证早期 loss 是否更稳",  # SUBTITLE — see "Title everything"
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

## Title everything — the first job, not the last

A chart nobody can read is wasted compute. **Every run and every metric needs a
title (what it is) and a subtitle (what it means)** — fill them in *as* you
instrument, not "later". This is the difference between a dashboard that explains
itself weeks later, next to ten sibling runs, and a wall of `metric_3` lines.

|        | Title (what it is)                                                        | Subtitle (what it means)                                          |
|--------|---------------------------------------------------------------------------|------------------------------------------------------------------|
| **Run**    | `name=` — what's *unique* about this run (the swept knob, the variant, the intent), never the project | `description=` — the one question this run answers / hypothesis it tests |
| **Metric** | the `key`, namespaced with `/` (`train/loss`); `series=` sets its legend label inside a panel | `define_metric(key, description=...)` — what it measures and which way is good |

```python
run = pandm.init(
    project="mahjong",
    name="reward-shaping-v2",                       # TITLE: what's unique about this run
    description="加入向听数势能塑形，验证是否加速早期收敛",  # SUBTITLE: the question it answers
    config={"shaping_coef": 0.3, "lr": 3e-4},
)
run.define_metric("eval/win_rate", unit="percent", goal="max", baseline=0.25,
                  description="对随机对手的对局胜率（0.25 为四人随机基线）")  # metric subtitle
```

Three rules that make the titles actually useful:

1. **Write subtitles in the language you're conversing with the user in** — they
   exist for that reader, not English-by-default.
2. **A subtitle must say what the title can't** — the hypothesis, the baseline, the
   unit's meaning, which direction is good. If it just restates the name, drop it.
3. **No `name=` → a timestamp.** pandm falls back to `2026-06-10_14:30:52` — unique
   but unreadable side by side. Always pass a name.

Everything below is *how* to render well once the titles are in place. Reach for the
axis / unit / panel / band options in *Shaping how a metric renders* — but a correct
title and subtitle come first.

## API

| Call | Purpose |
|---|---|
| `pandm.init(project="default", name=None, config=None, *, description=None, total_steps=None, tags=None, group=None, directory=None, remote=None, api_key=None)` | Start a run; returns a `Run` (also a context manager). `description` = a one-line subtitle; `tags=[...]` = filterable labels; `group=` buckets related runs (a sweep, a multi-process job). |
| `run.log(metrics: dict, step=None)` | Log scalar metrics. `step` defaults to an internal per-run counter. |
| `run.log_image(key, image, step=None, caption=None)` | Log one image. `step` defaults to the latest metric step. |
| `run.log_histogram(key, samples, *, step=None, bins=30, description=None)` | Log a distribution snapshot — drawn over time as a density heatmap. Needs numpy. See *Shaping how a metric renders*. |
| `run.set_progress(current, total=None)` | Report progress in a custom unit (epochs, samples) for the ETA. |
| `run.ingest_csv(path, *, step_column=None, include=None, exclude=None, prefix="")` | Import metric rows from a CSV a closed-source trainer writes; incremental (row-count cursor). Returns rows ingested. See *Trainers you can't inject a logger into*. |
| `run.watch_csv(path, *, interval=5.0, step_column=None, include=None, exclude=None, prefix="")` | Tail that CSV from a background thread until the run finishes; returns a `stop()`. Same filtering args as `ingest_csv`. |
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

> Every `define_metric` example above carries a `description` for a reason — see
> *Title everything*. The subtitle is part of declaring the metric, not an afterthought.

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

- **Run names need only be distinctive, not unique.** Runs are keyed by id, so two
  runs may share a name — but the dashboard lists by name, so a collision costs you at
  a glance; add a suffix when re-running the same config (`lr-3e-4-v2`). For *what* to
  name a run, see *Title everything*.
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

## PyTorch Lightning

`PandmLogger` is a Lightning `Logger`; it takes the same arguments as `pandm.init`.
Whatever your `LightningModule` passes to `self.log(...)` pandm follows.

```python
from pandm.integrations.lightning import PandmLogger

logger = PandmLogger(project="mnist", name="baseline", config={"lr": 1e-3})
trainer = Trainer(logger=logger)
trainer.fit(model)                                  # self.log("train/loss", loss) -> run.log
# images / histograms / summary: reach the underlying run via logger.experiment
logger.experiment.log_image("samples", img, step=step)
```

The run is created lazily on the first `log_hyperparams`/`log_metrics`, so
`save_hyperparameters()` folds into the run config. Lightning ships as two
mutually incompatible packages (`pytorch_lightning` vs `lightning.pytorch`);
`PandmLogger` subclasses whichever is importable. If **both** are installed, set
`PANDM_LIGHTNING_BACKEND=pytorch_lightning` (or `=lightning`) before import to
match the `Trainer` you use.

## Trainers you can't inject a logger into — follow their CSV

Closed-source trainers (rfdetr, YOLO, detectron2) build their own logger list and
won't accept an injected one, but they dump a per-epoch `metrics.csv`. Point pandm
at that file instead of fighting for a logger seat — each new numeric row becomes a
`log()` call.

```python
run = pandm.init(project="det", name="rfdetr-baseline",
                 description="RF-DETR 默认超参基线，跟踪 mAP 曲线")
run.watch_csv("output/metrics.csv", step_column="epoch")   # live: tails in the background
rfdetr_model.train(...)                                     # writes metrics.csv as it goes
run.finish()                                               # stops the watcher, drains the tail
```

- Ingestion is **incremental** (row-count cursor), safe to call repeatedly — assumes
  existing rows are immutable and the file only grows at the bottom (true for per-epoch logs).
- `step_column=` names the step column (`"epoch"`, `"step"`); omit it to use the auto counter.
- `include=` / `exclude=` filter source columns; `prefix=` namespaces the keys (`prefix="val/"`).
- One-shot instead of live: `run.ingest_csv("output/metrics.csv", step_column="epoch")`.
- CSV columns still need titling — once the keys land, declare them with `define_metric`
  (`unit="percent"` for mAP-style ratios, `panel=` to group, a `description=`). See *Title everything*.

## Verify it landed

```sh
pandm ls                 # the new run shows up here
pandm show <run_id>      # config + per-metric last value
pandm ui                 # http://127.0.0.1:7878 — live charts
```

A full working example lives in the repo at `examples/train_demo.py`. To read
runs back programmatically, use the **pandm-inspect** skill.

## Before you finish — self-check

Run this checklist before you call the instrumentation done:

- [ ] **Every run** has a `name` (distinctive) *and* a `description` (what it tests)?
- [ ] **Every metric** that isn't self-explanatory has `define_metric(description=...)`?
- [ ] **Every proportion** (accuracy, win/success rate) uses `unit="percent"`, and every known-range metric pins `min`/`max`?
- [ ] **Related keys** grouped into one `panel=` instead of N lonely charts?
- [ ] Titles & subtitles in **the user's language**, each subtitle saying what the name can't?
- [ ] **Throwaway / smoke-test runs deleted** (`run.delete()` or `pandm delete <id> -y`)?

Any unchecked box: fix it now — far cheaper than re-opening the dashboard weeks later
wondering what `metric_3` was.
