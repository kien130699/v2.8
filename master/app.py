from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from io import BytesIO
import sys
import uuid
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.env_loader import load_project_env, env_status, env_file_info
load_project_env()
STATIC = ROOT / "master" / "static"
MODULES = ROOT / "modules"
PORT = int(os.getenv("V28_PORT", "3000"))
HOST = os.getenv("V28_HOST", "127.0.0.1")
VERSION = "2.8.6.0-anti-stuck-auto-resume"

# Force legacy engines into render-only/in-process mode. V2.8 owns scheduling + Facebook publish.
os.environ.setdefault("UNIFIED_MONOLITH", "1")
os.environ.setdefault("APP_TIMEZONE", "Asia/Ho_Chi_Minh")
os.environ.setdefault("ALLOW_RENDER_WITHOUT_FB_PAGE", "1")
os.environ.setdefault("AUTO_START_FACTORY", "0")
os.environ.setdefault("AUTO_PUBLISH", "0")
os.environ.setdefault("FB_GRAPH_VERSION", "v25.0")
# V2.8 hard isolation: this app owns only HTTP/WS port 3000.
os.environ.setdefault("V28_ISOLATED_FLOW", "1")
os.environ.setdefault("AGENT_PORT", str(PORT))


def load_file_module(name: str, path: Path, search_path: Path | None = None):
    if search_path and str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))
    spec = importlib.util.spec_from_file_location(name, str(path))
    if not spec or not spec.loader:
        raise RuntimeError(f"Không load được module: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_engines() -> tuple[dict[str, FastAPI], dict[str, Any]]:
    modules: dict[str, Any] = {}
    modules["celebrity"] = load_file_module("v28_celebrity_server", MODULES / "facebook" / "server.py", MODULES / "facebook")
    modules["beauty"] = load_file_module("v28_beauty_server", MODULES / "flow_content" / "app.py", MODULES / "flow_content")
    modules["parenting"] = load_file_module("v28_parenting_server", MODULES / "parenting" / "app.py", MODULES / "parenting")
    return {k: v.app for k, v in modules.items()}, modules


ENGINE_APPS, ENGINE_MODULES = load_engines()

from core import db  # noqa: E402
from core import facebook  # noqa: E402
from core import server_features  # noqa: E402
from core.broker import (FlowBroker, load_settings as flow_settings, save_settings as save_flow_settings, IMAGE_MODELS, VIDEO_MODELS)  # noqa: E402
from core.engine import EngineFacade  # noqa: E402
from core.job_manager import JobManager  # noqa: E402

engine = EngineFacade(ENGINE_APPS)
broker = FlowBroker(PORT)
manager = JobManager(engine, broker)
TASKS: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    db.prune_logs(10000)
    safe_env = env_status("9ROUTER_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "SERPER_API_KEY")
    safe_summary = " · ".join(
        f"{k}={'OK' if v.get('configured') else 'MISS'}:{v.get('source')}"
        for k, v in safe_env.items()
    )
    print(f"[V2.8 ENV] {safe_summary}", flush=True)
    db.log_event(f"ENV STATUS {safe_summary}", level="INFO", kind="system")
    manager.load_plugins()
    manager.recover_orphaned_runs()
    async with AsyncExitStack() as stack:
        for name, sub in ENGINE_APPS.items():
            try:
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            except Exception as exc:
                raise RuntimeError(f"Khởi tạo engine {name} lỗi: {exc}") from exc
        # User sees one FB import; silently mirror tokens into legacy engines only for compatibility.
        try:
            await engine.sync_all_fb_pages_to_legacy(facebook.list_pages())
        except Exception as exc:
            db.log_event(f"FB legacy sync startup: {exc}", level="WARNING", kind="facebook")
        TASKS.extend([
            asyncio.create_task(broker.scheduler(), name="v28-flow-broker"),
            asyncio.create_task(broker.source_loop("beauty"), name="v28-bridge-beauty"),
            asyncio.create_task(broker.source_loop("parenting"), name="v28-bridge-parenting"),
            asyncio.create_task(manager.publisher_loop(), name="v28-facebook-publisher"),
            asyncio.create_task(manager.scheduler_loop(), name="v28-job-scheduler"),
            asyncio.create_task(manager.run_worker_loop(1), name="v28-db-run-worker-1"),
            asyncio.create_task(manager.run_worker_loop(2), name="v28-db-run-worker-2"),
            asyncio.create_task(manager.run_worker_loop(3), name="v28-db-run-worker-3"),
        ])
        try:
            yield
        finally:
            await manager.shutdown()
            for t in TASKS:
                t.cancel()
            await asyncio.gather(*TASKS, return_exceptions=True)
            TASKS.clear()


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Facebook Job Factory V2.8",
    version=VERSION,
    lifespan=lifespan,
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon")

@app.middleware("http")
async def no_stale_ui_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/") or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response
# Advanced/debug only. Main UI never requires opening these engines.
app.mount("/engine/celebrity", ENGINE_APPS["celebrity"], name="engine-celebrity")
app.mount("/engine/beauty", ENGINE_APPS["beauty"], name="engine-beauty")
app.mount("/engine/parenting", ENGINE_APPS["parenting"], name="engine-parenting")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")




class ShopeeResearchRequest(StrictModel):
    keyword: str
    count: int = 5


class ShopeeAffiliateRequest(StrictModel):
    links: list[str] = Field(default_factory=list)
    sub_ids: list[str] = Field(default_factory=list)

class InstanceCreate(StrictModel):
    template_id: str
    name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    page_ids: list[str] = Field(default_factory=list)


class InstanceUpdate(StrictModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None
    page_ids: list[str] | None = None


class CloneRequest(StrictModel):
    name: str | None = None


class RunRequest(StrictModel):
    trigger: str = "manual"


class FbImport(StrictModel):
    token: str = Field(min_length=10)


class FbSave(StrictModel):
    page_id: str
    name: str = "Facebook Page"
    access_token: str = Field(min_length=10)


class FlowSettingPatch(StrictModel):
    prioritySource: str | None = None
    imageModel: str | None = None
    videoModel: str | None = None
    imageConcurrency: int | None = None
    videoConcurrency: int | None = None
    aspectRatio: str | None = None
    imageOutputs: str | None = None
    videoDuration: str | None = None
    videoOutputs: str | None = None
    videoExtendFactor: str | None = None
    videoExtendPrompt: str | None = None
    scriptAiProvider: str | None = None
    scriptAiModel: str | None = None
    scriptFallbackModel: str | None = None
    submitPolicy: str | None = None
    maxSubmitsPerMinute: int | None = None
    submitGapMs: int | None = None
    imageTimeoutSec: int | None = None
    videoTimeoutSec: int | None = None
    systemicFailureLimit: int | None = None
    autoDownloadVideo: bool | None = None
    clearComposerBeforeRun: bool | None = None
    clearPromptBeforeRun: bool | None = None
    clearImagesBeforeRun: bool | None = None


class FlowControl(StrictModel):
    action: str





@app.post("/api/flow/control")
async def control_flow_broker(ctrl: FlowControl):
    if broker:
        if ctrl.action.lower() in ("reset", "stop", "clear"):
            return broker.reset_queue()
        return {"ok": True}
    return {"ok": False, "error": "broker not ready"}


@app.post("/api/flow/reset")
async def reset_flow_broker():
    if broker:
        return broker.reset_queue()
    return {"ok": False, "error": "broker not ready"}


@app.post("/api/flow/enqueue")
async def enqueue_flow_task(payload: dict[str, Any]):
    source = payload.get("source") or "beauty"
    if broker:
        broker.enqueue(source, payload)
        return {"ok": True, "jobId": payload.get("jobId")}
    return {"ok": False, "error": "flow_broker not ready"}


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    router_base = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or os.getenv("NINEROUTER_BASE_URL") or "http://127.0.0.1:20128/v1"
    router_key = os.getenv("9ROUTER_API_KEY") or os.getenv("ROUTER9_API_KEY") or os.getenv("NINEROUTER_API_KEY") or ""
    return {
        "ok": True, "version": VERSION, "port": PORT, "pid": os.getpid(), "root": str(ROOT),
        "architecture": "1 process · 3 job engines · 1 Flow extension · 1 Facebook manager",
        "isolation": {
            "server_port": PORT,
            "flow_ws": f"ws://127.0.0.1:{PORT}/ws/flow",
        },
        "ai": {"provider": "9router", "configured": bool(router_key), "base_url": router_base, "direct_provider_fallback": False},
        "env": {
            **env_file_info(),
            "keys": env_status("9ROUTER_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "SERPER_API_KEY"),
        },
    }


@app.get("/api/status")
def status():
    instances = manager.list_instances()
    runs = manager.list_runs(50)
    return {
        "ok": True,
        "version": VERSION,
        "flow": broker.snapshot(),
        "facebook": {"pages": len(facebook.list_pages()), "publish": facebook.list_publish_jobs(30)},
        "jobs": {
            "templates": manager.templates(),
            "instances": instances,
            "active": [r for r in runs if r["status"] in {"queued", "waiting_flow", "dispatching", "preparing", "running", "rendering", "publish_queued", "publishing"}],
        },
    }


# ---------------- Templates / Job instances ----------------
@app.get("/api/job-templates")
def job_templates():
    return manager.templates()


@app.get("/api/jobs")
def jobs():
    return manager.list_instances()


@app.get("/api/jobs/{instance_id}")
def job_detail(instance_id: str):
    item = manager.get_instance(instance_id)
    if not item:
        raise HTTPException(404, "Không thấy Job")
    return item


@app.post("/api/jobs")
def job_create(body: InstanceCreate):
    try:
        return {"ok": True, "job": manager.create_instance(body.template_id, body.name, body.config, body.page_ids)}
    except KeyError:
        raise HTTPException(404, "Không thấy Job Type")
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.patch("/api/jobs/{instance_id}")
def job_update(instance_id: str, body: InstanceUpdate):
    try:
        return {"ok": True, "job": manager.update_instance(instance_id, body.model_dump(exclude_none=True))}
    except KeyError:
        raise HTTPException(404, "Không thấy Job")
    except Exception as exc:
        raise HTTPException(400, str(exc))


ASSET_ROOT = ROOT / "data" / "job_assets"
ASSET_ROOT.mkdir(parents=True, exist_ok=True)
ALLOWED_ASSET_FIELDS = {"persona_path", "persona_left_path", "persona_right_path", "persona_back_path"}
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@app.post("/api/jobs/{instance_id}/assets/{field_name}")
async def job_asset_upload(instance_id: str, field_name: str, file: UploadFile = File(...)):
    job = manager.get_instance(instance_id)
    if not job:
        raise HTTPException(404, "Không thấy Job")
    if field_name not in ALLOWED_ASSET_FIELDS:
        raise HTTPException(400, "Field asset không hợp lệ")
    plugin = manager.plugins.get(str(job.get("template_id") or ""))
    field_spec = (plugin.schema if plugin else {}).get(field_name)
    if not isinstance(field_spec, dict) or str(field_spec.get("type") or "") != "file":
        raise HTTPException(400, f"Job {instance_id} không hỗ trợ asset {field_name}")
    ext = Path(file.filename or "persona.jpg").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, "Persona chỉ nhận JPG/JPEG/PNG/WEBP")
    data = await file.read(30 * 1024 * 1024 + 1)
    if not data or len(data) > 30 * 1024 * 1024:
        raise HTTPException(400, "Ảnh rỗng hoặc lớn hơn 30MB")
    try:
        with Image.open(BytesIO(data)) as im:
            if int(im.width or 0) * int(im.height or 0) > 80_000_000:
                raise HTTPException(400, "Ảnh quá lớn (>80 megapixel)")
            im.verify()
            detected = str(im.format or "").upper()
        if detected not in {"JPEG", "PNG", "WEBP"}:
            raise HTTPException(400, f"Định dạng ảnh không hỗ trợ: {detected or 'unknown'}")
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "File không phải ảnh JPG/PNG/WEBP hợp lệ")
    # Use the detected format, not the user-supplied filename extension.
    canonical_ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[detected]
    folder = ASSET_ROOT / instance_id.replace(".", "_")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{field_name}{canonical_ext}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    for old in folder.glob(f"{field_name}.*"):
        if old != target and old.suffix != ".tmp":
            try:
                old.unlink()
            except OSError:
                pass
    job = manager.update_instance(instance_id, {"config": {field_name: str(target.resolve())}})
    db.log_event(f"Import {field_name}: {target.name}", kind="asset", instance_id=instance_id)
    return {"ok": True, "field": field_name, "path": str(target.resolve()), "size": len(data), "job": job}


@app.get("/api/jobs/{instance_id}/assets/{field_name}")
def job_asset_get(instance_id: str, field_name: str):
    job = manager.get_instance(instance_id)
    if not job:
        raise HTTPException(404, "Không thấy Job")
    if field_name not in ALLOWED_ASSET_FIELDS:
        raise HTTPException(400, "Field asset không hợp lệ")
    raw = str((job.get("config") or {}).get(field_name) or "").strip()
    if not raw:
        raise HTTPException(404, "Chưa import ảnh")
    p = Path(raw).resolve()
    base = ASSET_ROOT.resolve()
    if not p.exists() or not (p == base or base in p.parents):
        raise HTTPException(404, "Ảnh không nằm trong asset V2.8")
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{instance_id}/clone")
def job_clone(instance_id: str, body: CloneRequest):
    try:
        return {"ok": True, "job": manager.clone_instance(instance_id, body.name)}
    except KeyError:
        raise HTTPException(404, "Không thấy Job")
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/jobs/{instance_id}")
def job_delete(instance_id: str):
    try:
        return manager.delete_instance(instance_id)
    except Exception as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/jobs/{instance_id}/run")
async def job_run(instance_id: str, body: RunRequest | None = None):
    try:
        return manager.run_instance(instance_id, trigger=(body.trigger if body else "manual"))
    except KeyError:
        raise HTTPException(404, "Không thấy Job")
    except Exception as exc:
        raise HTTPException(409, str(exc))




@app.post("/api/jobs/run-all")
async def jobs_run_all():
    results = []
    for job in manager.list_instances():
        if not job.get("enabled"):
            continue
        try:
            result = manager.run_instance(str(job["id"]), trigger="test-all-ui")
            results.append({"id": job["id"], "ok": True, **result})
        except Exception as exc:
            results.append({"id": job.get("id"), "ok": False, "error": str(exc)})
    return {"ok": any(x.get("ok") for x in results), "count": len(results), "results": results}


async def _enrich_run_checkpoints(run: dict[str, Any]) -> dict[str, Any]:
    out = dict(run)
    checkpoints = {}
    if str(out.get("template_id") or "") == "3" or str(out.get("engine") or "") == "parenting":
        for job_id in out.get("engine_job_ids") or []:
            try:
                data = await engine.call("parenting", "GET", f"/api/flow/jobs/{job_id}/checkpoints", timeout=30)
                checkpoints[str(job_id)] = data
            except Exception as exc:
                checkpoints[str(job_id)] = {"ok": False, "error": str(exc)}
    out["flow_checkpoints"] = checkpoints
    return out

@app.get("/api/runs")
async def runs(limit: int = 100, instance_id: str | None = None, checkpoints: bool = True):
    rows = manager.list_runs(limit, instance_id)
    if checkpoints:
        rows = [await _enrich_run_checkpoints(r) for r in rows]
    return rows

@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str, checkpoints: bool = True):
    run = manager.get_run(run_id)
    if not run:
        raise HTTPException(404, "Khong thay run")
    return await _enrich_run_checkpoints(run) if checkpoints else run

@app.post("/api/flow/jobs/{job_id}/resume")
async def master_flow_job_resume(job_id: str):
    return await engine.call("parenting", "POST", f"/api/flow/jobs/{job_id}/resume", timeout=60)

@app.post("/api/flow/jobs/{job_id}/scenes/{scene_id}/retry")
async def master_flow_scene_retry(job_id: str, scene_id: int):
    return await engine.call("parenting", "POST", f"/api/flow/jobs/{job_id}/scenes/{scene_id}/retry", timeout=60)

@app.post("/api/runs/{run_id}/cancel")
def run_cancel(run_id: str):
    try:
        return manager.cancel_run(run_id)
    except KeyError:
        raise HTTPException(404, "Không thấy run")
    except Exception as exc:
        raise HTTPException(409, str(exc))


def _safe_video_path(run: dict[str, Any], index: int) -> Path:
    paths = [Path(str(x)).resolve() for x in (run.get("output") or {}).get("video_paths") or []]
    if index < 0 or index >= len(paths):
        raise HTTPException(404, "Video index không tồn tại")
    p = paths[index]
    allowed = [
        (ROOT / "modules").resolve(),
        (ROOT / "data").resolve(),
    ]
    if not p.exists() or not any(a == p or a in p.parents for a in allowed):
        raise HTTPException(404, "Video không tồn tại hoặc nằm ngoài output V2.8")
    return p


@app.get("/api/runs/{run_id}/video")
def run_video(run_id: str, index: int = 0):
    run = manager.get_run(run_id)
    if not run:
        raise HTTPException(404, "Không thấy run")
    p = _safe_video_path(run, index)
    return FileResponse(p, media_type="video/mp4", headers={"Content-Disposition": "inline", "Cache-Control": "no-store"})


# ---------------- Facebook: ONE import for all jobs ----------------
@app.get("/api/facebook/pages")
def fb_pages():
    return facebook.list_pages()


@app.post("/api/facebook/import")
async def fb_import(body: FbImport):
    try:
        result = await asyncio.to_thread(facebook.import_token, body.token)
        result["legacy_sync"] = await engine.sync_all_fb_pages_to_legacy(facebook.list_pages())
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/facebook/pages")
async def fb_save(body: FbSave):
    try:
        facebook.save_page(body.page_id.strip(), body.name.strip(), body.access_token.strip(), [])
        sync = await engine.sync_fb_page_to_legacy(body.page_id.strip(), body.name.strip(), body.access_token.strip())
        return {"ok": True, "pages": facebook.list_pages(), "legacy_sync": sync}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/facebook/pages/{page_id}/test")
async def fb_test(page_id: str):
    try:
        return await asyncio.to_thread(facebook.test_page, page_id)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/facebook/pages/{page_id}")
async def fb_delete(page_id: str):
    result = facebook.delete_page(page_id)
    result["legacy_sync"] = await engine.remove_fb_page_from_legacy(page_id)
    return result


@app.post("/api/facebook/pages/test-all")
async def fb_test_all():
    pages = facebook.list_pages()
    results = {}
    for p in pages:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        try:
            res = await asyncio.to_thread(facebook.test_page, pid)
            results[pid] = res
        except Exception as exc:
            results[pid] = {"ok": False, "error": str(exc)}
    return {"ok": True, "tested": len(results), "results": results}


@app.post("/api/system/prune-media")
async def system_prune_media(days: int = 7):
    import time
    cutoff = time.time() - (max(1, days) * 86400)
    deleted = 0
    reclaimed_bytes = 0
    search_dirs = [
        ROOT / "data" / "downloads",
        ROOT / "modules" / "parenting" / "outputs" / "flow_downloads",
        ROOT / "modules" / "flow_content" / "outputs" / "flow_downloads"
    ]
    for root_dir in search_dirs:
        if not root_dir.exists():
            continue
        for f in root_dir.rglob("*.mp4"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    sz = f.stat().st_size
                    f.unlink()
                    deleted += 1
                    reclaimed_bytes += sz
            except Exception:
                pass
    return {"ok": True, "deleted_files": deleted, "reclaimed_mb": round(reclaimed_bytes / (1024 * 1024), 2)}


@app.get("/api/facebook/publish-jobs")
def fb_publish_jobs(limit: int = 200):
    return facebook.list_publish_jobs(limit)


# ---------------- Flow: ONE extension / central queue ----------------
@app.websocket("/ws/flow")
async def flow_ws(ws: WebSocket):
    await broker.attach_extension(ws)


@app.websocket("/ws/gpt")
async def obsolete_gpt_ws(ws: WebSocket):
    # V2.8 does not use a GPT worker. Accept+close gives old extensions a useful reason
    # instead of noisy 403 reconnect spam in the server console.
    await ws.accept()
    await ws.close(code=4009, reason="V2.8 does not use GPT_WORKER; disable the old GPT extension")





@app.get("/api/shopee/session-health")
async def shopee_session_health():
    return await broker.check_shopee_session_health()


@app.post("/api/shopee/research")
async def shopee_research(req: ShopeeResearchRequest):
    keyword = (req.keyword or "").strip()
    if not keyword:
        raise HTTPException(400, "Thiếu keyword Shopee")
    count = max(1, min(20, int(req.count or 5)))
    return await engine.call(
        "parenting", "POST", "/api/parenting/shopee/search-preview",
        {"keywords": [keyword], "count": count, "content_pillar": "mixed", "affiliate_id": ""},
        timeout=240,
    )


@app.post("/api/shopee/affiliate/convert")
async def shopee_affiliate_convert(req: ShopeeAffiliateRequest):
    links = [str(x).strip() for x in (req.links or []) if str(x).strip()]
    if not links:
        raise HTTPException(400, "Thiếu link Shopee")
    if len(links) > 5:
        raise HTTPException(400, "Mỗi lần chỉ đổi 1–5 link")
    bad = [x for x in links if not (x.startswith("https://shopee.vn/") or x.startswith("https://www.shopee.vn/") or x.startswith("https://shope.ee/"))]
    if bad:
        raise HTTPException(400, "Chỉ nhận link Shopee/shope.ee HTTPS")
    cached_items = []
    missing_links = []
    for origin in links:
        cached = server_features.get_affiliate(origin)
        if cached:
            cached_items.append({"origin_url": origin, "affiliate_url": cached, "cached": True})
        else:
            missing_links.append(origin)
    if not missing_links:
        return {"ok": True, "items": cached_items, "found": [x["affiliate_url"] for x in cached_items], "cached": True}
    sub_ids = [re.sub(r"[^a-zA-Z0-9]", "", str(x or ""))[:50] for x in (req.sub_ids or []) if re.sub(r"[^a-zA-Z0-9]", "", str(x or ""))][:5]
    async def call_convert_once() -> dict[str, Any]:
        return await broker.request_extension({
            "type": "SHOPEE_AFFILIATE_CONVERT",
            "requestId": "aff_" + uuid.uuid4().hex[:16],
            "links": missing_links,
            "subIds": sub_ids,
        }, timeout=120)
    try:
        res = await call_convert_once()
    except Exception as exc:
        text = str(exc)
        if "No tab with id" in text or "Cannot access" in text:
            try:
                res = await call_convert_once()
            except Exception as exc2:
                raise HTTPException(502, f"Đổi affiliate lỗi: {exc2}")
        else:
            raise HTTPException(502, f"Đổi affiliate lỗi: {exc}")
    if not res.get("ok"):
        raise HTTPException(502, str(res.get("error") or "Đổi affiliate lỗi"))
    items = cached_items + list(res.get("items") or [])
    for item in items:
        server_features.set_affiliate(str(item.get("origin_url") or ""), str(item.get("affiliate_url") or ""), "api")
    res["items"] = items
    res["found"] = [str(x.get("affiliate_url") or "") for x in items if str(x.get("affiliate_url") or "")]
    return res


@app.post("/api/shopee/affiliate/diag")
async def shopee_affiliate_diag():
    try:
        res = await broker.request_extension({
            "type": "SHOPEE_AFFILIATE_DIAG",
            "requestId": "affdiag_" + uuid.uuid4().hex[:16],
        }, timeout=60)
    except Exception as exc:
        raise HTTPException(502, f"Diag affiliate lỗi: {exc}")
    if not res.get("ok"):
        raise HTTPException(502, str(res.get("error") or "Diag affiliate lỗi"))
    return res


@app.get("/api/shopee/session-health")
async def shopee_session_health():
    try:
        res = await broker.request_extension({
            "type": "SHOPEE_AFFILIATE_DIAG",
            "requestId": "affhealth_" + uuid.uuid4().hex[:16],
        }, timeout=60)
    except Exception as exc:
        return {"ok": False, "ready": False, "error": str(exc)}
    diag = res.get("diag") or {}
    body = str(diag.get("bodySample") or "")
    ready = bool(res.get("ok") and "Custom Link" in body and ("L?y link" in body or "L\u1ea5y link" in body))
    return {"ok": bool(res.get("ok")), "ready": ready, "risk": bool(diag.get("risk")), "url": diag.get("url"), "title": diag.get("title"), "boxes": diag.get("boxes", [])[:6]}

@app.delete("/api/runs")
def clear_all_runs_and_media():
    with db.connect() as c:
        for t in ["runs", "run_steps", "scene_checkpoints", "publish_jobs"]:
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        c.commit()
    for d in [ROOT / "modules" / "facebook" / "engine_v27" / "work",
              ROOT / "modules" / "facebook" / "engine_v27" / "output",
              ROOT / "modules" / "facebook" / "engine_v27" / "input" / "celebrity",
              ROOT / "data" / "FlowAutomationServer",
              ROOT / "data" / "FlowPairAuto"]:
        if d.exists():
            for item in d.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                except Exception:
                    pass
    return {"ok": True, "message": "Đã xóa toàn bộ jobs và media caches."}


@app.get("/api/flow")
def flow_status():
    return {
        "queue": broker.snapshot(),
        "settings": flow_settings(),
        "models": {"image": IMAGE_MODELS, "video": VIDEO_MODELS},
    }


@app.patch("/api/flow/settings")
def flow_patch(body: FlowSettingPatch):
    return save_flow_settings(body.model_dump(exclude_none=True))


@app.post("/api/flow/control")
async def flow_control(body: FlowControl):
    return await broker.control(body.action)


@app.get("/api/diagnostics")
def diagnostics():
    flow = broker.snapshot()
    return {
        "ok": True,
        "version": VERSION,
        "db": {"path": str(db.DB_PATH), "exists": db.DB_PATH.exists()},
        "workers": [{"name": t.get_name(), "done": t.done(), "cancelled": t.cancelled()} for t in TASKS],
        "flow": {
            "extensionConnected": flow.get("extensionConnected"),
            "extension": flow.get("extension"),
            "sources": flow.get("sources"),
            "pending": len(flow.get("pending") or []),
            "active": flow.get("active"),
        },
        "queuedRuns": len(db.rows("SELECT id FROM runs WHERE status IN ('queued','waiting_flow','dispatching','preparing','running','rendering')")),
    }


@app.get("/api/logs")
def logs(limit: int = 500, kind: str | None = None):
    return db.list_logs(limit=limit, kind=kind)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    event_id = f"err_{os.urandom(4).hex()}"
    db.log_event(f"{event_id} Unhandled: {type(exc).__name__}: {exc}", level="ERROR", kind="system")
    return JSONResponse(status_code=500, content={"detail": f"Internal error ({event_id}). Xem Logs để biết chi tiết."})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("master.app:app", host=HOST, port=PORT, log_level="info", reload=False)
