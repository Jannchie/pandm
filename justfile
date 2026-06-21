# 列出所有命令
default:
    @just --list

# Python 单测
test:
    uv run pytest tests -q

# pyright 类型检查
typecheck:
    pyright

# 单测 + 类型检查
check: test typecheck

# 代码检查：ruff (python) + oxlint (前端)
lint:
    uv run ruff check src tests examples
    pnpm -C web lint

# 格式化：ruff format (python) + prettier (前端)
fmt:
    uv run ruff format src tests examples
    pnpm -C web format

# 构建前端到 src/pandm/static（打包进 wheel 前必跑）
web-build:
    pnpm -C web build

# Cloudflare Workers 契约测试
workers-test:
    pnpm -C workers typecheck
    pnpm -C workers test

# 部署到 Cloudflare（先建前端再 wrangler deploy）
workers-deploy:
    pnpm -C workers run deploy

# 前端开发服务器（/api 代理到 127.0.0.1:7878）
web-dev:
    pnpm -C web dev

# 生成演示数据
demo:
    uv run python examples/train_demo.py

# 启动本地 dashboard
ui:
    uv run pandm ui

# 一条龙：建前端 → 造数据 → 开 dashboard
play: web-build demo ui
