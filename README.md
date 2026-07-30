# pandm

pandm tracks ML experiments locally. The Python SDK writes metrics and images straight to a `.pandm/` directory next to your code — no account, no daemon, no cloud — and `pandm ui` serves a dashboard to compare runs. Unlike wandb there is nothing to sign up for, and unlike tensorboard the data is plain SQLite + PNG files you can query yourself. The same scripts report to a shared server over HTTP when you set one env var.

![dashboard](docs/screenshot.png)

## Install

```sh
pip install pandm
```

## Quick start

```python
import pandm

run = pandm.init(project="mnist", config={"lr": 1e-3, "batch_size": 64}, description="baseline sweep")
run.define_metric("train/acc", unit="percent", goal="max")  # fixed 0–100% axis, leading run marked
for step in range(1000):
    loss, acc = train_step()
    run.log({"train/loss": loss, "train/acc": acc}, step=step)
    if step % 100 == 0:
        run.log_image("samples", sample_grid, step=step)  # PIL / numpy / torch / path
run.summary({"best/acc": 0.99, "best/epoch": 7})  # the chosen checkpoint's self-consistent row
run.finish()
```

```sh
pandm ui   # opens http://127.0.0.1:7878
```

The dashboard overlays selected runs per metric, with smoothing, log scale, step/time axes, an image browser with a step slider, and a config/summary comparison table. It polls while runs are alive, so curves grow during training.

### Telling the dashboard what matters

A run that logs 30 metrics has at most 3 that decide anything, and alphabetical order can't tell them apart. The training code knows which is which, so it says so — every option below is optional, and unset means the previous behaviour:

```python
run.define_metric("vs_baseline/rank", importance="primary", goal="min")  # pinned, large, top of page
run.define_metric("dropped_rows", importance="debug")                    # folded away at the bottom
run.define_metric("budget_exceeded_rate", alarm={"ok": 0})               # a badge until it trips, then red + on top
run.define_metric("train/loss", panel="optim", scale="log")              # per-metric log axis
run.define_metric("train/grad_norm", panel="optim", axis="right")        # its own scale, an order of magnitude apart
run.define_metric("opt/lr", kind="stat")                                 # one value + sparkline, 1/6 of a slot
run.define_metric("pool/0/rank", panel="pool", kind="table", row="anchor", series="avg rank")
```

`alarm` also warns on stderr the first time it trips and POSTs the violation to `PANDM_ALARM_WEBHOOK` if set — a 30-hour unattended run has nobody watching the page. Charts carry hover-highlight and click-a-legend-entry-to-isolate, so a ten-line panel stays readable; the toolbar filters 100+ keys by name, stitches resumed runs of one `group` into a single curve, and a **Scatter** tab plots one point per run (pick a metric for each axis) for cross-run questions.

## Usage

`step` is optional (an internal counter is used). Runs end as `finished` or `crashed`: uncaught exceptions are detected via `sys.excepthook` (and the context manager), and hard-killed processes (`kill -9`, OOM) are presumed crashed once their 15s heartbeat goes quiet for 60s — self-healing if the process was merely suspended. That verdict is an inference, so it isn't written down: `finished_at` stays null until `pandm finish --stale` records it for good.

```python
with pandm.init(project="mnist") as run:
    run.log({"loss": 0.5})
```

Inspect, export or delete runs from the terminal:

```sh
pandm ls                          # list runs (-P project, -s status, -t tag, --sort-by val/acc)
pandm projects                    # projects with run counts
pandm show <run_id>               # config, summary, logged metrics
pandm export <run_id> > data.csv  # full series as CSV (or --json, -k <key>, --histograms)
pandm tag <run_id> best --rm wip  # add/remove tags
pandm edit <run_id> --name gold   # rename, or move with -P <project>
pandm finish --stale              # persist 'crashed' for runs whose process died
pandm delete <run_id> -y          # delete runs (local + cloud); also -s crashed or -P <project>
pandm ingest metrics.csv --step-column epoch --watch  # follow another trainer's CSV
```

Data lives in `./.pandm` by default; override with `--dir` or `PANDM_DIR`.

### Resuming a run

Give a run a stable `id` and pass `resume=True` to continue it after a crash, a
preemption (spot/OOM), or a manual restart — the run flips back to `running` and
its step counter picks up past the last logged step instead of starting a second,
disconnected run. Its original config is kept.

```python
run = pandm.init(project="mnist", id="exp-42", resume=True)  # continue if it exists, else start fresh
# resume="must" errors if exp-42 is missing; a fresh id that already exists errors unless resume is set
```

`pandm show` reports `MIN`/`MAX` per metric next to the last value (and the read
API carries a `stats` field — `{min, max, last, count}` per key — so the
dashboard and `pandm-inspect` can pick the best run, not just the latest value).

### Hugging Face Accelerate

Pass a `PandmTracker` instance to `Accelerator` (Accelerate only resolves strings for its built-in trackers) — `accelerator.log` then reports to pandm, and `end_training` finishes the run:

