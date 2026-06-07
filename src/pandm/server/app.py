"""FastAPI app: read API for the dashboard + ingest API for cloud mode.

The same app powers `pandm ui` (local) and `pandm server` (cloud). When an
API key is configured, write endpoints require the `x-api-key` header.
"""

from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..storage import LocalStore, new_run_id, resolve_dir

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


class MetricRowsIn(BaseModel):
    rows: list[MetricRow]


class FinishIn(BaseModel):
    status: str = "finished"
    finished_at: float | None = None


def create_app(data_dir: str | os.PathLike | None = None, api_key: str | None = None) -> FastAPI:
    store = LocalStore(resolve_dir(data_dir))
    app = FastAPI(title="pandm", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if api_key and x_api_key != api_key:
            raise HTTPException(status_code=401, detail="invalid or missing x-api-key")

    # --------------------------------------------------------- read API

    @app.get("/api/projects")
    def projects() -> list[dict[str, Any]]:
        return store.list_projects()

    @app.get("/api/runs")
    def runs(project: str | None = None) -> list[dict[str, Any]]:
        return store.list_runs(project)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/runs/{run_id}/metrics")
    def run_metric_keys(run_id: str) -> list[dict[str, Any]]:
        return store.metric_keys(run_id)

    @app.get("/api/runs/{run_id}/metrics/{key:path}")
    def run_metric_series(run_id: str, key: str, max_points: int = 1500) -> dict[str, list]:
        return store.metric_series(run_id, key, max_points=max_points)

    @app.get("/api/runs/{run_id}/media")
    def run_media(run_id: str, key: str | None = None) -> list[dict[str, Any]]:
        items = store.list_media(run_id, key)
        for item in items:
            item["url"] = f"/api/media/{run_id}/{item['filename']}"
        return items

    @app.get("/api/media/{run_id}/{filename}")
    def media_file(run_id: str, filename: str):
        path = store.media_path(run_id, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="file not found")
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    # ------------------------------------------------------- ingest API

    @app.post("/api/runs", dependencies=[Depends(require_key)])
    def create_run(body: RunIn) -> dict[str, Any]:
        run_id = body.id or new_run_id()
        store.create_run(run_id, body.project, body.name, body.config, body.created_at)
        return {"id": run_id}

    @app.post("/api/runs/{run_id}/metrics", dependencies=[Depends(require_key)])
    def ingest_metrics(run_id: str, body: MetricRowsIn) -> dict[str, int]:
        store.log_metrics(run_id, [(r.key, r.step, r.value, r.ts) for r in body.rows])
        return {"inserted": len(body.rows)}

    @app.post("/api/runs/{run_id}/media", dependencies=[Depends(require_key)])
    def ingest_media(
        run_id: str,
        file: UploadFile = File(...),
        key: str = Form(...),
        step: int = Form(0),
        caption: str = Form(""),
        ts: float = Form(default=None),
    ) -> dict[str, str]:
        ext = Path(file.filename or "upload.png").suffix.lower() or ".png"
        filename = store.log_media(
            run_id, key, step, file.file.read(), ext, caption or None, ts if ts is not None else time.time()
        )
        return {"filename": filename}

    @app.post("/api/runs/{run_id}/heartbeat", dependencies=[Depends(require_key)])
    def run_heartbeat(run_id: str) -> dict[str, bool]:
        store.heartbeat(run_id)  # server clock — immune to client clock skew
        return {"ok": True}

    @app.post("/api/runs/{run_id}/finish", dependencies=[Depends(require_key)])
    def finish_run(run_id: str, body: FinishIn) -> dict[str, str]:
        store.finish_run(run_id, body.status, body.finished_at)
        return {"status": body.status}

    @app.delete("/api/runs/{run_id}", dependencies=[Depends(require_key)])
    def delete_run(run_id: str) -> dict[str, bool]:
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
