from __future__ import annotations

import json
import mimetypes
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from facebook_app.config import settings
from facebook_app.db import get_setting, init_db, set_setting
from facebook_app.events import events
from facebook_app.repository import dashboard, get_job, get_page, job_logs, list_jobs, list_pages, retry_job, update_page
from facebook_app.services.facebook import facebook
from facebook_app.services.factory import factory
from facebook_app.services.llm_router import llm_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await factory.boot()
    yield
    await factory.shutdown()


app = FastAPI(title="Facebook Content Factory", version="3.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=settings.root / "web"), name="static")


class PageUpdate(BaseModel):
    name: str | None = None
    page_id: str | None = None
    token_env_key: str | None = None
    enabled: bool | None = None
    posts_per_day: int | None = None
    slot1: str | None = None
    slot2: str | None = None
    theme: str | None = None
    celebrity_pool: list[str] | None = None
    output_mode: str | None = None


class SettingsUpdate(BaseModel):
    auto_publish: bool | None = None
    daily_target: int | None = None
    selected_llm_model: str | None = None


class LLMTestRequest(BaseModel):
    model: str | None = None


class TestVideoRequest(BaseModel):
    page_row_id: int | None = None
    model: str | None = None
    v28_config: dict[str, Any] | None = None


@app.get("/")
def index():
    return FileResponse(settings.root / "web" / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": "3.1.0",
        "time": datetime.now(settings.tz).isoformat(timespec="seconds"),
        "timezone": settings.timezone_name,
        "engine_dir": str(settings.video_engine_dir),
        "engine_exists": (settings.video_engine_dir / "app.py").exists(),
    }


@app.get("/api/dashboard")
def api_dashboard():
    return dashboard()


@app.post("/api/factory/start")
async def factory_start():
    await factory.start()
    return {"ok": True, "state": "RUNNING"}


@app.post("/api/factory/pause")
async def factory_pause():
    await factory.pause()
    return {"ok": True, "state": "PAUSED"}


@app.post("/api/factory/stop")
async def factory_stop():
    await factory.stop()
    return {"ok": True, "state": "STOPPED"}


@app.post("/api/factory/plan-today")
async def plan_today():
    count = await factory.ensure_today_plan()
    return {"ok": True, "count": count}


@app.get("/api/pages")
def pages():
    return list_pages()


@app.patch("/api/pages/{page_row_id}")
def page_update(page_row_id: int, body: PageUpdate):
    if not get_page(page_row_id):
        raise HTTPException(404, "Page not found")
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    update_page(page_row_id, payload)
    return get_page(page_row_id)


@app.post("/api/pages/{page_row_id}/test-facebook")
def page_test_facebook(page_row_id: int):
    page = get_page(page_row_id)
    if not page:
        raise HTTPException(404, "Page not found")
    return facebook.test_page(page)


@app.get("/api/jobs")
def jobs(date: str | None = None, limit: int = 200):
    return list_jobs(date, min(max(limit, 1), 500))


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    data = get_job(job_id)
    if not data:
        raise HTTPException(404, "Job not found")
    data["logs"] = job_logs(job_id, 500)
    return data


@app.post("/api/jobs/{job_id}/retry")
def job_retry(job_id: str):
    if not get_job(job_id):
        raise HTTPException(404, "Job not found")
    retry_job(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str):
    data = get_job(job_id)
    if not data or not data.get("output_path"):
        raise HTTPException(404, "Video not found")
    p = Path(data["output_path"]).resolve()
    out_root = settings.output_dir.resolve()
    if out_root not in p.parents or not p.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(p, media_type="video/mp4", filename=f"{job_id}.mp4")


@app.get("/api/settings")
def api_settings():
    return {
        "auto_publish": get_setting("auto_publish", "0") == "1",
        "daily_target": int(get_setting("daily_target", "20")),
        "factory_state": get_setting("factory_state", "STOPPED"),
        "timezone": settings.timezone_name,
        "server_host": settings.host,
        "server_port": settings.port,
        "fb_graph_version": settings.fb_graph_version or "app-default",
        "llm_configured": llm_router.configured(),
        "llm_provider": llm_router.provider_name(),
        "selected_llm_model": get_setting("llm_selected_model", llm_router.default_model()),
        "ninerouter_configured": bool(settings.ninerouter_api_key),
        "ninerouter_base_url": settings.ninerouter_base_url,
        "video_engine_dir": str(settings.video_engine_dir),
    }


@app.patch("/api/settings")
def api_settings_update(body: SettingsUpdate):
    if body.auto_publish is not None:
        set_setting("auto_publish", "1" if body.auto_publish else "0")
    if body.daily_target is not None:
        set_setting("daily_target", str(max(1, min(body.daily_target, 200))))
    if body.selected_llm_model is not None:
        set_setting("llm_selected_model", body.selected_llm_model.strip())
    return api_settings()


@app.get("/api/llm/models")
def api_llm_models(live: bool = True):
    result: dict[str, Any] = {
        "configured": llm_router.configured(),
        "provider": llm_router.provider_name(),
        "selected": get_setting("llm_selected_model", llm_router.default_model()),
        "presets": llm_router.presets(),
        "live": [],
        "error": "",
    }
    if live and llm_router.configured():
        try:
            result["live"] = llm_router.live_models()
        except Exception as exc:
            result["error"] = str(exc)
    return result


@app.post("/api/llm/test")
def api_llm_test(body: LLMTestRequest):
    try:
        return llm_router.test(body.model)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/test-video")
async def api_test_video(body: TestVideoRequest):
    try:
        return await factory.create_test_video(body.page_row_id, body.model, body.v28_config)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        async for event in events.subscribe():
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