```python
from accelerate import Accelerator
from pandm.integrations.accelerate import PandmTracker

accelerator = Accelerator(log_with=PandmTracker(project="mnist", name="baseline"))
accelerator.init_trackers("mnist", config={"lr": 1e-3})
accelerator.log({"loss": 0.42}, step=10)
accelerator.end_training()
```

For images, unwrap the raw run: `accelerator.get_tracker("pandm", unwrap=True).log_image("samples", img, step=step, caption=prompt)`.

### Cloud mode

Training scripts never change — sign in once per machine and `pandm.init()` dual-writes: local stays the source of truth, a background thread syncs to the server, and anything logged offline is backfilled on reconnect. Delivery is exact-once (re-pushes are deduped server-side). Sync never stalls training: every network step is time-bounded (`PANDM_SYNC_TIMEOUT`, default 10s) and `finish()` flushes the tail under a hard budget (`PANDM_FINISH_TIMEOUT`, default 4s) before leaving the rest to `pandm sync`.

```sh
pandm login        # hosted cloud (pandm.jannchie.com); pass a URL for self-hosted
python train.py    # local + cloud
pandm sync         # backfill runs whose process already exited
pandm pull         # download cloud runs on another machine
pandm whoami       # who am I signed in as, and what's still unpushed
```

`pandm login` uses device-flow approval (like `gh auth login`): it prints a URL
to open in any browser and polls until you approve — so it works over ssh, where
it won't try to open a browser on the remote host. Until you're signed in, the
first `pandm.init()` offers to log in on an interactive terminal, or prints a
one-line hint on a non-interactive one (CI, `nohup`, ssh) — it never blocks a
run. `PANDM_SILENT=1` silences the hint for good, as does logging in or choosing
*keep local*.

Each user signs in with GitHub and sees only their own runs. Two interchangeable server implementations speak the same protocol — the full walkthrough (OAuth App, custom domain, backups, troubleshooting) is in **[docs/deploy.md](docs/deploy.md)**:

**Cloudflare Workers** (serverless: D1 for metrics, R2 for media — `workers/`):

```sh
cd workers && pnpm install
npx wrangler d1 create pandm             # paste the database_id into wrangler.jsonc
npx wrangler secret put GITHUB_CLIENT_ID     # OAuth App callback: https://<domain>/api/auth/callback
npx wrangler secret put GITHUB_CLIENT_SECRET
npx wrangler secret put PANDM_SECRET_KEY     # e.g. `openssl rand -hex 32`
npx wrangler d1 migrations apply pandm --remote
pnpm run deploy
```

> Note: D1 bills per row written (100k/day free). Logging ~10 metrics/sec around the clock lands in the paid tier — a few dollars a month.

**Self-hosted Python server** (same binary as `pandm ui`):

```sh
GITHUB_CLIENT_ID=… GITHUB_CLIENT_SECRET=… docker compose up -d   # multi-user mode
```

Without OAuth env vars the server falls back to single-tenant mode — `pandm server --api-key my-secret` plus `PANDM_REMOTE`/`PANDM_API_KEY` on the client (remote-only, no local copy, no accounts).

## API

| | |
|---|---|
| `pandm.init(project, name=None, config=None, *, description=None, id=None, resume=False, total_steps=None, tags=None, group=None, directory=None, remote=None, api_key=None)` | start (or resume) a run; `description` is a one-line subtitle, `tags=[...]` adds filterable labels, `group=` buckets related runs |
| `run.log(metrics, step=None)` | log scalar metrics |
| `run.log_image(key, image, step=None, caption=None)` | log an image |
| `run.summary(values)` | record run-level scalars (the chosen checkpoint's metric row); merges across calls |
| `run.define_metric(key, *, min=None, max=None, unit=None, goal=None, baseline=None, description=None, panel=None, series=None, band=None, kind="line", importance=None, alarm=None, axis=None, scale=None, row=None, x_label=None, y_label=None, x_ticks=None, y_ticks=None)` | declare a metric's display: fixed axis, `unit="percent"`, `baseline` line, `goal` for the leading run, `description` subtitle, `panel` grouping, `importance` ranking, `alarm` thresholds — see *Telling the dashboard what matters* |
| `run.finish(status="finished")` | end the run (also via `atexit`) |
| `run.delete()` | delete the run + media, local and cloud — for throwaway smoke-test runs |
| `GET /api/docs` | REST API reference on any running server |

## Agent skills

LLM/agent harnesses can drive pandm through two [Agent Skills](skills/): one to
record runs, one to read them back as JSON. Install them with
[`npx skills`](https://github.com/vercel-labs/skills):

```sh
npx skills add Jannchie/pandm --skill pandm-track --skill pandm-inspect
```

`-g` installs at the user level, `-a claude-code` targets one agent. See
[skills/README.md](skills/README.md) for what each skill does.

## Development

```sh
uv sync && uv run pytest          # python sdk + server
cd web && pnpm install && pnpm dev   # dashboard dev server (proxies to :7878)
pnpm build                        # bundles the dashboard into src/pandm/static
cd workers && pnpm install && pnpm test   # cloudflare workers server (contract tests)
```

## License

MIT
