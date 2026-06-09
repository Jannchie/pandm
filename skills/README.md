# pandm skills

Agent Skills that let an LLM use [pandm](../README.md) — record experiments and
read them back. Each subdirectory is a self-contained skill (a `SKILL.md` plus
any scripts); the model loads one when its `description` matches the task.

| Skill | Use it to |
|---|---|
| [`pandm-track`](pandm-track/SKILL.md) | Log metrics, images, config, and training progress from a Python script. |
| [`pandm-inspect`](pandm-inspect/SKILL.md) | List runs, read config/summary/metrics, compare runs, and find logged images — as JSON. |

## Installing

These follow the Anthropic Agent Skills layout, so they drop into any client
that reads a skills directory:

- **Claude Code** — copy or symlink a skill folder into `.claude/skills/` (project)
  or `~/.claude/skills/` (personal):
  ```sh
  mkdir -p ~/.claude/skills
  cp -r skills/pandm-track skills/pandm-inspect ~/.claude/skills/
  ```
- **Other harnesses** — point your skills loader at this `skills/` directory, or
  copy the folders wherever that harness discovers them.

Both skills assume the `pandm` package is importable in the environment they run
in (`pip install pandm`). `pandm-inspect` also calls its bundled
`scripts/pandm_inspect.py`; keep the script alongside its `SKILL.md` when copying.
