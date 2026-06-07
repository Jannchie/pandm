# ---- dashboard build
FROM node:22-alpine AS web
RUN npm install -g pnpm@10
WORKDIR /repo/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build
# vite outDir is ../src/pandm/static -> lands at /repo/src/pandm/static

# ---- server image
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY --from=web /repo/src/pandm/static src/pandm/static
RUN uv pip install --system --no-cache .

ENV PANDM_DIR=/data
VOLUME /data
EXPOSE 7878
CMD ["pandm", "server", "--host", "0.0.0.0", "--port", "7878"]
