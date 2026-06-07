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

run = pandm.init(project="mnist", config={"lr": 1e-3, "batch_size": 64})
for step in range(1000):
    loss, acc = train_step()
    run.log({"train/loss": loss, "train/acc": acc}, step=step)
    if step % 100 == 0:
        run.log_image("samples", sample_grid, step=step)  # PIL / numpy / torch / path
run.finish()
```

```sh
pandm ui   # opens http://127.0.0.1:7878
```

The dashboard overlays selected runs per metric, with smoothing, log scale, step/time axes, an image browser with a step slider, and a config/summary comparison table. It polls while runs are alive, so curves grow during training.

## Usage

`step` is optional (an internal counter is used). Runs end as `finished` or `crashed`: uncaught exceptions are detected via `sys.excepthook` (and the context manager), and hard-killed processes (`kill -9`, OOM) are presumed crashed once their 15s heartbeat goes quiet for 60s — self-healing if the process was merely suspended.

```python
with pandm.init(project="mnist") as run:
    run.log({"loss": 0.5})
```

List or delete runs from the terminal:

```sh
pandm ls
pandm delete <run_id>
```

Data lives in `./.pandm` by default; override with `--dir` or `PANDM_DIR`.

### Cloud mode

Run a server anywhere, then point training scripts at it — no code changes:

```sh
pandm server --api-key my-secret               # on the server (default port 7878)
```

```sh
export PANDM_REMOTE=http://my-host:7878
export PANDM_API_KEY=my-secret
python train.py                                 # same script, now reports over HTTP
```

The API key protects write endpoints only; put the server behind a reverse proxy if reads need auth too. If the server becomes unreachable mid-run, the SDK warns and keeps training: it retries every 30s, replays run creation on recovery, and drops whatever was logged while offline.

## API

| | |
|---|---|
| `pandm.init(project, name=None, config=None, *, directory=None, remote=None, api_key=None)` | start a run |
| `run.log(metrics, step=None)` | log scalar metrics |
| `run.log_image(key, image, step=None, caption=None)` | log an image |
| `run.finish(status="finished")` | end the run (also via `atexit`) |
| `GET /api/docs` | REST API reference on any running server |

## Development

```sh
uv sync && uv run pytest          # python sdk + server
cd web && pnpm install && pnpm dev   # dashboard dev server (proxies to :7878)
pnpm build                        # bundles the dashboard into src/pandm/static
```

## License

MIT
