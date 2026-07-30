# pandm skills

Agent Skills that let an LLM use [pandm](../README.md) — record experiments and
read them back. Each subdirectory is a self-contained skill (a `SKILL.md` plus
any scripts); the model loads one when its `description` matches the task.

| Skill | Use it to |
|---|---|
| [`pandm-track`](pandm-track/SKILL.md) | Log metrics, images, config, and training progress from a Python script. |
| [`pandm-inspect`](pandm-inspect/SKILL.md) | List runs, read config/summary/metrics, compare runs, and find logged images — as JSON. |

## Installing

The quickest way is [`npx skills`](https://github.com/vercel-labs/skills) — one
command, and it works across Claude Code, Codex, Cursor, OpenCode and others:

```sh
# install both skills into the current project
npx skills add Jannchie/pandm --skill pandm-track --skill pandm-inspect

# or list what the repo offers first
npx skills add Jannchie/pandm --list
```

Handy flags: `-g` installs at the user level instead of the project,
`-a claude-code` targets a single agent, `-y` skips the prompts (CI).

Prefer to do it by hand? The skills use the standard Agent Skills layout, so just
copy a folder into your skills directory:

```sh
cp -r skills/pandm-track skills/pandm-inspect ~/.claude/skills/
```

Both skills assume the `pandm` package is importable in the environment they run
in (`pip install pandm`).
