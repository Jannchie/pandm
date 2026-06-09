"""FastAPI app: read API for the dashboard + ingest API for cloud mode.

The same app powers `pandm ui` (local), `pandm server --api-key` (single-key
cloud) and the multi-user cloud mode. Multi-user mode turns on when
GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET are set: GitHub OAuth sign-in,
per-user API keys, and every run scoped to its owner.
"""

from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..storage import LocalStore, new_run_id, resolve_dir
from .auth import AuthContext, github_oauth_config, load_secret, register_auth_routes

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class RunIn(BaseModel):
    id: str | None = None
    project: str = "default"
    name: str = "unnamed"
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: float | None = None


class MetricRow(BaseModel):
    key: str
    step: int
    value: float
    ts: float
    seq: int | None = None  # client-local rowid; enables idempotent re-push


class MetricRowsIn(BaseModel):
    rows: list[MetricRow]


class ProgressIn(BaseModel):
    current: float
    total: float | None = None
    ts: float | None = None


class FinishIn(BaseModel):
    status: str = "finished"
    finished_at: float | None = None
    summary: dict[str, Any] | None = None  # author scalars, sent with the run's terminal state


def create_app(data_dir: str | os.PathLike | None = None, api_key: str | None = None) -> FastAPI:
    root = resolve_dir(data_dir)
    store = LocalStore(root)
    app = FastAPI(title="pandm", docs_url="/api/docs", openapi_url="/api/openapi.json")

    oauth = github_oauth_config()
    ctx = AuthContext(store, load_secret(root), *oauth) if oauth else None

    if ctx is None:
        # single-origin isn't guaranteed in ad-hoc local setups; harmless without cookies
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/api/me")
        def me_local() -> dict[str, str]:
            return {"mode": "local"}

    else:
        register_auth_routes(app, ctx)

    def current_user(request: Request) -> dict[str, Any] | None:
        """Resolved identity in multi-user mode; None in local / single-key mode."""
        return ctx.require_user(request) if ctx else None

    def require_key(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any] | None:
        """Write guard: per-user identity in multi-user mode, global key otherwise."""
        if ctx:
            return ctx.require_user(request)
        if api_key and x_api_key != api_key:
            raise HTTPException(status_code=401, detail="invalid or missing x-api-key")
        return None

    def check_owner(run_id: str, user: dict[str, Any] | None) -> None:
        """In multi-user mode a foreign run is indistinguishable from a missing one."""
        if user is not None and store.run_owner(run_id) != user["id"]:
            raise HTTPException(status_code=404, detail="run not found")

    # --------------------------------------------------------- read API

    @app.get("/api/projects")
    def projects(user: dict | None = Depends(current_user)) -> list[dict[str, Any]]:
        return store.list_projects(user_id=user["id"] if user else None)

    @app.get("/api/runs")
    def runs(project: str | None = None, user: dict | None = Depends(current_user)) -> list[dict[str, Any]]:
        return store.list_runs(project, user_id=user["id"] if user else None)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str, user: dict | None = Depends(current_user)) -> dict[str, Any]:
        run = store.get_run(run_id, user_id=user["id"] if user else None)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/runs/{run_id}/metrics")
    def run_metric_keys(run_id: str, user: dict | None = Depends(current_user)) -> list[dict[str, Any]]:
        check_owner(run_id, user)
        return store.metric_keys(run_id)

    @app.get("/api/runs/{run_id}/metrics/{key:path}")
    def run_metric_series(
        run_id: str,
        key: str,
        max_points: int = 1500,
        after_step: int | None = None,
        user: dict | None = Depends(current_user),
    ) -> dict[str, list]:
        check_owner(run_id, user)
        return store.metric_series(run_id, key, max_points=max_points, after_step=after_step)

    @app.get("/api/runs/{run_id}/media")
    def run_media(
        run_id: str, key: str | None = None, user: dict | None = Depends(current_user)
    ) -> list[dict[str, Any]]:
        check_owner(run_id, user)
        items = store.list_media(run_id, key)
        for item in items:
            item["url"] = f"/api/media/{run_id}/{item['filename']}"
        return items

    @app.get("/api/media/{run_id}/{filename}")
    def media_file(run_id: str, filename: str, user: dict | None = Depends(current_user)):
        check_owner(run_id, user)
        path = store.media_path(run_id, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="file not found")
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    # ------------------------------------------------------- ingest API

    @app.post("/api/runs")
    def create_run(body: RunIn, user: dict | None = Depends(require_key)) -> dict[str, Any]:
        run_id = body.id or new_run_id()
        store.create_run(
            run_id, body.project, body.name, body.config, body.created_at,
            user_id=user["id"] if user else None,
        )
        return {"id": run_id}

    @app.post("/api/runs/{run_id}/metrics")
    def ingest_metrics(
        run_id: str, body: MetricRowsIn, user: dict | None = Depends(require_key)
    ) -> dict[str, int]:
        check_owner(run_id, user)
        if body.rows and all(r.seq is not None for r in body.rows):
            inserted = store.log_metrics_seq(
                run_id, [(r.key, r.step, r.value, r.ts, r.seq) for r in body.rows]  # type: ignore[misc]
            )
        else:
            store.log_metrics(run_id, [(r.key, r.step, r.value, r.ts) for r in body.rows])
            inserted = len(body.rows)
        return {"inserted": inserted}

    @app.post("/api/runs/{run_id}/media")
    def ingest_media(
        run_id: str,
        file: UploadFile = File(...),
        key: str = Form(...),
        step: int = Form(0),
        caption: str = Form(""),
        ts: float = Form(default=None),
        media_seq: int = Form(default=None),
        user: dict | None = Depends(require_key),
    ) -> dict[str, Any]:
        check_owner(run_id, user)
        if media_seq is not None and not store.claim_media_seq(run_id, media_seq):
            return {"filename": None, "skipped": True}  # replay of an already-ingested upload
        ext = Path(file.filename or "upload.png").suffix.lower() or ".png"
        filename = store.log_media(
            run_id, key, step, file.file.read(), ext, caption or None, ts if ts is not None else time.time()
        )
        return {"filename": filename}

    @app.post("/api/runs/{run_id}/heartbeat")
    def run_heartbeat(run_id: str, user: dict | None = Depends(require_key)) -> dict[str, bool]:
        check_owner(run_id, user)
        store.heartbeat(run_id)  # server clock — immune to client clock skew
        return {"ok": True}

    @app.post("/api/runs/{run_id}/progress")
    def run_progress(
        run_id: str, body: ProgressIn, user: dict | None = Depends(require_key)
    ) -> dict[str, bool]:
        check_owner(run_id, user)
        store.update_progress(run_id, body.current, body.total, body.ts)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/finish")
    def finish_run(run_id: str, body: FinishIn, user: dict | None = Depends(require_key)) -> dict[str, str]:
        check_owner(run_id, user)
        if body.summary:
            store.set_summary(run_id, body.summary)
        store.finish_run(run_id, body.status, body.finished_at)
        return {"status": body.status}

    @app.post("/api/runs/{run_id}/resume")
    def resume_run(run_id: str, user: dict | None = Depends(require_key)) -> dict[str, int]:
        check_owner(run_id, user)
        return {"max_step": store.resume_run(run_id)}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str, user: dict | None = Depends(require_key)) -> dict[str, bool]:
        check_owner(run_id, user)
        store.delete_run(run_id)
        return {"deleted": True}

    # ------------------------------------------------------- dashboard

    if (STATIC_DIR / "index.html").is_file():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
    else:

        @app.get("/")
        def no_dashboard() -> JSONResponse:
            return JSONResponse(
                {
                    "message": "pandm API is running, but the dashboard is not built.",
                    "hint": "run `pnpm install && pnpm build` inside web/ to build it",
                    "api_docs": "/api/docs",
                }
            )

    return app
