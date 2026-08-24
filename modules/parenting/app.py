from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def _configure_utf8_stdio() -> None:
    """Keep Windows CMD/PowerShell codepages from crashing on Vietnamese log text."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_utf8_stdio()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
STATIC_DIR = ROOT / "static"
DB_PATH = DATA_DIR / "factory.sqlite3"
ENV_PATH = ROOT / ".env"

for p in [DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, STATIC_DIR]:
    p.mkdir(parents=True, exist_ok=True)

load_dotenv(ENV_PATH)
HOST = os.getenv("V28_HOST", "127.0.0.1") if os.getenv("V28_ISOLATED_FLOW") == "1" else os.getenv("HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("V28_PORT", "3000")) if os.getenv("V28_ISOLATED_FLOW") == "1" else int(os.getenv("WEB_PORT", "8997"))
WEB_PORT_FALLBACK_START = int(os.getenv("WEB_PORT_FALLBACK_START", "8997"))
WEB_PORT_FALLBACK_END = int(os.getenv("WEB_PORT_FALLBACK_END", "9010"))
# Legacy V1 used PORT=8786. In V1.1 that legacy value belongs to the extension bridge, not the UI.
AGENT_PORT = int(os.getenv("V28_PORT", "3000")) if os.getenv("V28_ISOLATED_FLOW") == "1" else int(os.getenv("AGENT_PORT", os.getenv("PORT", "8787")))
SERVER_VERSION = "4.5.0"
APP_NAME = os.getenv("APP_NAME", "Parenting Content Factory V4.5 · Strict Video Identity + Deterministic Recovery")
HTTP_ACCESS_LOG = os.getenv("HTTP_ACCESS_LOG", "0").strip().lower() in {"1", "true", "yes", "on"}
PARENTING_AGENT_MIN_VERSION = (14, 6, 4)
FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v25.0").strip() or "v25.0"
AUTO_CACHE_FLOW_IMAGES = os.getenv("AUTO_CACHE_FLOW_IMAGES", "1").strip() not in {"0", "false", "False"}
FLOW_IMAGE_CACHE_TIMEOUT = int(os.getenv("FLOW_IMAGE_CACHE_TIMEOUT", "60"))
FLOW_VIDEO_DOWNLOAD_CONNECT_TIMEOUT = max(5, int(os.getenv("FLOW_VIDEO_DOWNLOAD_CONNECT_TIMEOUT", "15") or 15))
FLOW_VIDEO_DOWNLOAD_READ_TIMEOUT = max(20, int(os.getenv("FLOW_VIDEO_DOWNLOAD_READ_TIMEOUT", "120") or 120))
FLOW_VIDEO_DOWNLOAD_CHUNK_TIMEOUT = max(10, int(os.getenv("FLOW_VIDEO_DOWNLOAD_CHUNK_TIMEOUT", "45") or 45))
FLOW_VIDEO_DOWNLOAD_RETRIES = max(1, min(8, int(os.getenv("FLOW_VIDEO_DOWNLOAD_RETRIES", "4") or 4)))
FLOW_VIDEO_DOWNLOAD_MAX_MB = max(64, int(os.getenv("FLOW_VIDEO_DOWNLOAD_MAX_MB", "2048") or 2048))
FLOW_VIDEO_SIGNED_URL_CACHE_MINUTES = max(1, int(os.getenv("FLOW_VIDEO_SIGNED_URL_CACHE_MINUTES", "20") or 20))
# V4.5 deterministic recovery state machine:
# one resolver cycle already contains several extension-level probes/retries.  If that
# cycle cannot verify a generated mediaId, do NOT loop the same ID forever: mark the
# whole scene video chain unusable and regenerate from the preserved scene image.
FLOW_VIDEO_MEDIA_RESOLVE_CYCLES = max(1, min(3, int(os.getenv("FLOW_VIDEO_MEDIA_RESOLVE_CYCLES", "1") or 1)))
FLOW_VIDEO_MEDIA_MAX_REGENERATIONS = max(1, min(5, int(os.getenv("FLOW_VIDEO_MEDIA_MAX_REGENERATIONS", "2") or 2)))
FLOW_VIDEO_MIN_VALID_BYTES = max(1024, int(os.getenv("FLOW_VIDEO_MIN_VALID_BYTES", "4096") or 4096))
FLOW_VIDEO_MIN_VALID_DURATION = max(0.25, float(os.getenv("FLOW_VIDEO_MIN_VALID_DURATION", "1.0") or 1.0))
VIDEO_PROBE_CACHE: dict[str, tuple[int, int, bool, str]] = {}
FACEBOOK_DEFAULT_DRY_RUN = os.getenv("FACEBOOK_DEFAULT_DRY_RUN", "1").strip() not in {"0", "false", "False"}
ROUTER9_BASE_URL = (os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or os.getenv("NINE_ROUTER_BASE_URL") or "http://127.0.0.1:20128/v1").rstrip("/")
ROUTER9_API_KEY = (os.getenv("9ROUTER_API_KEY") or os.getenv("ROUTER9_API_KEY") or os.getenv("NINE_ROUTER_API_KEY") or os.getenv("ROUTER_API_KEY") or "").strip()
ROUTER9_DEFAULT_MODEL = (os.getenv("9ROUTER_DEFAULT_MODEL") or os.getenv("ROUTER9_DEFAULT_MODEL") or "").strip()
ROUTER9_TIMEOUT = int(os.getenv("ROUTER9_TIMEOUT", "120"))
AUTO_FB_CANDIDATE_CHUNK_SIZE = max(1, min(2, int(os.getenv("AUTO_FB_CANDIDATE_CHUNK_SIZE", "2") or 2)))
AUTO_FB_CANDIDATE_CHUNK_RETRIES = max(1, min(2, int(os.getenv("AUTO_FB_CANDIDATE_CHUNK_RETRIES", "2") or 2)))
AUTO_FB_CANDIDATE_CHUNK_TIMEOUT = max(210, min(300, int(os.getenv("AUTO_FB_CANDIDATE_CHUNK_TIMEOUT", "210") or 210)))
AUTO_FB_CANDIDATE_RETRY_BACKOFF = max(0.5, min(15.0, float(os.getenv("AUTO_FB_CANDIDATE_RETRY_BACKOFF", "2") or 2)))
AUTO_FB_EDITOR_CHUNK_SIZE = max(8, min(10, int(os.getenv("AUTO_FB_EDITOR_CHUNK_SIZE", "10") or 10)))
AUTO_FB_EDITOR_TIMEOUT = max(60, min(300, int(os.getenv("AUTO_FB_EDITOR_TIMEOUT", "210") or 210)))
SCHEDULER_TZ_NAME = os.getenv("SCHEDULER_TZ", "Asia/Ho_Chi_Minh")
try:
    SCHEDULER_TZ = ZoneInfo(SCHEDULER_TZ_NAME)
except (ZoneInfoNotFoundError, Exception) as exc:
    # Windows Python may not ship the IANA timezone database. tzdata is installed
    # by requirements.txt in V2.10.1, but never crash the whole server if it is missing.
    print(f"[TIMEZONE] Không load được {SCHEDULER_TZ_NAME}: {exc} -> fallback UTC+07:00")
    SCHEDULER_TZ = timezone(timedelta(hours=7))



def _port_is_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _select_web_port() -> int:
    """Pick the configured UI port, otherwise the first free fallback port.

    Important for drop-in upgrades: an old .env may still contain WEB_PORT=8797.
    If that port is occupied, V2.0 automatically moves to 8997..9010.
    """
    preferred = int(WEB_PORT)
    if preferred != AGENT_PORT and _port_is_free(HOST, preferred):
        return preferred

    for port in range(WEB_PORT_FALLBACK_START, WEB_PORT_FALLBACK_END + 1):
        if port == AGENT_PORT:
            continue
        if _port_is_free(HOST, port):
            if port != preferred:
                print(f"[WEB PORT] {HOST}:{preferred} đang bận -> tự chuyển sang {HOST}:{port}")
            return port

    raise RuntimeError(
        f"Không tìm được cổng web trống. Đã thử {preferred} và "
        f"{WEB_PORT_FALLBACK_START}-{WEB_PORT_FALLBACK_END}. "
        "Hãy đặt WEB_PORT khác trong .env."
    )


def utcnow() -> str:
    """UTC timestamp for DB/state math. Keep internal persistence stable."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def server_now() -> str:
    """User-facing server time in Viet Nam (UTC+7)."""
    return datetime.now(SCHEDULER_TZ).isoformat(timespec="seconds")


def to_server_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        d=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if d.tzinfo is None:
            d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(SCHEDULER_TZ).isoformat(timespec="seconds")
    except Exception:
        return value




def _install_windows_asyncio_exception_filter(loop: asyncio.AbstractEventLoop) -> None:
    """Suppress only the harmless Windows Proactor WinError 10054 close callback.

    Chrome/extension WebSocket reconnects can reset a TCP socket while asyncio's
    Proactor transport is already closing it. The reset is handled by our WS/bridge
    lifecycle, but CPython may still print an exception from _call_connection_lost().
    Never hide unrelated asyncio errors.
    """
    previous = loop.get_exception_handler()

    def handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        message = str(context.get("message") or "")
        winerror = getattr(exc, "winerror", None)
        harmless_reset = isinstance(exc, ConnectionResetError) and winerror == 10054
        proactor_close = "_ProactorBasePipeTransport._call_connection_lost" in message or "_call_connection_lost" in message
        if harmless_reset and (os.name == "nt" or proactor_close):
            return
        if previous is not None:
            previous(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)

def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def path_to_local_url(path: str | None) -> str | None:
    path = (path or "").strip()
    if not path:
        return None
    try:
        p = Path(path).resolve()
        if p.exists() and OUTPUT_DIR.resolve() in p.parents:
            rel = p.relative_to(OUTPUT_DIR.resolve())
            return "/outputs/" + "/".join(rel.parts)
    except Exception:
        return None
    return None


def _persona_output_dir(profile_id: str) -> Path:
    d = OUTPUT_DIR / "personas" / _slug(profile_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _crop_square(img: Image.Image, top_ratio: float, side_ratio: float) -> Image.Image:
    w, h = img.size
    side = int(min(w, h, max(256, min(w, int(h * side_ratio)))))
    x = max(0, (w - side) // 2)
    y = max(0, min(h - side, int(h * top_ratio)))
    return img.crop((x, y, x + side, y + side))


def _enhance_face(img: Image.Image, size: int = 2048) -> Image.Image:
    img = ImageOps.autocontrast(img.convert("RGB"), cutoff=0.5)
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageEnhance.Color(img).enhance(1.02)
    img = ImageEnhance.Sharpness(img).enhance(1.12)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.8, percent=140, threshold=2))
    return img


def prepare_persona_variant_assets(source_path: str, profile_id: str, slot: str) -> dict[str, str]:
    src = Path(source_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Không thấy ảnh persona {slot}: {src}")
    outdir = _persona_output_dir(profile_id)
    ext = src.suffix.lower() if src.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
    original_copy = outdir / f"persona_{slot}_original{ext}"
    shutil.copy2(src, original_copy)
    img = Image.open(src).convert("RGB")
    face_crop = _crop_square(img, 0.08, 0.72 if slot == 'back' else 0.62)
    master_2048 = _enhance_face(face_crop, 2048)
    master_path = outdir / f"persona_{slot}_master_2048.jpg"
    master_2048.save(master_path, quality=95, optimize=True)
    return {
        f"persona_{slot}_path": str(src),
        f"persona_{slot}_original_path": str(original_copy.resolve()),
        f"persona_{slot}_master_path": str(master_path.resolve()),
    }


def prepare_persona_assets(source_path: str, profile_id: str, *, left_path: str | None = None, right_path: str | None = None, back_path: str | None = None) -> dict[str, Any]:
    src = Path(source_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Không thấy ảnh persona: {src}")
    outdir = _persona_output_dir(profile_id)
    ext = src.suffix.lower() if src.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
    original_copy = outdir / f"persona_original{ext}"
    shutil.copy2(src, original_copy)
    img = Image.open(src).convert("RGB")
    face_crop = _crop_square(img, 0.08, 0.62)
    bust_crop = _crop_square(img, 0.04, 0.88)
    face_crop_1024 = _enhance_face(face_crop, 1024)
    master_2048 = _enhance_face(face_crop, 2048)
    bust_2048 = _enhance_face(bust_crop, 2048)
    fullbody_w = 1536
    ratio = fullbody_w / img.width
    fullbody_h = int(img.height * ratio)
    fullbody_1536 = img.resize((fullbody_w, fullbody_h), Image.Resampling.LANCZOS)
    fullbody_1536 = ImageEnhance.Sharpness(fullbody_1536).enhance(1.05)
    paths = {
        "persona_original_path": str(original_copy.resolve()),
        "persona_face_crop_path": str((outdir / "persona_face_crop_1024.jpg").resolve()),
        "persona_master_path": str((outdir / "persona_master_2048.jpg").resolve()),
        "persona_bust_path": str((outdir / "persona_bust_2048.jpg").resolve()),
        "persona_fullbody_path": str((outdir / "persona_fullbody_1536.jpg").resolve()),
        "persona_prepared_at": utcnow(),
    }
    face_crop_1024.save(paths["persona_face_crop_path"], quality=95, optimize=True)
    master_2048.save(paths["persona_master_path"], quality=95, optimize=True)
    bust_2048.save(paths["persona_bust_path"], quality=95, optimize=True)
    fullbody_1536.save(paths["persona_fullbody_path"], quality=95, optimize=True)
    if (left_path or '').strip():
        paths.update(prepare_persona_variant_assets(left_path, profile_id, 'left'))
    if (right_path or '').strip():
        paths.update(prepare_persona_variant_assets(right_path, profile_id, 'right'))
    if (back_path or '').strip():
        paths.update(prepare_persona_variant_assets(back_path, profile_id, 'back'))
    paths["persona_assets"] = {
        "original": {"path": paths["persona_original_path"], "url": path_to_local_url(paths["persona_original_path"])},
        "face_crop": {"path": paths["persona_face_crop_path"], "url": path_to_local_url(paths["persona_face_crop_path"])},
        "master_2048": {"path": paths["persona_master_path"], "url": path_to_local_url(paths["persona_master_path"])},
        "bust_2048": {"path": paths["persona_bust_path"], "url": path_to_local_url(paths["persona_bust_path"])},
        "left_2048": {"path": paths.get("persona_left_master_path"), "url": path_to_local_url(paths.get("persona_left_master_path"))},
        "right_2048": {"path": paths.get("persona_right_master_path"), "url": path_to_local_url(paths.get("persona_right_master_path"))},
        "back_2048": {"path": paths.get("persona_back_master_path"), "url": path_to_local_url(paths.get("persona_back_master_path"))},
        "fullbody_1536": {"path": paths["persona_fullbody_path"], "url": path_to_local_url(paths["persona_fullbody_path"])},
    }
    return paths


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    # WAL is configured once in init_db(). Re-running journal_mode on every
    # short-lived connection adds lock/IO overhead on Windows.
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    return c




def ensure_column(c: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


GITHUB_MODEL_PREFIXES = ("gh/", "github/")
PERMANENT_MODEL_ERROR_PATTERNS = (
    "model is not supported",
    "model_not_supported",
    "model not supported",
    "model_not_available_for_integrator",
    "not available for integrator",
    "requested model is not supported",
    "requested model is not available for integrator",
)


def _is_github_model(model_id: str, owned_by: str | None = None) -> bool:
    mid = str(model_id or "").strip().lower()
    owner = str(owned_by or "").strip().lower()
    return mid.startswith(GITHUB_MODEL_PREFIXES) or "github" in owner


def _is_permanent_model_error(error: str | None) -> bool:
    low = str(error or "").lower()
    return any(p in low for p in PERMANENT_MODEL_ERROR_PATTERNS)


def _apply_model_block_policy_db(c: sqlite3.Connection) -> None:
    now = utcnow()
    # Provider GitHub is globally forbidden, even if a previous health test was green.
    c.execute(
        "UPDATE ai_model_status SET disabled=1,hard_disabled=1,block_reason='github_provider_blocked',updated_at=? "
        "WHERE lower(model_id) LIKE 'gh/%' OR lower(model_id) LIKE 'github/%'",
        (now,),
    )
    # Upgrade old V2.5 errors that clearly mean the model can never work on this route.
    rows = c.execute("SELECT model_id,error FROM ai_model_status WHERE status='error'").fetchall()
    permanent = [str(r["model_id"]) for r in rows if _is_permanent_model_error(r["error"])]
    if permanent:
        c.executemany(
            "UPDATE ai_model_status SET disabled=1,hard_disabled=1,block_reason='model_not_supported',updated_at=? WHERE model_id=?",
            [(now, mid) for mid in permanent],
        )
    # Any Page Profile pinned to a forbidden/permanent model goes back to AUTO.
    c.execute("UPDATE page_profiles SET ai_model='',updated_at=? WHERE lower(ai_model) LIKE 'gh/%' OR lower(ai_model) LIKE 'github/%'", (now,))
    hard_ids = [str(r["model_id"]) for r in c.execute("SELECT model_id FROM ai_model_status WHERE COALESCE(hard_disabled,0)=1").fetchall()]
    if hard_ids:
        c.executemany("UPDATE page_profiles SET ai_model='',updated_at=? WHERE ai_model=?", [(now, mid) for mid in hard_ids])


def init_db() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS flow_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'flow',
                status TEXT NOT NULL,
                prompt TEXT,
                flow_json TEXT NOT NULL,
                scenes_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                agent_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_flow_jobs_status ON flow_jobs(status, created_at);

            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                scene_id INTEGER,
                kind TEXT NOT NULL,
                url TEXT,
                local_path TEXT,
                media_id TEXT,
                title TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assets_job ON assets(job_id, scene_id);
            CREATE INDEX IF NOT EXISTS idx_assets_kind_scene ON assets(kind, scene_id);

            CREATE TABLE IF NOT EXISTS flow_scene_checkpoints (
                job_id TEXT NOT NULL,
                scene_id INTEGER NOT NULL,
                image_status TEXT NOT NULL DEFAULT 'pending',
                image_media_id TEXT,
                image_local_path TEXT,
                video_status TEXT NOT NULL DEFAULT 'pending',
                video_media_ids_json TEXT NOT NULL DEFAULT '[]',
                video_local_paths_json TEXT NOT NULL DEFAULT '[]',
                video_download_urls_json TEXT NOT NULL DEFAULT '{}',
                video_download_meta_json TEXT NOT NULL DEFAULT '{}',
                invalid_video_media_ids_json TEXT NOT NULL DEFAULT '[]',
                video_regen_count INTEGER NOT NULL DEFAULT 0,
                image_attempts INTEGER NOT NULL DEFAULT 0,
                video_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id, scene_id)
            );
            CREATE INDEX IF NOT EXISTS idx_flow_scene_checkpoints_job ON flow_scene_checkpoints(job_id, scene_id);

            CREATE TABLE IF NOT EXISTS fb_pages (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                access_token TEXT NOT NULL,
                tasks_json TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_test_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fb_page_ignored (
                id TEXT PRIMARY KEY,
                name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS publish_jobs (
                id TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                video_path TEXT NOT NULL,
                title TEXT,
                description TEXT,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 1,
                fb_video_id TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status, created_at);

            CREATE TABLE IF NOT EXISTS page_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                theme TEXT NOT NULL DEFAULT '',
                persona_path TEXT,
                body_preset TEXT NOT NULL DEFAULT 'curvy_fit',
                sexiness_level INTEGER NOT NULL DEFAULT 60,
                outfit_prompts_json TEXT,
                outfit_paths_json TEXT,
                backgrounds_json TEXT,
                poses_json TEXT,
                music_paths_json TEXT,
                default_video_mode TEXT NOT NULL DEFAULT 'AUTO',
                image_to_video_ratio INTEGER NOT NULL DEFAULT 25,
                image_model TEXT NOT NULL DEFAULT 'Nano Banana 2',
                video_model TEXT NOT NULL DEFAULT 'Veo 3.1 - Fast',
                facebook_page_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_page_profiles_enabled ON page_profiles(enabled, name);

            CREATE TABLE IF NOT EXISTS factory_runs (
                id TEXT PRIMARY KEY,
                page_profile_id TEXT NOT NULL,
                requested_count INTEGER NOT NULL,
                requested_mode TEXT NOT NULL,
                auto_publish INTEGER NOT NULL DEFAULT 0,
                facebook_dry_run INTEGER NOT NULL DEFAULT 1,
                job_ids_json TEXT NOT NULL,
                config_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_queue (
                id TEXT PRIMARY KEY,
                page_profile_id TEXT NOT NULL,
                flow_job_id TEXT UNIQUE,
                video_path TEXT,
                title TEXT,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'generating',
                publish_job_id TEXT,
                scheduled_for TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_queue_profile_status ON content_queue(page_profile_id,status,created_at);
            CREATE INDEX IF NOT EXISTS idx_content_queue_flow_job ON content_queue(flow_job_id);

            CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                type TEXT NOT NULL,
                job_id TEXT,
                agent_id TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_event_logs_id ON event_logs(id DESC);
            CREATE INDEX IF NOT EXISTS idx_event_logs_type ON event_logs(type, id DESC);

            CREATE TABLE IF NOT EXISTS ai_model_status (
                model_id TEXT PRIMARY KEY,
                family TEXT NOT NULL DEFAULT 'other',
                status TEXT NOT NULL DEFAULT 'untested',
                latency_ms INTEGER,
                error TEXT,
                tested_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_model_status_status ON ai_model_status(status, family);

            CREATE TABLE IF NOT EXISTS qc_results (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                score REAL NOT NULL,
                passed INTEGER NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_qc_job ON qc_results(job_id, created_at);
            """
        )
        ensure_column(c, "page_profiles", "title_hint", "TEXT")
        ensure_column(c, "page_profiles", "caption_style", "TEXT DEFAULT ''")
        ensure_column(c, "page_profiles", "ai_model", "TEXT DEFAULT ''")
        ensure_column(c, "page_profiles", "ai_provider", "TEXT DEFAULT 'router9'")
        ensure_column(c, "page_profiles", "persona_original_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_face_crop_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_bust_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_master_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_left_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_right_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_back_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_left_master_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_right_master_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_back_master_path", "TEXT")
        ensure_column(c, "page_profiles", "persona_left_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(c, "page_profiles", "persona_right_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(c, "page_profiles", "persona_back_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(c, "page_profiles", "persona_prepared_at", "TEXT")
        ensure_column(c, "page_profiles", "scheduler_enabled", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "page_profiles", "publish_interval_minutes", "INTEGER NOT NULL DEFAULT 180")
        ensure_column(c, "page_profiles", "buffer_target", "INTEGER NOT NULL DEFAULT 2")
        ensure_column(c, "page_profiles", "scheduler_dry_run", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(c, "page_profiles", "scheduler_warmup", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(c, "page_profiles", "next_publish_at", "TEXT")
        ensure_column(c, "page_profiles", "last_publish_at", "TEXT")
        ensure_column(c, "page_profiles", "scheduler_config_json", "TEXT")
        ensure_column(c, "ai_model_status", "disabled", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "ai_model_status", "hard_disabled", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "ai_model_status", "block_reason", "TEXT")
        ensure_column(c, "flow_jobs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "flow_jobs", "max_retries", "INTEGER NOT NULL DEFAULT 5")
        ensure_column(c, "flow_jobs", "next_retry_at", "TEXT")
        ensure_column(c, "flow_jobs", "last_stage", "TEXT")
        ensure_column(c, "flow_jobs", "retry_reason", "TEXT")
        ensure_column(c, "flow_jobs", "dispatch_epoch", "INTEGER NOT NULL DEFAULT 0")
        # V4.2/V4.4: persist exact mediaId -> last signed URL and deterministic
        # invalidation/regeneration state.  Old IDs stay in history but are never allowed
        # to trap the dispatcher in DOWNLOAD_ONLY forever.
        ensure_column(c, "flow_scene_checkpoints", "video_download_urls_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(c, "flow_scene_checkpoints", "video_download_meta_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(c, "flow_scene_checkpoints", "invalid_video_media_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(c, "flow_scene_checkpoints", "video_regen_count", "INTEGER NOT NULL DEFAULT 0")
        # Existing Parenting rows inherit the stronger V4 recovery budget too.
        c.execute("UPDATE flow_jobs SET max_retries=8 WHERE kind LIKE 'parenting_%' AND COALESCE(max_retries,0)<8")
        ensure_column(c, "publish_jobs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "publish_jobs", "max_retries", "INTEGER NOT NULL DEFAULT 4")
        ensure_column(c, "publish_jobs", "next_retry_at", "TEXT")
        _apply_model_block_policy_db(c)
        # V4.0 restart reconciliation is NON-DESTRUCTIVE.
        # Never mark a generating item failed merely because Python restarted; completed
        # scene assets/mediaIds are checkpoints and will be reused on reconnect.
        now = utcnow()
        c.execute(
            "UPDATE flow_jobs SET status='interrupted',agent_id=NULL,retry_reason='server_restart',updated_at=? "
            "WHERE status IN ('dispatching','running','downloading')",
            (now,),
        )
        c.execute(
            "UPDATE content_queue SET status='generating',error='Server restart; resume từ checkpoint',updated_at=? "
            "WHERE status='generating'",
            (now,),
        )
        c.execute(
            "UPDATE content_queue SET status='ready',error='Server restart khi đang publish; scheduler sẽ kiểm tra publish job trước khi retry',updated_at=? "
            "WHERE status='publishing' AND video_path IS NOT NULL",
            (now,),
        )


def rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


class FlowTestRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=6000)
    person_path: str | None = None
    outfit_path: str | None = None
    image_model: str = "Nano Banana 2"
    aspect_ratio: str = "9:16"
    image_outputs: str = "x1"


class VideoTestRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=6000)
    person_path: str | None = None
    outfit_path: str | None = None
    music_path: str | None = None
    image_model: str = "Nano Banana 2"
    image_count: int = Field(default=6, ge=2, le=10)
    image_concurrency: int = Field(default=6, ge=1, le=10)
    duration_sec: float = Field(default=10.0, ge=4.0, le=30.0)
    motion_preset: str = "capcut_beat"


class FlowJobRequest(BaseModel):
    scenes: list[dict[str, Any]]
    flow: dict[str, Any] = Field(default_factory=dict)
    kind: str = "flow"

class FlowDownloadMediaTestRequest(BaseModel):
    media_id: str = Field(min_length=8, max_length=200)
    job_id: str | None = None
    scene_id: int = Field(default=1, ge=1, le=999)


class FactoryBatchRequest(BaseModel):
    count: int = Field(default=8, ge=1, le=100)
    page_profile: str = "Gym Girls"
    theme: str = "adult fitness lifestyle"
    base_prompt: str = "Photorealistic adult woman, natural smartphone photography, vertical social media composition"
    persona_path: str | None = None
    outfit_path: str | None = None
    image_model: str = "Nano Banana 2"
    aspect_ratio: str = "9:16"
    image_outputs: str = "x1"
    image_concurrency: int = Field(default=9, ge=1, le=10)


class PageProfileSave(BaseModel):
    id: str | None = None
    name: str = Field(min_length=2, max_length=120)
    theme: str = "adult glamour fitness lifestyle"
    persona_path: str | None = None
    persona_left_path: str | None = None
    persona_right_path: str | None = None
    persona_back_path: str | None = None
    body_preset: str = "curvy_fit"
    sexiness_level: int = Field(default=60, ge=0, le=100)
    outfit_prompts: list[str] = Field(default_factory=list)
    outfit_paths: list[str] = Field(default_factory=list)
    backgrounds: list[str] = Field(default_factory=list)
    poses: list[str] = Field(default_factory=list)
    music_paths: list[str] = Field(default_factory=list)
    default_video_mode: str = "AUTO"
    image_to_video_ratio: int = Field(default=25, ge=0, le=100)
    image_model: str = "Nano Banana 2"
    video_model: str = "Veo 3.1 - Fast"
    facebook_page_id: str | None = None
    title_hint: str = ""
    caption_style: str = "engaging_short"
    ai_model: str = ""
    ai_provider: str = "router9"
    enabled: bool = True


class FactoryV2GenerateRequest(BaseModel):
    page_profile_id: str
    videos: int = Field(default=3, ge=1, le=50)
    mode: str = "AUTO"
    beat_image_count: int = Field(default=7, ge=3, le=10)
    beat_duration_sec: float = Field(default=10.0, ge=4.0, le=30.0)
    beat_motion_preset: str = "capcut_beat"
    i2v_clip_count: int = Field(default=3, ge=2, le=6)
    i2v_clip_duration: str = "4s"
    image_concurrency: int = Field(default=9, ge=1, le=10)
    video_concurrency: int = Field(default=4, ge=1, le=10)
    auto_publish: bool = False
    facebook_dry_run: bool = True


class SchedulerConfigRequest(BaseModel):
    enabled: bool = True
    scheduler_mode: str = "INTERVAL"
    publish_interval_minutes: int = Field(default=180, ge=5, le=10080)
    buffer_target: int = Field(default=2, ge=1, le=20)
    facebook_dry_run: bool = True
    first_publish_delay_minutes: int = Field(default=0, ge=0, le=10080)
    daily_slots: list[str] = Field(default_factory=lambda: ["08:00", "14:00", "21:00"])
    daily_random_minutes: int = Field(default=30, ge=0, le=180)
    resume_random_minutes: int = Field(default=30, ge=0, le=180)
    mode: str = "AUTO"
    beat_image_count: int = Field(default=7, ge=3, le=10)
    beat_duration_sec: float = Field(default=10.0, ge=4.0, le=30.0)
    beat_motion_preset: str = "capcut_beat"
    i2v_clip_count: int = Field(default=3, ge=2, le=6)
    i2v_clip_duration: str = "4s"
    image_concurrency: int = Field(default=9, ge=1, le=10)
    video_concurrency: int = Field(default=4, ge=1, le=10)


class FacebookPageSave(BaseModel):
    page_id: str
    name: str = "Facebook Page"
    access_token: str


class FacebookSyncRequest(BaseModel):
    user_access_token: str | None = None


class FacebookTokenResolveRequest(BaseModel):
    access_token: str = Field(min_length=20, max_length=4096)


class FacebookPublishRequest(BaseModel):
    page_id: str
    video_path: str
    description: str = ""
    title: str = ""
    dry_run: bool | None = None


class AiModelTestRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=300)


class AgentRuntime:
    def __init__(self, connection_id: str, ws: WebSocket):
        self.id = connection_id
        self.ws = ws
        self.extension_id: str | None = None
        self.version: str | None = None
        self.role: str = "unknown"
        self.runtime: dict[str, Any] = {}
        self.connected_at = utcnow()
        self.last_seen = utcnow()
        self.busy = False
        self.job_id: str | None = None
        self.active_job_ids: set[str] = set()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "extension_id": self.extension_id,
            "version": self.version,
            "role": self.role,
            "busy": self.busy,
            "job_id": self.job_id,
            "queue_depth": len(self.active_job_ids),
            "active_job_ids": sorted(self.active_job_ids),
            "connected_at": to_server_time(self.connected_at),
            "last_seen": to_server_time(self.last_seen),
            "runtime": self.runtime,
            "parenting_compatible": agent_parenting_compatible(self),
            "compatibility": agent_compatibility_label(self),
        }


def _version_tuple(value: str | None) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", str(value or ""))[:4]]
    return tuple(nums) if nums else (0,)


def agent_parenting_compatible(agent: AgentRuntime) -> bool:
    if str(agent.role or "").lower() not in {"flow-extension", "flow_extension", "flow"}:
        return False
    ver = _version_tuple(agent.version)
    padded = ver + (0,) * max(0, len(PARENTING_AGENT_MIN_VERSION) - len(ver))
    return padded[:len(PARENTING_AGENT_MIN_VERSION)] >= PARENTING_AGENT_MIN_VERSION


def agent_compatibility_label(agent: AgentRuntime) -> str:
    if agent_parenting_compatible(agent):
        return "parenting-ready"
    if not agent.version:
        return "waiting-hello"
    return f"legacy/incompatible (< {'.'.join(map(str, PARENTING_AGENT_MIN_VERSION))})"


def agent_supports_job(agent: AgentRuntime, kind: str) -> bool:
    # Parenting requires the modern server bridge/result schema and multi-reference support.
    if str(kind or "").startswith("parenting_"):
        return agent_parenting_compatible(agent)
    return True


def agent_priority(agent: AgentRuntime) -> tuple[int, tuple[int, ...], str]:
    # Prefer Parenting-capable/newer workers. Stable id keeps ordering deterministic.
    return (1 if agent_parenting_compatible(agent) else 0, _version_tuple(agent.version), str(agent.id))


AGENTS: dict[str, AgentRuntime] = {}
DOWNLOAD_RECOVERY: dict[str, dict[str, Any]] = {}
SERVER_VIDEO_DOWNLOAD_TASKS: dict[str, asyncio.Task] = {}
SHOPEE_INSPECT_WAITERS: dict[str, asyncio.Future] = {}
SHOPEE_SEARCH_WAITERS: dict[str, asyncio.Future] = {}
PARENTING_HANDLER = None
UI_CLIENTS: set[WebSocket] = set()
LEGACY_OBSERVER_CLIENTS: set[WebSocket] = set()
DISPATCH_LOCK = asyncio.Lock()
SCHEDULER_LOCK = asyncio.Lock()
BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn(coro: Any) -> asyncio.Task:
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


def persist_event_log(event: dict[str, Any]) -> None:
    try:
        payload = {"ts": server_now(), **event}
        etype = str(payload.get("type") or "EVENT")
        job_id = payload.get("jobId") or payload.get("job_id")
        agent_id = payload.get("agentId") or payload.get("agent_id")
        if not agent_id and isinstance(payload.get("agent"), dict):
            agent_id = payload["agent"].get("id") or payload["agent"].get("extension_id")
        with conn() as c:
            c.execute(
                "INSERT INTO event_logs(ts,type,job_id,agent_id,payload_json) VALUES(?,?,?,?,?)",
                (payload["ts"], etype, str(job_id) if job_id else None, str(agent_id) if agent_id else None, dumps(payload)),
            )
            # Keep DB bounded for a local always-on server.
            c.execute("DELETE FROM event_logs WHERE id < (SELECT COALESCE(MAX(id),0)-5000 FROM event_logs)")
    except Exception:
        pass


def _short_log_message(payload: dict[str, Any]) -> str:
    et = str(payload.get("type") or "EVENT")
    job = str(payload.get("jobId") or payload.get("job_id") or "")
    scene = payload.get("sceneId") or payload.get("scene_id")
    model = payload.get("model") or payload.get("imageModel") or payload.get("videoModel")
    parts: list[str] = []

    if et == "SERVER_STARTED":
        return f"SERVER STARTED · v{payload.get('serverVersion') or SERVER_VERSION} · log phiên cũ đã clear"
    if et == "AGENT_HELLO":
        a = payload.get("agent") or {}
        rt = a.get("runtime") or {}
        parts.append(f"Flow Agent online · v{a.get('version') or '?'}")
        if a.get("extension_id"):
            parts.append(str(a.get("extension_id")))
        if rt.get("progressLabel"):
            parts.append(str(rt.get("progressLabel")))
        return " · ".join(parts)
    if et == "AGENT_CONNECTED":
        return "Flow Agent connected · chờ HELLO"
    if et == "AGENT_DISCONNECTED":
        return "V2.8 internal Flow broker disconnected · master sẽ tự reconnect"
    if et == "VIDEO_DOWNLOAD_RECOVERY":
        return f"DOWNLOAD RECOVERY · job={job} · scene={scene or '-'} · {payload.get('count') or '-'} media"
    if et == "VIDEO_DOWNLOAD_RECOVERED":
        return f"DOWNLOAD RECOVERED · job={job} · đủ video local"
    if et == "VIDEO_FILE_ERROR":
        return f"DOWNLOAD ERROR · job={job} · scene={scene or '-'} · {payload.get('error') or ''}"
    if et == "VIDEO_MEDIA_REGENERATE_REQUIRED":
        return f"MEDIA INVALID → REGENERATE · job={job} · scene={scene or '-'} · regen={payload.get('regeneration') or '-'} · {str(payload.get('failedMediaId') or '')[:8]}"
    if et == "VIDEO_MEDIA_REGEN_EXHAUSTED":
        return f"MEDIA REGEN EXHAUSTED · job={job} · scene={scene or '-'} · {payload.get('error') or ''}"
    if et == "JOB_QUEUED":
        return f"JOB QUEUED · {payload.get('kind') or '-'} · job={job} · scenes={payload.get('sceneCount') or '-'}"
    if et == "JOB_DISPATCHED":
        return f"DISPATCH → Flow · job={job} · agent={payload.get('agentId') or payload.get('agent_id') or '-'}"
    if et == "FLOW_JOB_ACCEPTED":
        return f"Flow đã nhận job · {job}"
    if et == "IMAGE_READY":
        title = payload.get("title") or ""
        media = payload.get("mediaId") or ""
        return f"IMAGE READY · job={job} · scene={scene or '-'} · media={str(media)[:10]} · {title}".strip(" ·")
    if et == "VIDEO_FILE_READY":
        path = payload.get("localPath") or payload.get("path") or ""
        return f"VIDEO READY · job={job} · scene={scene or '-'} · {Path(str(path)).name if path else 'file ready'}"
    if et == "PARENTING_CHARACTER_READY":
        return f"CHARACTER READY · {payload.get('characterId') or '-'} · job={job} · {Path(str(payload.get('localPath') or '')).name}".strip(" ·")
    if et == "PARENTING_CHARACTER_SYNC_FAILED":
        return f"CHARACTER SYNC FAIL · {payload.get('characterId') or '-'} · job={job} · {payload.get('error') or 'Flow DONE nhưng thiếu ảnh trả về'}"
    if et == "PARENTING_AGENT_SKIPPED":
        return f"AGENT BỎ QUA · v{payload.get('version') or '?'} · không tương thích Parenting"
    if et == "PARENTING_RENDER_STARTED":
        return f"PARENTING RENDER START · job={job}"
    if et == "PARENTING_VIDEO_READY":
        return f"PARENTING FINAL READY · job={job} · {Path(str(payload.get('localPath') or '')).name}".strip(" ·")
    if et == "PARENTING_RENDER_FAILED":
        return f"PARENTING RENDER FAIL · job={job} · {payload.get('error') or ''}"
    if et == "FLOW_JOB_RESULT":
        ok = bool(payload.get("ok"))
        result = payload.get("result") or {}
        jobs = result.get("jobs") if isinstance(result, dict) else None
        count = len(jobs) if isinstance(jobs, dict) else payload.get("sceneCount")
        return f"FLOW DONE · job={job} · {'OK' if ok else 'FAIL'}" + (f" · scenes={count}" if count is not None else "")
    if et == "FACTORY_RENDER_STARTED":
        return f"RENDER START · job={job} · đang ghép final MP4"
    if et == "FACTORY_VIDEO_READY":
        qc = payload.get("qc") or {}
        path = payload.get("localPath") or ""
        return f"FINAL READY · job={job} · QC={qc.get('score','-')} {'PASS' if qc.get('passed') else 'FAIL'} · {Path(str(path)).name if path else ''}".strip(" ·")
    if et == "AUTO_PUBLISH_QUEUED":
        return f"FACEBOOK QUEUE · job={job} · {'DRY RUN' if payload.get('dryRun') else 'PUBLISH THẬT'} · page={payload.get('pageId') or '-'}"
    if et in {"FACTORY_RENDER_FAILED","VIDEO_RENDER_FAILED","FLOW_JOB_REJECTED","FLOW_JOB_FAILED"}:
        return f"LỖI · job={job or '-'} · {payload.get('error') or payload.get('message') or ''}"
    if et == "AGENT_EVENT":
        msg = payload.get("message") or {}
        mt = str(msg.get("type") or "event")
        if mt == "FLOW_RUNTIME":
            rt = msg.get("runtime") or {}
            pct = rt.get("progressPercent")
            label = rt.get("progressLabel") or "Flow đang chạy"
            detail = rt.get("progressDetail") or ""
            metrics = rt.get("metrics") or {}
            last_line = ""
            logs = rt.get("logs") or []
            if logs:
                last = logs[-1] or {}
                last_line = str(last.get("text") or "")
            seg = [str(label)]
            if pct is not None:
                seg.append(f"{pct}%")
            if metrics:
                seg.append(f"done {metrics.get('done',0)}/{metrics.get('total',0)}")
            if detail:
                seg.append(str(detail))
            if last_line and last_line not in detail:
                seg.append(last_line)
            return " · ".join(x for x in seg if x)
        if mt == "FLOW_LOG":
            level = msg.get("level") or "info"
            text = msg.get("text") or msg.get("message") or ""
            return f"FLOW {str(level).upper()} · {text}"
        return f"Agent event · {mt} · {msg.get('text') or msg.get('message') or ''}".strip(" ·")
    if model:
        parts.append(f"model={model}")
    if scene:
        parts.append(f"scene={scene}")
    return et + (f" · job={job}" if job else "") + ((" · " + " · ".join(parts)) if parts else "")


CONSOLE_EVENT_TYPES = {
    "SERVER_STARTED", "AGENT_CONNECTED", "AGENT_HELLO", "AGENT_DISCONNECTED",
    "JOB_QUEUED", "JOB_DISPATCHED", "FLOW_JOB_ACCEPTED", "FLOW_JOB_REJECTED",
    "IMAGE_READY", "VIDEO_FILE_READY", "VIDEO_FILE_ERROR", "VIDEO_DOWNLOAD_RECOVERY", "VIDEO_DOWNLOAD_RECOVERED", "VIDEO_MEDIA_REGENERATE_REQUIRED", "VIDEO_MEDIA_REGEN_EXHAUSTED", "FLOW_JOB_RESULT",
    "PARENTING_CHARACTER_READY", "PARENTING_CHARACTER_SYNC_FAILED", "PARENTING_AGENT_SKIPPED", "PARENTING_RENDER_STARTED", "PARENTING_VIDEO_READY",
    "PARENTING_RENDER_FAILED", "FACTORY_RENDER_STARTED", "FACTORY_VIDEO_READY",
    "FLOW_JOB_INTERRUPTED", "FLOW_JOB_FAILED",
}


def console_event(event: dict[str, Any]) -> None:
    """Readable one-line lifecycle log for Windows console.

    Runtime telemetry is intentionally excluded so the console stays useful.
    """
    try:
        et = str(event.get("type") or "EVENT")
        if et not in CONSOLE_EVENT_TYPES:
            return
        payload = {"ts": server_now(), **event}
        stamp = datetime.now(SCHEDULER_TZ).strftime("%H:%M:%S")
        print(f"[{stamp}] {_short_log_message(payload)}", flush=True)
    except Exception:
        pass


def record_local_event(event: dict[str, Any]) -> None:
    """Persist + print a synchronous event. UI reads it via /api/logs polling."""
    persist_event_log(event)
    console_event(event)


def list_event_logs(limit: int = 300, mode: str = "short") -> list[dict[str, Any]]:
    limit = min(max(int(limit), 1), 1000)
    with conn() as c:
        rows = c.execute("SELECT * FROM event_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out=[]
    for r in rows:
        payload = loads(r["payload_json"], {}) or {}
        if mode == "full":
            out.append({"id":r["id"],"ts":r["ts"],"type":r["type"],"job_id":r["job_id"],"agent_id":r["agent_id"],"payload":payload})
        else:
            out.append({"id":r["id"],"ts":r["ts"],"type":r["type"],"job_id":r["job_id"],"message":_short_log_message(payload)})
    return out


RUNTIME_EVENT_LAST_PERSIST: dict[str, float] = {}


def _should_persist_ui_event(event: dict[str, Any]) -> bool:
    if str(event.get("type") or "") != "AGENT_EVENT":
        return True
    msg = event.get("message") or {}
    if str(msg.get("type") or "") != "FLOW_RUNTIME":
        return True
    key = str(event.get("agentId") or "runtime")
    now = time.monotonic()
    last = RUNTIME_EVENT_LAST_PERSIST.get(key, 0.0)
    if now - last < 2.0:
        return False
    RUNTIME_EVENT_LAST_PERSIST[key] = now
    return True


async def ui_broadcast(event: dict[str, Any]) -> None:
    if _should_persist_ui_event(event):
        persist_event_log(event)
    console_event(event)
    payload = dumps({"ts": server_now(), **event})
    dead_ui: list[WebSocket] = []
    for ws in list(UI_CLIENTS):
        try:
            await ws.send_text(payload)
        except Exception:
            dead_ui.append(ws)
    for ws in dead_ui:
        UI_CLIENTS.discard(ws)

    # V1.1 compatibility observers are read-only. They receive lifecycle events
    # but are NEVER registered as Flow agents and can never receive jobs.
    dead_legacy: list[WebSocket] = []
    for ws in list(LEGACY_OBSERVER_CLIENTS):
        try:
            await ws.send_text(payload)
        except Exception:
            dead_legacy.append(ws)
    for ws in dead_legacy:
        LEGACY_OBSERVER_CLIENTS.discard(ws)


def get_flow_job(job_id: str) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute("SELECT * FROM flow_jobs WHERE id=?", (job_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["flow"] = loads(d.pop("flow_json"), {})
    d["scenes"] = loads(d.pop("scenes_json"), [])
    d["result"] = loads(d.pop("result_json"), None)
    return d


def list_flow_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM flow_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["flow"] = loads(d.pop("flow_json"), {})
        d["scenes"] = loads(d.pop("scenes_json"), [])
        d["result"] = loads(d.pop("result_json"), None)
        out.append(d)
    return out


def list_flow_jobs_summary(limit: int = 100) -> list[dict[str, Any]]:
    """Lightweight job rows for the web table; do not decode flow/result payloads."""
    with conn() as c:
        rows = c.execute(
            "SELECT id,kind,status,error,agent_id,created_at,updated_at,scenes_json FROM flow_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["scene_count"] = len(loads(d.pop("scenes_json"), []))
        except Exception:
            d.pop("scenes_json", None)
            d["scene_count"] = 0
        out.append(d)
    return out


def create_flow_job(kind: str, scenes: list[dict[str, Any]], flow: dict[str, Any]) -> str:
    if not scenes:
        raise HTTPException(400, "Job phải có ít nhất 1 scene")
    job_id = f"flow_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    prompt = str(scenes[0].get("imagePrompt") or scenes[0].get("videoPrompt") or "")
    now = utcnow()
    with conn() as c:
        retry_budget = 8 if str(kind).startswith("parenting_") else 5
        c.execute(
            "INSERT INTO flow_jobs(id,kind,status,prompt,flow_json,scenes_json,max_retries,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, kind, "queued", prompt, dumps(flow), dumps(scenes), retry_budget, now, now),
        )
    record_local_event({"type": "JOB_QUEUED", "jobId": job_id, "kind": kind, "sceneCount": len(scenes)})
    return job_id


def update_flow_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utcnow()
    cols = ",".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [job_id]
    with conn() as c:
        c.execute(f"UPDATE flow_jobs SET {cols} WHERE id=?", vals)


def default_flow_config(**overrides: Any) -> dict[str, Any]:
    cfg = {
        "imageModel": "Nano Banana 2",
        "videoModel": "NONE",
        "imageConcurrency": 9,
        "videoConcurrency": 4,
        "submitPolicy": "GLOBAL_FIFO",
        "autoDownloadVideo": False,
        "maxSubmitsPerMinute": 8,
        "submitGapMs": 700,
        "aspectRatio": "9:16",
        "imageOutputs": "x1",
        "videoDuration": "8s",
        "videoOutputs": "x1",
        "videoExtendFactor": "x1",
        "imageTimeoutSec": 300,
        "videoTimeoutSec": 600,
    }
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


BODY_PRESETS: dict[str, str] = {
    "slim_fit": "adult woman with a slim athletic figure, toned legs, balanced natural proportions",
    "curvy_fit": "adult woman with a naturally curvy athletic figure, fuller bust and rounded hips, defined waist, balanced realistic proportions",
    "glam_curvy": "adult woman with a glamorous curvy figure, fuller bust, rounded hips and a defined waist, realistic anatomy and proportions",
    "soft_feminine": "adult woman with a soft feminine figure, graceful curves and realistic natural proportions",
    "sporty_curvy": "adult woman with a sporty curvy figure, strong legs, defined waist, fuller bust and hips, realistic athletic proportions",
}

DEFAULT_OUTFITS = [
    "fitted sleeveless cooling crop top with high-waisted short gym shorts",
    "lightweight fitted camisole-style top with short sculpting shorts",
    "figure-flattering athletic crop top with high-waisted mini shorts",
    "cool breathable ribbed top with short sporty skirt and safety shorts",
    "sleek halter-style fitted top with short lounge shorts",
    "summer fitted square-neck top with short tailored shorts",
    "body-hugging sporty two-piece set with short bottoms, fully opaque",
    "glamorous fitted off-shoulder top with short skort, fully clothed and opaque",
]
DEFAULT_BACKGROUNDS = [
    "premium modern gym with mirrors and soft practical lighting",
    "bright clean fitness studio",
    "upscale hotel gym",
    "minimal mirror workout studio",
    "modern lifestyle cafe with large windows",
    "sunlit premium apartment interior",
    "clean urban rooftop at golden hour",
    "modern shopping mall corridor",
    "stylish hotel lobby",
    "night city street with cinematic practical lights",
]
DEFAULT_POSES = [
    "standing naturally and looking toward camera",
    "casual mirror selfie pose",
    "walking toward camera with relaxed confident posture",
    "adjusting hair naturally",
    "relaxed three-quarter side pose",
    "holding a water bottle after workout",
    "sitting on a bench with relaxed posture",
    "checking a smartwatch",
    "turning slightly toward camera",
    "walking past camera and glancing back naturally",
]
VIDEO_MOTIONS = [
    "Walk two or three natural steps toward camera, then glance slightly to the side. Camera slowly dollies backward. Natural hair and clothing motion. Do not repeat a static pose.",
    "Turn the upper body gently toward camera while adjusting hair once. Camera makes a subtle arc from left to right. Keep movement confident and continuous.",
    "Begin in a relaxed side pose, shift weight naturally, then take one step forward. Camera pushes in slightly. Preserve face, outfit and body identity.",
    "Take a few slow steps across the scene, briefly look toward camera, then continue. Gentle handheld-style camera follow, realistic motion.",
    "Hold the current pose for a brief moment, then naturally change stance and touch the outfit or hair once. Camera moves closer with a smooth push-in.",
    "Walk past the camera at a slight angle and glance back once. Camera pans to follow. Keep the same adult woman, outfit, lighting and location.",
]


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip()).strip("_").lower()
    return value[:48] or f"page_{uuid.uuid4().hex[:8]}"


def _clean_list(items: list[str] | None, fallback: list[str] | None = None) -> list[str]:
    out = [str(x).strip() for x in (items or []) if str(x).strip()]
    return out or list(fallback or [])


def _profile_from_row(row: sqlite3.Row | dict[str, Any] | None, include_secret_paths: bool = True) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    for key in ["outfit_prompts_json", "outfit_paths_json", "backgrounds_json", "poses_json", "music_paths_json"]:
        d[key.removesuffix("_json")] = loads(d.pop(key, None), [])
    d["enabled"] = bool(d.get("enabled"))
    d["title_hint"] = d.get("title_hint") or ""
    d["caption_style"] = d.get("caption_style") or "engaging_short"
    d["ai_model"] = d.get("ai_model") or ""
    d["ai_provider"] = d.get("ai_provider") or "router9"
    d["persona_assets"] = {
        "original": {"path": d.get("persona_original_path"), "url": path_to_local_url(d.get("persona_original_path"))},
        "face_crop": {"path": d.get("persona_face_crop_path"), "url": path_to_local_url(d.get("persona_face_crop_path"))},
        "master_2048": {"path": d.get("persona_master_path"), "url": path_to_local_url(d.get("persona_master_path"))},
        "bust_2048": {"path": d.get("persona_bust_path"), "url": path_to_local_url(d.get("persona_bust_path"))},
        "left_2048": {"path": d.get("persona_left_master_path"), "url": path_to_local_url(d.get("persona_left_master_path"))},
        "right_2048": {"path": d.get("persona_right_master_path"), "url": path_to_local_url(d.get("persona_right_master_path"))},
        "back_2048": {"path": d.get("persona_back_master_path"), "url": path_to_local_url(d.get("persona_back_master_path"))},
    }
    d["persona_ready"] = bool(d.get("persona_master_path"))
    for angle in ("left","right","back"):
        d[f"persona_{angle}_enabled"] = bool(d.get(f"persona_{angle}_enabled", 1))
    d["persona_angle_count"] = sum(1 for k in ["persona_left_master_path","persona_right_master_path","persona_back_master_path"] if d.get(k))
    d["persona_angle_enabled_count"] = sum(1 for a in ("left","right","back") if d.get(f"persona_{a}_master_path") and d.get(f"persona_{a}_enabled", True))
    d["persona_pack_ready"] = bool(d.get("persona_master_path")) and d["persona_angle_count"] >= 3
    d["scheduler_enabled"] = bool(d.get("scheduler_enabled"))
    d["scheduler_dry_run"] = bool(d.get("scheduler_dry_run", 1))
    d["scheduler_warmup"] = bool(d.get("scheduler_warmup", 1))
    d["publish_interval_minutes"] = int(d.get("publish_interval_minutes") or 180)
    d["buffer_target"] = int(d.get("buffer_target") or 2)
    d["scheduler_config"] = loads(d.get("scheduler_config_json"), {})
    d["scheduler_mode"] = str(d["scheduler_config"].get("scheduler_mode") or "INTERVAL").upper()
    d["daily_slots"] = d["scheduler_config"].get("daily_slots") or ["08:00", "14:00", "21:00"]
    d["daily_random_minutes"] = int(30 if d["scheduler_config"].get("daily_random_minutes") is None else d["scheduler_config"].get("daily_random_minutes"))
    d["resume_random_minutes"] = int(30 if d["scheduler_config"].get("resume_random_minutes") is None else d["scheduler_config"].get("resume_random_minutes"))
    d["persona_angle_slots"] = {
        a: {
            "angle": a,
            "ready": bool(d.get(f"persona_{a}_master_path")),
            "enabled": bool(d.get(f"persona_{a}_enabled", True)),
            "path": d.get(f"persona_{a}_master_path"),
            "url": path_to_local_url(d.get(f"persona_{a}_master_path")),
        } for a in ("left","right","back")
    }
    if not include_secret_paths:
        d["persona_path"] = bool(d.get("persona_path"))
        d["persona_original_path"] = bool(d.get("persona_original_path"))
        d["persona_face_crop_path"] = bool(d.get("persona_face_crop_path"))
        d["persona_bust_path"] = bool(d.get("persona_bust_path"))
        d["persona_master_path"] = bool(d.get("persona_master_path"))
        d["persona_left_path"] = bool(d.get("persona_left_path"))
        d["persona_right_path"] = bool(d.get("persona_right_path"))
        d["persona_back_path"] = bool(d.get("persona_back_path"))
        d["persona_left_master_path"] = bool(d.get("persona_left_master_path"))
        d["persona_right_master_path"] = bool(d.get("persona_right_master_path"))
        d["persona_back_master_path"] = bool(d.get("persona_back_master_path"))
        d["outfit_paths"] = [Path(x).name for x in d.get("outfit_paths", [])]
        d["music_paths"] = [Path(x).name for x in d.get("music_paths", [])]
    return d


def list_page_profiles() -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM page_profiles ORDER BY enabled DESC,name ASC").fetchall()
    return [_profile_from_row(r) for r in rows]


def get_page_profile(profile_id: str) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM page_profiles WHERE id=?", (profile_id,)).fetchone()
    return _profile_from_row(row)


def save_page_profile(req: PageProfileSave) -> dict[str, Any]:
    profile_id = _slug(req.id or req.name)
    now = utcnow()
    outfits = _clean_list(req.outfit_prompts, DEFAULT_OUTFITS)
    outfit_paths = _clean_list(req.outfit_paths)
    backgrounds = _clean_list(req.backgrounds, DEFAULT_BACKGROUNDS)
    poses = _clean_list(req.poses, DEFAULT_POSES)
    music_paths = _clean_list(req.music_paths)
    mode = str(req.default_video_mode or "AUTO").upper()
    if mode not in {"AUTO", "IMAGE_BEAT", "IMAGE_TO_VIDEO"}:
        mode = "AUTO"
    body = req.body_preset if req.body_preset in BODY_PRESETS else "curvy_fit"
    persona_path = (req.persona_path or "").strip() or None
    persona_left_path = (req.persona_left_path or "").strip() or None
    persona_right_path = (req.persona_right_path or "").strip() or None
    persona_back_path = (req.persona_back_path or "").strip() or None
    with conn() as c:
        existing = c.execute("SELECT * FROM page_profiles WHERE id=?", (profile_id,)).fetchone()
        existing_d = dict(existing) if existing else {}
        main_changed = bool(persona_path and persona_path != (existing_d.get("persona_path") or ""))
        if not main_changed:
            persona_left_path = persona_left_path or existing_d.get("persona_left_path")
            persona_right_path = persona_right_path or existing_d.get("persona_right_path")
            persona_back_path = persona_back_path or existing_d.get("persona_back_path")
        else:
            # New FRONT identity => old generated angles are no longer valid.
            persona_left_path = None
            persona_right_path = None
            persona_back_path = None
        persona_original = existing_d.get("persona_original_path")
        persona_face_crop = existing_d.get("persona_face_crop_path")
        persona_bust = existing_d.get("persona_bust_path")
        persona_master = existing_d.get("persona_master_path")
        persona_left_master = None if main_changed else existing_d.get("persona_left_master_path")
        persona_right_master = None if main_changed else existing_d.get("persona_right_master_path")
        persona_back_master = None if main_changed else existing_d.get("persona_back_master_path")
        persona_prepared_at = existing_d.get("persona_prepared_at")
        if persona_path:
            changed = main_changed or not persona_master
            if changed:
                prepared = prepare_persona_assets(persona_path, profile_id)
                persona_original = prepared.get("persona_original_path")
                persona_face_crop = prepared.get("persona_face_crop_path")
                persona_bust = prepared.get("persona_bust_path")
                persona_master = prepared.get("persona_master_path")
                persona_left_master = prepared.get("persona_left_master_path")
                persona_right_master = prepared.get("persona_right_master_path")
                persona_back_master = prepared.get("persona_back_master_path")
                persona_prepared_at = prepared.get("persona_prepared_at")
        c.execute(
            """
            INSERT OR REPLACE INTO page_profiles(
              id,name,theme,persona_path,body_preset,sexiness_level,outfit_prompts_json,outfit_paths_json,
              backgrounds_json,poses_json,music_paths_json,default_video_mode,image_to_video_ratio,image_model,
              video_model,facebook_page_id,title_hint,caption_style,ai_model,ai_provider,enabled,created_at,updated_at,
              persona_original_path,persona_face_crop_path,persona_bust_path,persona_master_path,persona_left_path,persona_right_path,persona_back_path,
              persona_left_master_path,persona_right_master_path,persona_back_master_path,persona_prepared_at,
              scheduler_enabled,publish_interval_minutes,buffer_target,scheduler_dry_run,scheduler_warmup,next_publish_at,last_publish_at,scheduler_config_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                profile_id, req.name.strip(), req.theme.strip(), persona_path,
                body, int(req.sexiness_level), dumps(outfits), dumps(outfit_paths), dumps(backgrounds), dumps(poses),
                dumps(music_paths), mode, int(req.image_to_video_ratio), req.image_model, req.video_model,
                (req.facebook_page_id or "").strip() or None, req.title_hint.strip(), req.caption_style.strip() or "engaging_short",
                req.ai_model.strip(), req.ai_provider.strip() or "router9", 1 if req.enabled else 0,
                existing_d.get("created_at") if existing_d else now, now,
                persona_original, persona_face_crop, persona_bust, persona_master, persona_left_path, persona_right_path, persona_back_path,
                persona_left_master, persona_right_master, persona_back_master, persona_prepared_at,
                int(existing_d.get("scheduler_enabled") or 0), int(existing_d.get("publish_interval_minutes") or 180), int(existing_d.get("buffer_target") or 2),
                int(existing_d.get("scheduler_dry_run") if existing_d.get("scheduler_dry_run") is not None else 1),
                int(existing_d.get("scheduler_warmup") if existing_d.get("scheduler_warmup") is not None else 1),
                existing_d.get("next_publish_at"), existing_d.get("last_publish_at"), existing_d.get("scheduler_config_json"),
            ),
        )
    return get_page_profile(profile_id) or {}


def prepare_profile_persona(profile_id: str) -> dict[str, Any]:
    with conn() as c:
        row = c.execute("SELECT * FROM page_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            raise ValueError("Không thấy Page Profile")
        d = dict(row)
        persona_path = str(d.get("persona_path") or d.get("persona_original_path") or d.get("persona_master_path") or "").strip()
        if not persona_path:
            raise ValueError("Page Profile chưa có ảnh FRONT/original để rebuild")
        prepared = prepare_persona_assets(persona_path, profile_id, left_path=d.get("persona_left_path"), right_path=d.get("persona_right_path"), back_path=d.get("persona_back_path"))
        c.execute(
            "UPDATE page_profiles SET persona_original_path=?,persona_face_crop_path=?,persona_bust_path=?,persona_master_path=?,persona_left_master_path=?,persona_right_master_path=?,persona_back_master_path=?,persona_prepared_at=?,updated_at=? WHERE id=?",
            (prepared.get("persona_original_path"), prepared.get("persona_face_crop_path"), prepared.get("persona_bust_path"), prepared.get("persona_master_path"), prepared.get("persona_left_master_path"), prepared.get("persona_right_master_path"), prepared.get("persona_back_master_path"), prepared.get("persona_prepared_at"), utcnow(), profile_id),
        )
    return get_page_profile(profile_id) or {}


def _persona_angle_instruction(angle: str) -> str:
    specs = {
        "left": "three-quarter LEFT view, approximately 40 degrees turned to her left; both eyes still visible; show left cheek, ear area and side hair structure clearly",
        "right": "three-quarter RIGHT view, approximately 40 degrees turned to her right; both eyes still visible; show right cheek, ear area and side hair structure clearly",
        "back": "BACK view of head and upper shoulders, facing directly away from camera; clearly show rear hairstyle, hair volume, hair length, nape, neck and shoulder line; face must not be visible",
    }
    if angle not in specs:
        raise ValueError(f"Góc persona không hợp lệ: {angle}")
    return specs[angle]


def build_persona_angle_scene(profile: dict[str, Any], angle: str) -> dict[str, Any]:
    angle = str(angle or "").strip().lower()
    front = str(profile.get("persona_master_path") or profile.get("persona_path") or "").strip()
    if not front or not Path(front).exists():
        raise ValueError("Persona FRONT master chưa sẵn sàng")
    # Deliberately only use FRONT here. Using bust as a second ref caused Asset Picker/mediaId
    # failures on some Flow sessions and does not add much information for a head-angle reference.
    refs = [{"path": front, "name": Path(front).stem, "role": "person_front"}]
    base = (
        "Photorealistic identity reference portrait of the exact same adult woman, age 21+. "
        "Preserve exactly the same face shape, eyes, nose, lips, jawline, skin tone, hair color, hairline, hair parting, hair length, hair volume and hairstyle. "
        "Neutral soft studio background, even lighting, no text, no watermark, fully clothed, non-explicit. "
        "This image will become a permanent multi-angle identity reference, so do not redesign or beautify the person. "
    )
    return {
        "sceneId": 1,
        "imagePrompt": base + _persona_angle_instruction(angle) + ". Keep clothing simple and neutral so the head/hair silhouette is easy to reference.",
        "videoPrompt": "",
        "inputImages": refs,
        "metadata": {"personaAnglePack": True, "profileId": profile["id"], "personaAngle": angle},
    }


def build_persona_angle_scenes(profile: dict[str, Any], angles: list[str] | None = None) -> list[dict[str, Any]]:
    use = angles or ["left","right","back"]
    scenes=[]
    for idx, angle in enumerate(use, 1):
        sc=build_persona_angle_scene(profile, angle)
        sc["sceneId"] = idx
        scenes.append(sc)
    return scenes


def save_persona_angle_result(profile_id: str, angle: str, local_path: str) -> dict[str, Any]:
    angle = str(angle or "").strip().lower()
    if angle not in {"left","right","back"}:
        raise ValueError(f"Góc persona không hợp lệ: {angle}")
    prepared = prepare_persona_variant_assets(local_path, profile_id, angle)
    source_key=f"persona_{angle}_path"
    master_key=f"persona_{angle}_master_path"
    enabled_key=f"persona_{angle}_enabled"
    with conn() as c:
        c.execute(
            f"UPDATE page_profiles SET {source_key}=?, {master_key}=?, {enabled_key}=1, persona_prepared_at=?, updated_at=? WHERE id=?",
            (prepared.get(source_key) or local_path, prepared.get(master_key), utcnow(), utcnow(), profile_id),
        )
    return get_page_profile(profile_id) or {}


def _job_persona_angle(job: dict[str, Any]) -> tuple[str, str]:
    scenes = job.get("scenes") or []
    if not scenes:
        return "", ""
    meta = scenes[0].get("metadata") or {}
    return str(meta.get("profileId") or ""), str(meta.get("personaAngle") or "")


def find_active_persona_angle_job(profile_id: str, angle: str | None = None) -> dict[str, Any] | None:
    with conn() as c:
        rows=c.execute("SELECT * FROM flow_jobs WHERE kind IN ('persona_angle_pack','persona_angle') AND status IN ('queued','dispatching','running') ORDER BY created_at DESC LIMIT 100").fetchall()
    for row in rows:
        d=get_flow_job(dict(row)["id"])
        if not d:
            continue
        scenes=d.get("scenes") or []
        if not scenes:
            continue
        for sc in scenes:
            meta=sc.get("metadata") or {}
            if str(meta.get("profileId") or "") != profile_id:
                continue
            if angle and str(meta.get("personaAngle") or "") != angle:
                continue
            return d
    return None


def list_active_persona_angle_jobs(profile_id: str) -> dict[str, dict[str, Any]]:
    out={}
    for a in ("left","right","back"):
        j=find_active_persona_angle_job(profile_id,a)
        if j:
            out[a]={"id":j["id"],"status":j["status"]}
    return out


def delete_persona_angle(profile_id: str, angle: str) -> dict[str, Any]:
    angle=str(angle or "").strip().lower()
    if angle not in {"left","right","back"}:
        raise ValueError("Góc không hợp lệ")
    if find_active_persona_angle_job(profile_id, angle):
        raise RuntimeError(f"Góc {angle} đang generate; chờ job xong rồi xóa")
    profile=get_page_profile(profile_id)
    if not profile:
        raise ValueError("Không thấy Page Profile")
    paths=[profile.get(f"persona_{angle}_path"),profile.get(f"persona_{angle}_master_path")]
    with conn() as c:
        c.execute(f"UPDATE page_profiles SET persona_{angle}_path=NULL, persona_{angle}_master_path=NULL, persona_{angle}_enabled=0, updated_at=? WHERE id=?",(utcnow(),profile_id))
    base=(_persona_output_dir(profile_id)).resolve()
    for raw in paths:
        try:
            fp=Path(str(raw or "")).resolve()
            if fp.exists() and base in fp.parents:
                fp.unlink(missing_ok=True)
        except Exception:
            pass
    return get_page_profile(profile_id) or {}


def set_persona_angle_enabled(profile_id: str, angle: str, enabled: bool) -> dict[str, Any]:
    angle=str(angle or "").strip().lower()
    if angle not in {"left","right","back"}:
        raise ValueError("Góc không hợp lệ")
    profile=get_page_profile(profile_id)
    if not profile:
        raise ValueError("Không thấy Page Profile")
    if enabled and not profile.get(f"persona_{angle}_master_path"):
        raise ValueError(f"Góc {angle} chưa có ảnh để dùng")
    with conn() as c:
        c.execute(f"UPDATE page_profiles SET persona_{angle}_enabled=?,updated_at=? WHERE id=?",(1 if enabled else 0,utcnow(),profile_id))
    return get_page_profile(profile_id) or {}




def delete_page_profile(profile_id: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM page_profiles WHERE id=?", (profile_id,))




def router9_enabled() -> bool:
    return bool(ROUTER9_API_KEY)


def _model_family(model_id: str) -> str:
    low = str(model_id or "").lower()
    if "gemini" in low or low.startswith("google/") or low.startswith("vertex/"):
        return "gemini"
    if "gpt" in low or low.startswith("openai/") or low.startswith("codex/"):
        return "gpt"
    return "other"


def get_ai_model_status_map() -> dict[str, dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM ai_model_status").fetchall()
    return {str(r["model_id"]): dict(r) for r in rows}


def upsert_ai_model_status(model_id: str, family: str, status: str, *, latency_ms: int | None = None, error: str | None = None, disabled: int | None = None, hard_disabled: int | None = None, block_reason: str | None = None) -> None:
    now = utcnow()
    existing = get_ai_model_status_map().get(model_id) or {}
    if disabled is None:
        disabled = int(existing.get("disabled") or 0)
    if hard_disabled is None:
        hard_disabled = int(existing.get("hard_disabled") or 0)
    if block_reason is None:
        block_reason = existing.get("block_reason")
    with conn() as c:
        c.execute(
            """INSERT INTO ai_model_status(model_id,family,status,latency_ms,error,tested_at,updated_at,disabled,hard_disabled,block_reason)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(model_id) DO UPDATE SET family=excluded.family,status=excluded.status,
                 latency_ms=excluded.latency_ms,error=excluded.error,tested_at=excluded.tested_at,updated_at=excluded.updated_at,
                 disabled=excluded.disabled,hard_disabled=excluded.hard_disabled,block_reason=excluded.block_reason""",
            (model_id, family, status, latency_ms, error, now if status in {"ok","error"} else existing.get("tested_at"), now, int(disabled), int(hard_disabled), block_reason),
        )


def _hard_block_model(model_id: str, reason: str, *, error: str | None = None) -> None:
    family = _model_family(model_id)
    now = utcnow()
    with conn() as c:
        c.execute(
            """INSERT INTO ai_model_status(model_id,family,status,error,tested_at,updated_at,disabled,hard_disabled,block_reason)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(model_id) DO UPDATE SET family=excluded.family,status='error',error=COALESCE(excluded.error,ai_model_status.error),
                 tested_at=COALESCE(ai_model_status.tested_at,excluded.tested_at),updated_at=excluded.updated_at,disabled=1,hard_disabled=1,block_reason=excluded.block_reason""",
            (model_id, family, "error", error, now, now, 1, 1, reason),
        )
        c.execute("UPDATE page_profiles SET ai_model='',updated_at=? WHERE ai_model=?", (now, model_id))

def _router9_fetch_raw_models() -> list[dict[str, Any]]:
    if not router9_enabled():
        return []
    headers = {"Authorization": f"Bearer {ROUTER9_API_KEY}"}
    r = requests.get(ROUTER9_BASE_URL + "/models", headers=headers, timeout=(10, 30))
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:2000]}
    if not r.ok:
        raise RuntimeError(f"9router models HTTP {r.status_code}: {data}")
    rows = data.get("data") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def router9_models() -> list[dict[str, Any]]:
    rows = _router9_fetch_raw_models()
    statuses = get_ai_model_status_map()
    out=[]
    for x in rows:
        mid = str((x or {}).get("id") or "").strip()
        if not mid:
            continue
        owned_by = str((x or {}).get("owned_by") or "")
        # Hard policy: GitHub provider never enters UI/test/AUTO.
        if _is_github_model(mid, owned_by):
            _hard_block_model(mid, "github_provider_blocked")
            continue
        st = statuses.get(mid) or get_ai_model_status_map().get(mid) or {}
        if int(st.get("hard_disabled") or 0):
            continue
        family = _model_family(mid)
        out.append({
            "id": mid, "family": family, "owned_by": owned_by,
            "status": st.get("status") or "untested",
            "disabled": bool(st.get("disabled") or 0),
            "hard_disabled": False,
            "latency_ms": st.get("latency_ms"), "error": st.get("error"), "tested_at": st.get("tested_at"),
            "block_reason": st.get("block_reason"),
        })
    out.sort(key=lambda x: (0 if x["family"] in {"gpt","gemini"} else 1, x["family"], x["id"]))
    return out


def router9_model_policy_stats() -> dict[str, int]:
    raw = _router9_fetch_raw_models() if router9_enabled() else []
    github = sum(1 for x in raw if _is_github_model(str((x or {}).get("id") or ""), str((x or {}).get("owned_by") or "")))
    with conn() as c:
        hard = int(c.execute("SELECT COUNT(*) FROM ai_model_status WHERE COALESCE(hard_disabled,0)=1 AND block_reason<>'github_provider_blocked'").fetchone()[0])
        soft = int(c.execute("SELECT COUNT(*) FROM ai_model_status WHERE COALESCE(disabled,0)=1 AND COALESCE(hard_disabled,0)=0").fetchone()[0])
    return {"blocked_github": github, "hard_disabled": hard, "soft_disabled": soft}


def router9_usable_models() -> list[dict[str, Any]]:
    rows = router9_models()
    candidates = [m for m in rows if m["family"] in {"gpt", "gemini"} and not m.get("disabled")]
    ok_rows = [m for m in candidates if m.get("status") == "ok"]
    if ok_rows:
        return sorted(ok_rows, key=lambda m: (m.get("latency_ms") or 999999, m["id"]))
    any_tested = any(m.get("status") in {"ok", "error"} for m in candidates)
    if any_tested:
        return []
    return [m for m in candidates if m.get("status") == "untested"]


def disable_failed_router9_models() -> dict[str, int]:
    with conn() as c:
        rows = c.execute("SELECT model_id,error,COALESCE(disabled,0) disabled,COALESCE(hard_disabled,0) hard_disabled FROM ai_model_status WHERE status='error'").fetchall()
    soft_ids=[]; hard_ids=[]
    for r in rows:
        mid=str(r["model_id"]); err=str(r["error"] or "")
        if _is_github_model(mid) or _is_permanent_model_error(err):
            hard_ids.append(mid)
        elif not int(r["disabled"] or 0):
            soft_ids.append(mid)
    for mid in hard_ids:
        _hard_block_model(mid, "github_provider_blocked" if _is_github_model(mid) else "model_not_supported")
    if soft_ids:
        now=utcnow()
        with conn() as c:
            c.executemany("UPDATE ai_model_status SET disabled=1,updated_at=? WHERE model_id=?", [(now, mid) for mid in soft_ids])
            c.executemany("UPDATE page_profiles SET ai_model='',updated_at=? WHERE ai_model=?", [(now, mid) for mid in soft_ids])
    return {"soft_disabled": len(soft_ids), "hard_disabled": len(hard_ids)}


def reset_disabled_router9_models() -> int:
    # Permanent blocks (GitHub / model_not_supported) are intentionally NOT restored.
    with conn() as c:
        cur = c.execute("UPDATE ai_model_status SET disabled=0,updated_at=? WHERE COALESCE(disabled,0)=1 AND COALESCE(hard_disabled,0)=0", (utcnow(),))
        return int(cur.rowcount or 0)


def _router9_response_content(response: requests.Response) -> tuple[str, dict[str, Any]]:
    """Parse both normal JSON and SSE responses returned by 9Router/providers."""
    text = response.text or ""
    ctype = (response.headers.get("content-type") or "").lower()
    # Normal OpenAI JSON
    if "text/event-stream" not in ctype and not text.lstrip().startswith("data:"):
        try:
            data = response.json()
        except Exception:
            data = {"raw": text[:4000]}
        if isinstance(data, dict):
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            if not content:
                content = (((data.get("choices") or [{}])[0].get("delta") or {}).get("content") or "")
            return str(content or "").strip(), data
        return "", {"data": data}

    # SSE / streaming OpenAI chunks
    parts: list[str] = []
    chunks: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        if isinstance(obj, dict):
            chunks.append(obj)
            choice = (obj.get("choices") or [{}])[0] or {}
            delta = choice.get("delta") or {}
            msg = choice.get("message") or {}
            piece = delta.get("content") if isinstance(delta, dict) else None
            if piece is None and isinstance(msg, dict):
                piece = msg.get("content")
            if piece:
                parts.append(str(piece))
    return "".join(parts).strip(), {"stream": True, "chunks": chunks[-8:], "raw": text[:4000]}


def test_router9_model_sync(model_id: str) -> dict[str, Any]:
    model_id = str(model_id or "").strip()
    if not model_id:
        raise ValueError("Thiếu model_id")
    if _is_github_model(model_id):
        _hard_block_model(model_id, "github_provider_blocked")
        return {"ok": False, "model_id": model_id, "status": "blocked", "hard_disabled": True, "error": "GitHub provider bị chặn theo policy"}
    family = _model_family(model_id)
    existing = get_ai_model_status_map().get(model_id) or {}
    if int(existing.get("hard_disabled") or 0):
        return {"ok": False, "model_id": model_id, "status": "blocked", "hard_disabled": True, "error": existing.get("block_reason") or "Model bị permanent block"}
    # Retest thủ công chỉ mở lại SOFT-disabled model.
    upsert_ai_model_status(model_id, family, "testing", latency_ms=None, error=None, disabled=0, hard_disabled=0, block_reason=None)
    started = time.perf_counter()
    try:
        headers = {"Authorization": f"Bearer {ROUTER9_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": model_id,
            "temperature": 0,
            "max_tokens": 96,
            "stream": False,
            "messages": [{"role": "user", "content": "Reply exactly with: OK"}],
        }
        r = requests.post(ROUTER9_BASE_URL + "/chat/completions", headers=headers, json=payload, timeout=(15, min(90, ROUTER9_TIMEOUT)))
        latency = int((time.perf_counter()-started)*1000)
        content, data = _router9_response_content(r)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {data}")
        if not content:
            raise RuntimeError(f"Không có content sau khi parse JSON/SSE: {data}")
        upsert_ai_model_status(model_id, family, "ok", latency_ms=latency, error=None, disabled=0)
        return {"ok": True, "model_id": model_id, "family": family, "status": "ok", "latency_ms": latency, "content": content[:240]}
    except Exception as exc:
        latency = int((time.perf_counter()-started)*1000)
        err = str(exc)[:2000]
        if _is_permanent_model_error(err):
            _hard_block_model(model_id, "model_not_supported", error=err)
            return {"ok": False, "model_id": model_id, "family": family, "status": "blocked", "hard_disabled": True, "latency_ms": latency, "error": err}
        upsert_ai_model_status(model_id, family, "error", latency_ms=latency, error=err, disabled=0, hard_disabled=0)
        return {"ok": False, "model_id": model_id, "family": family, "status": "error", "latency_ms": latency, "error": err}


def test_router9_models_background(model_ids: list[str]) -> None:
    for mid in model_ids:
        try:
            test_router9_model_sync(mid)
        except Exception as exc:
            upsert_ai_model_status(mid, _model_family(mid), "error", error=str(exc)[:2000])


def _extract_json_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


def router9_chat_json(*, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.7, timeout_seconds: int | None = None, max_tokens: int | None = None, allow_model_fallback: bool = False) -> dict[str, Any]:
    if not router9_enabled():
        raise RuntimeError("ROUTER9_API_KEY chưa cấu hình")
    requested_model = str(model or "").strip()
    if requested_model and _is_github_model(requested_model):
        _hard_block_model(requested_model, "github_provider_blocked")
        raise RuntimeError(f"GitHub provider bị chặn: {requested_model}")

    # AUTO mode may fail on one slow provider. For resilient workloads (Auto FB pool/editor),
    # try up to 3 currently usable models before failing the whole chunk. Explicit model stays fixed.
    if requested_model:
        status = get_ai_model_status_map().get(requested_model) or {}
        if status.get("hard_disabled"):
            raise RuntimeError(f"Model 9router đã bị PERMANENT BLOCK: {requested_model} · {status.get('block_reason') or ''}")
        if status.get("disabled"):
            raise RuntimeError(f"Model 9router đã bị CLEAR/DISABLE: {requested_model}")
        if status.get("status") == "error":
            raise RuntimeError(f"Model 9router đã TEST LỖI và bị khóa: {requested_model}. Hãy test lại hoặc chọn model xanh.")
        model_ids=[requested_model]
    else:
        usable=router9_usable_models()
        model_ids=[str(x.get("id") or "") for x in usable if str(x.get("id") or "").strip()]
        if not allow_model_fallback:
            model_ids=model_ids[:1]
        else:
            model_ids=model_ids[:max(1,min(3,int(os.getenv("ROUTER9_AUTO_FALLBACK_LIMIT","2") or 2)))]
    if not model_ids:
        raise RuntimeError("9router không trả model nào từ GET /v1/models")

    url = ROUTER9_BASE_URL + "/chat/completions"
    headers = {"Authorization": f"Bearer {ROUTER9_API_KEY}", "Content-Type": "application/json"}
    read_timeout=max(20, int(timeout_seconds or ROUTER9_TIMEOUT))
    token_cap=max(256, int(max_tokens or 1400))
    errors=[]
    for active_model in model_ids:
        payload = {
            "model": active_model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "max_tokens": token_cap,
        }
        last_data: dict[str, Any] = {}
        try:
            for with_json_mode in (True, False):
                body = dict(payload)
                if with_json_mode:
                    body["response_format"] = {"type": "json_object"}
                try:
                    r = requests.post(url, headers=headers, json=body, timeout=(20, read_timeout))
                except requests.Timeout as exc:
                    raise RuntimeError(f"9router timeout model={active_model} sau {read_timeout}s") from exc
                except requests.RequestException as exc:
                    raise RuntimeError(f"9router network model={active_model}: {exc}") from exc
                content, parsed = _router9_response_content(r)
                last_data = parsed if isinstance(parsed, dict) else {"data": parsed}
                if r.ok:
                    if not content:
                        raise RuntimeError(f"9router rỗng sau JSON/SSE parse: {last_data}")
                    try:
                        return json.loads(_extract_json_text(content))
                    except Exception as exc:
                        raise RuntimeError(f"9router JSON parse lỗi model={active_model}: {content[:500]}") from exc
                if r.status_code not in {400, 404, 422} or not with_json_mode:
                    raise RuntimeError(f"9router HTTP {r.status_code} model={active_model}: {last_data}")
            raise RuntimeError(f"9router lỗi model={active_model}: {last_data}")
        except Exception as exc:
            errors.append(str(exc))
            if requested_model or not allow_model_fallback:
                raise
            continue
    raise RuntimeError("9router tất cả model thử đều lỗi: " + " | ".join(errors[-3:]))


def fallback_content_plan(profile: dict[str, Any], *, theme: str, body: str, sexy_level: int, outfit_pool: list[str], background_pool: list[str], pose_pool: list[str], final_index: int, mode: str) -> dict[str, Any]:
    outfit = random.choice(outfit_pool)
    bg = random.choice(background_pool)
    pose = random.choice(pose_pool)
    title_hint = str(profile.get("title_hint") or profile.get("theme") or profile.get("name") or "")
    sexy_word = "cuốn hút" if sexy_level >= 70 else "năng động" if sexy_level >= 45 else "nhẹ nhàng"
    title = f"{title_hint or profile.get('name')}: {sexy_word} mỗi ngày"[:98]
    hashtags = ["#reels", "#viral", "#fyp", "#gymgirl", "#beauty", "#fashion"]
    caption = f"{sexy_word.title()} vibe hôm nay ✨ {theme}. Outfit: {outfit}. {profile.get('name')} giữ đúng gương mặt, đổi outfit và background để video đa dạng hơn. {' '.join(hashtags)}"
    return {
        "title": title,
        "caption": caption[:1800],
        "hashtags": hashtags,
        "outfit": outfit,
        "background": bg,
        "pose": pose,
        "style_note": sexy_word,
        "ai_used": False,
    }


def generate_content_plan(profile: dict[str, Any], *, theme: str, body: str, sexy_level: int, outfit_pool: list[str], background_pool: list[str], pose_pool: list[str], final_index: int, mode: str) -> dict[str, Any]:
    fallback = fallback_content_plan(profile, theme=theme, body=body, sexy_level=sexy_level, outfit_pool=outfit_pool, background_pool=background_pool, pose_pool=pose_pool, final_index=final_index, mode=mode)
    model = (profile.get("ai_model") or "").strip()
    if not router9_enabled():
        return fallback
    system_prompt = (
        "You plan short-form social content for adult glamour/fitness/lifestyle pages. "
        "Always keep the subject adult 21+, fully clothed, non-explicit, social-safe and non-nude. "
        "Return compact JSON only."
    )
    user_prompt = (
        f"Page: {profile.get('name')}\nTheme: {theme}\nTitle hint: {profile.get('title_hint') or ''}\n"
        f"Caption style: {profile.get('caption_style') or 'engaging_short'}\nBody preset: {profile.get('body_preset')} => {body}\n"
        f"Sexy level: {sexy_level}/100 (make it glamorous/attractive but still fully clothed and non-explicit).\n"
        f"Video mode: {mode}\nExisting outfit ideas (for style reference only): {json.dumps(outfit_pool, ensure_ascii=False)}\n"
        f"Candidate backgrounds: {json.dumps(background_pool, ensure_ascii=False)}\n"
        f"Candidate poses: {json.dumps(pose_pool, ensure_ascii=False)}\n"
        "DESIGN A NEW OUTFIT for this video instead of merely selecting an existing line. Prioritize short bottoms such as short gym shorts, mini skorts or short tailored shorts, and breathable cool tops such as crop tops, sleeveless tops, halter tops, fitted camisole-style tops or square-neck tops. "
        "The outfit should be glamorous, highly eye-catching, cooling and summer-friendly for an adult 21+ creator, and strongly figure-flattering around a fuller bust, waist and hips, but fully clothed, opaque, non-transparent, non-explicit, and realistic enough to wear in public. "
        "Vary color, neckline, sleeve style, waistline, short-bottom silhouette, material and accessories across videos. "
        "Return JSON with keys: title, caption, hashtags (array 3-8), outfit, background, pose, style_note. "
        "Title under 90 chars. Caption 1-3 short lines, suitable for Facebook Reels, can include hashtags. "
        "Choose background/pose from the lists or a very close safe variation. Outfit may be newly invented."
    )
    try:
        out = router9_chat_json(model=model, system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.8)
        plan = {**fallback, **(out or {})}
        # sanitize
        plan["title"] = str(plan.get("title") or fallback["title"])[:95]
        plan["caption"] = str(plan.get("caption") or fallback["caption"])[:1800]
        plan["outfit"] = str(plan.get("outfit") or fallback["outfit"])
        plan["background"] = str(plan.get("background") or fallback["background"])
        plan["pose"] = str(plan.get("pose") or fallback["pose"])
        tags = plan.get("hashtags")
        if not isinstance(tags, list):
            tags = fallback["hashtags"]
        plan["hashtags"] = [str(x).strip() for x in tags if str(x).strip()][:8] or fallback["hashtags"]
        plan["ai_used"] = True
        plan["ai_model"] = model
        return plan
    except Exception as exc:
        plan = dict(fallback)
        plan["ai_error"] = str(exc)
        return plan


def _sexiness_clause(level: int) -> str:
    level = max(0, min(100, int(level)))
    if level < 30:
        return "fashionable, flattering and fully clothed styling"
    if level < 60:
        return "confident fitted fashion that flatters the adult figure, fully clothed, non-transparent"
    if level < 80:
        return "bold glamorous fitted styling, attractive and figure-flattering, fully clothed, opaque, non-explicit"
    return "high-glamour figure-hugging adult fashion, strongly flattering the bust, waist and hips while remaining fully clothed, opaque and non-explicit"


def _pick_existing(paths: list[str]) -> str | None:
    valid = [p for p in paths if Path(p).exists()]
    return random.choice(valid) if valid else None


def _factory_meta(profile: dict[str, Any], run_id: str, final_index: int, mode: str, *, music_path: str | None,
                  beat_duration: float, motion_preset: str, expected_count: int, auto_publish: bool,
                  facebook_dry_run: bool) -> dict[str, Any]:
    return {
        "factoryV2": {
            "runId": run_id,
            "profileId": profile["id"],
            "profileName": profile["name"],
            "finalIndex": final_index,
            "mode": mode,
            "musicPath": music_path,
            "beatDurationSec": beat_duration,
            "motionPreset": motion_preset,
            "expectedCount": expected_count,
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "autoPublish": bool(auto_publish),
            "facebookDryRun": bool(facebook_dry_run),
            "facebookPageId": profile.get("facebook_page_id"),
        }
    }


def build_factory_v2_job(profile: dict[str, Any], req: FactoryV2GenerateRequest, run_id: str, final_index: int, mode: str) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    persona = str(profile.get("persona_master_path") or profile.get("persona_path") or "").strip()
    if not persona:
        raise ValueError(f"Page '{profile['name']}' chưa có persona master")
    if not Path(persona).exists():
        raise ValueError(f"Persona master không tồn tại: {persona}")

    body = BODY_PRESETS.get(profile.get("body_preset") or "curvy_fit", BODY_PRESETS["curvy_fit"])
    sexy = _sexiness_clause(int(profile.get("sexiness_level") or 60))
    outfit_prompts = _clean_list(profile.get("outfit_prompts"), DEFAULT_OUTFITS)
    backgrounds = _clean_list(profile.get("backgrounds"), DEFAULT_BACKGROUNDS)
    poses = _clean_list(profile.get("poses"), DEFAULT_POSES)
    music_path = _pick_existing(_clean_list(profile.get("music_paths")))
    outfit_ref = _pick_existing(_clean_list(profile.get("outfit_paths")))
    theme = str(profile.get("theme") or "adult glamour lifestyle")
    plan = generate_content_plan(
        profile, theme=theme, body=body, sexy_level=int(profile.get("sexiness_level") or 60),
        outfit_pool=outfit_prompts, background_pool=backgrounds, pose_pool=poses, final_index=final_index, mode=mode
    )
    selected_outfit = str(plan.get("outfit") or random.choice(outfit_prompts))
    refs = normalize_persona_pack(profile, outfit_ref)

    if mode == "IMAGE_TO_VIDEO":
        count = int(req.i2v_clip_count)
    else:
        count = int(req.beat_image_count)

    meta_base = _factory_meta(
        profile, run_id, final_index, mode, music_path=music_path,
        beat_duration=float(req.beat_duration_sec), motion_preset=req.beat_motion_preset,
        expected_count=count, auto_publish=req.auto_publish, facebook_dry_run=req.facebook_dry_run,
    )
    meta_base.update({
        "title": str(plan.get("title") or profile.get("name") or ""),
        "caption": str(plan.get("caption") or ""),
        "hashtags": plan.get("hashtags") or [],
        "styleNote": str(plan.get("style_note") or ""),
        "aiUsed": bool(plan.get("ai_used")),
        "aiModel": str(plan.get("ai_model") or profile.get("ai_model") or ""),
        "titleHint": str(profile.get("title_hint") or ""),
        "personaMasterPath": persona,
        "personaBustPath": str(profile.get("persona_bust_path") or ""),
        "personaPreparedAt": str(profile.get("persona_prepared_at") or ""),
        "personaLeftPath": str(profile.get("persona_left_master_path") or ""),
        "personaRightPath": str(profile.get("persona_right_master_path") or ""),
        "personaBackPath": str(profile.get("persona_back_master_path") or ""),
    })

    scenes: list[dict[str, Any]] = []
    preferred_bg = str(plan.get("background") or "").strip()
    preferred_pose = str(plan.get("pose") or "").strip()
    bg_order = random.sample(backgrounds, min(len(backgrounds), count)) if len(backgrounds) >= count else [random.choice(backgrounds) for _ in range(count)]
    pose_order = random.sample(poses, min(len(poses), count)) if len(poses) >= count else [random.choice(poses) for _ in range(count)]
    if preferred_bg:
        bg_order[0] = preferred_bg
    if preferred_pose:
        pose_order[0] = preferred_pose
    motion_order = random.sample(VIDEO_MOTIONS, min(len(VIDEO_MOTIONS), count)) if len(VIDEO_MOTIONS) >= count else [random.choice(VIDEO_MOTIONS) for _ in range(count)]

    for i in range(count):
        outfit_clause = (
            "Keep the clothing faithfully consistent with the uploaded outfit reference."
            if outfit_ref else f"Wardrobe: {selected_outfit}."
        )
        angle_clause = "Use the full multi-angle persona pack when available: front, bust, left, right and back references. Keep hair parting, hair volume, ear visibility, neck, shoulder line and back-of-head appearance consistent across all views."
        image_prompt = (
            f"Photorealistic Vietnamese adult woman, age 21+, East Asian ethnicity, fair natural skin tone, same exact identity, face, hairstyle and recognizable facial features as the person reference. "
            f"{body}. Theme: {theme}. Styling: {sexy}. {outfit_clause} {angle_clause} "
            f"Scene {i+1}: {bg_order[i]}; pose: {pose_order[i]}. "
            "Natural realistic skin texture, attractive social-media photography, full body or three-quarter body, vertical 9:16 composition, "
            "realistic anatomy, no text, no watermark, no nudity, no transparent clothing."
        )
        video_prompt = ""
        if mode == "IMAGE_TO_VIDEO":
            video_prompt = (
                f"Use the attached generated image as the exact first-frame identity reference. {motion_order[i]} "
                "Preserve East Asian ethnicity, fair skin tone, face, outfit and body identity. "
                "Keep the exact same adult woman's face, body proportions, hairstyle, back-of-head appearance, outfit, background and lighting. "
                "If the subject turns or rotates, preserve the same hair shape, hair parting, side profile and rear hair consistency from the multi-angle reference pack. "
                "Natural realistic body movement and cloth physics. No talking, no lip-sync, no scene redesign, no morphing. Vertical social video."
            )
        metadata = {
            **meta_base,
            "variation": i + 1,
            "selectedOutfit": selected_outfit,
            "outfitReference": outfit_ref,
            "background": bg_order[i],
            "pose": pose_order[i],
            "adultOnly": True,
        }
        scenes.append({
            "sceneId": i + 1,
            "imagePrompt": image_prompt,
            "videoPrompt": video_prompt,
            "inputImages": refs,
            "metadata": metadata,
        })

    if mode == "IMAGE_TO_VIDEO":
        flow = default_flow_config(
            imageModel=profile.get("image_model") or "Nano Banana 2",
            videoModel=profile.get("video_model") or "Veo 3.1 - Fast",
            imageConcurrency=min(req.image_concurrency, 10),
            videoConcurrency=min(req.video_concurrency, 10),
            submitPolicy="VIDEO_LIGHT",
            autoDownloadVideo=True,
            aspectRatio="9:16",
            imageOutputs="x1",
            videoDuration=req.i2v_clip_duration,
            videoOutputs="x1",
            videoExtendFactor="x1",
            maxSubmitsPerMinute=min(8, max(2, req.video_concurrency + 2)),
            submitGapMs=850,
            videoTimeoutSec=900,
        )
        kind = "factory_v2_i2v"
    else:
        flow = default_flow_config(
            imageModel=profile.get("image_model") or "Nano Banana 2",
            videoModel="NONE",
            imageConcurrency=min(req.image_concurrency, 10),
            aspectRatio="9:16",
            imageOutputs="x1",
            maxSubmitsPerMinute=min(10, max(2, req.image_concurrency)),
        )
        kind = "factory_v2_beat"
    return scenes, flow, kind


def choose_factory_mode(profile: dict[str, Any], requested: str) -> str:
    requested = str(requested or "AUTO").upper()
    if requested in {"IMAGE_BEAT", "IMAGE_TO_VIDEO"}:
        return requested
    default = str(profile.get("default_video_mode") or "AUTO").upper()
    if default in {"IMAGE_BEAT", "IMAGE_TO_VIDEO"}:
        return default
    ratio = max(0, min(100, int(profile.get("image_to_video_ratio") or 25)))
    return "IMAGE_TO_VIDEO" if random.randrange(100) < ratio else "IMAGE_BEAT"


def create_factory_run(profile_id: str, req: FactoryV2GenerateRequest, jobs: list[str]) -> str:
    run_id = f"factory_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    with conn() as c:
        c.execute(
            "INSERT INTO factory_runs(id,page_profile_id,requested_count,requested_mode,auto_publish,facebook_dry_run,job_ids_json,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, profile_id, req.videos, req.mode.upper(), 1 if req.auto_publish else 0, 1 if req.facebook_dry_run else 0, dumps(jobs), dumps(req.model_dump()), utcnow(), utcnow()),
        )
    return run_id


def _factory_run_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    job_ids = loads(d.pop("job_ids_json"), [])
    d["job_ids"] = job_ids
    d["config"] = loads(d.pop("config_json"), {})
    d["auto_publish"] = bool(d["auto_publish"])
    d["facebook_dry_run"] = bool(d["facebook_dry_run"])
    statuses = []
    for jid in job_ids:
        j = get_flow_job(jid)
        if j:
            statuses.append({"id": jid, "kind": j["kind"], "status": j["status"], "error": j.get("error")})
    d["jobs"] = statuses
    d["done"] = sum(1 for j in statuses if j["status"] in {"done", "qc_passed", "published", "dry_run_ok"})
    d["failed"] = sum(1 for j in statuses if j["status"] in {"failed", "qc_failed", "partial_failed", "interrupted"})
    d["active"] = sum(1 for j in statuses if j["status"] in {"queued", "dispatching", "running", "flow_done", "rendering", "qc"})
    d["status"] = "done" if statuses and d["done"] + d["failed"] >= len(statuses) else "running" if d["active"] else "queued"
    return d


def list_factory_runs(limit: int = 50) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM factory_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_factory_run_row(r) for r in rows]


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def _queue_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def get_content_queue_by_flow(flow_job_id: str) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM content_queue WHERE flow_job_id=?", (flow_job_id,)).fetchone()
    return _queue_row(row) if row else None


def create_content_queue_item(profile_id: str, flow_job_id: str) -> str:
    qid = f"cq_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    now = utcnow()
    with conn() as c:
        c.execute(
            "INSERT INTO content_queue(id,page_profile_id,flow_job_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (qid, profile_id, flow_job_id, "generating", now, now),
        )
    return qid


def update_content_queue_by_flow(flow_job_id: str, **fields: Any) -> None:
    fields["updated_at"] = utcnow()
    cols = ",".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [flow_job_id]
    with conn() as c:
        c.execute(f"UPDATE content_queue SET {cols} WHERE flow_job_id=?", vals)


def list_content_queue(profile_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM content_queue WHERE page_profile_id=? ORDER BY created_at DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _scheduler_local_now() -> datetime:
    return datetime.now(SCHEDULER_TZ)


def _scheduler_cfg(profile: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(profile.get("scheduler_config") or {})
    cfg["scheduler_mode"] = str(cfg.get("scheduler_mode") or "INTERVAL").upper()
    if cfg["scheduler_mode"] not in {"INTERVAL", "DAILY_SLOTS"}:
        cfg["scheduler_mode"] = "INTERVAL"
    cfg["daily_slots"] = _normalize_daily_slots(cfg.get("daily_slots") or ["08:00", "14:00", "21:00"])
    cfg["daily_random_minutes"] = max(0, min(180, int(30 if cfg.get("daily_random_minutes") is None else cfg.get("daily_random_minutes"))))
    cfg["resume_random_minutes"] = max(0, min(180, int(30 if cfg.get("resume_random_minutes") is None else cfg.get("resume_random_minutes"))))
    return cfg


def _normalize_daily_slots(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").replace(";", ",").split(",")
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
        if not m:
            continue
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            val = f"{hh:02d}:{mm:02d}"
            if val not in out:
                out.append(val)
    return sorted(out) or ["08:00", "14:00", "21:00"]


def _save_scheduler_cfg(profile_id: str, cfg: dict[str, Any], *, next_at: datetime | None = None) -> None:
    fields = {"scheduler_config_json": dumps(cfg), "updated_at": utcnow()}
    if next_at is not None:
        fields["next_publish_at"] = next_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    cols = ",".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE page_profiles SET {cols} WHERE id=?", list(fields.values()) + [profile_id])


def _plan_dt_local(entry: dict[str, Any]) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(entry.get("at") or ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SCHEDULER_TZ)
        return dt.astimezone(SCHEDULER_TZ)
    except Exception:
        return None


def _generate_daily_plan(cfg: dict[str, Any], day_local, *, preserve_states: dict[str, str] | None = None) -> list[dict[str, Any]]:
    slots = _normalize_daily_slots(cfg.get("daily_slots"))
    jitter = max(0, min(180, int(30 if cfg.get("daily_random_minutes") is None else cfg.get("daily_random_minutes"))))
    plan: list[dict[str, Any]] = []
    for slot in slots:
        hh, mm = [int(x) for x in slot.split(":", 1)]
        base = datetime(day_local.year, day_local.month, day_local.day, hh, mm, tzinfo=SCHEDULER_TZ)
        offset = random.randint(-jitter, jitter) if jitter else 0
        at = base + timedelta(minutes=offset)
        state = (preserve_states or {}).get(slot, "pending")
        plan.append({"slot": slot, "at": at.isoformat(timespec="seconds"), "offset_minutes": offset, "state": state})
    plan.sort(key=lambda x: x["at"])
    return plan


def _ensure_daily_plan(profile_id: str, profile: dict[str, Any], cfg: dict[str, Any], now_local: datetime | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now_local = now_local or _scheduler_local_now()
    day_key = now_local.date().isoformat()
    plan = cfg.get("daily_plan") if isinstance(cfg.get("daily_plan"), list) else []
    stored_day = str(cfg.get("daily_plan_date") or "")
    same_day = stored_day == day_key
    same_signature = cfg.get("daily_plan_signature") == {
        "slots": _normalize_daily_slots(cfg.get("daily_slots")),
        "random": int(30 if cfg.get("daily_random_minutes") is None else cfg.get("daily_random_minutes")),
    }
    future_day_plan = bool(plan and same_signature and stored_day and stored_day > day_key)
    if future_day_plan:
        return cfg, plan
    if not (same_day and same_signature and plan):
        plan = _generate_daily_plan(cfg, now_local.date())
        cfg["daily_plan_date"] = day_key
        cfg["daily_plan_signature"] = {"slots": _normalize_daily_slots(cfg.get("daily_slots")), "random": int(30 if cfg.get("daily_random_minutes") is None else cfg.get("daily_random_minutes"))}
        cfg["daily_plan"] = plan
        _save_scheduler_cfg(profile_id, cfg)
    return cfg, plan


def _next_daily_entry(profile_id: str, profile: dict[str, Any], cfg: dict[str, Any], now_local: datetime | None = None, *, startup_reconcile: bool = False) -> tuple[dict[str, Any], dict[str, Any], datetime]:
    now_local = now_local or _scheduler_local_now()
    cfg, plan = _ensure_daily_plan(profile_id, profile, cfg, now_local)
    pending = [e for e in plan if str(e.get("state") or "pending") == "pending" and _plan_dt_local(e)]
    due = [e for e in pending if _plan_dt_local(e) <= now_local]
    future = [e for e in pending if _plan_dt_local(e) > now_local]
    if due:
        # Do not burst multiple missed posts after downtime. Keep only latest missed slot as catch-up.
        latest = max(due, key=lambda e: _plan_dt_local(e))
        for e in due:
            if e is not latest:
                e["state"] = "skipped"
                e["skip_reason"] = "missed_while_server_off"
        if startup_reconcile or not latest.get("catchup_at"):
            jitter = max(0, int(30 if cfg.get("resume_random_minutes") is None else cfg.get("resume_random_minutes")))
            catchup = now_local + timedelta(minutes=random.randint(0, jitter) if jitter else 0)
            latest["catchup_at"] = catchup.isoformat(timespec="seconds")
        target = datetime.fromisoformat(latest["catchup_at"]).astimezone(SCHEDULER_TZ)
        cfg["daily_plan"] = plan
        _save_scheduler_cfg(profile_id, cfg, next_at=target)
        return cfg, latest, target
    if future:
        nxt = min(future, key=lambda e: _plan_dt_local(e))
        target = _plan_dt_local(nxt)
        _save_scheduler_cfg(profile_id, cfg, next_at=target)
        return cfg, nxt, target
    # Today finished: create tomorrow plan now so next_publish_at survives overnight/restart.
    tomorrow = now_local.date() + timedelta(days=1)
    plan = _generate_daily_plan(cfg, tomorrow)
    cfg["daily_plan_date"] = tomorrow.isoformat()
    cfg["daily_plan_signature"] = {"slots": _normalize_daily_slots(cfg.get("daily_slots")), "random": int(30 if cfg.get("daily_random_minutes") is None else cfg.get("daily_random_minutes"))}
    cfg["daily_plan"] = plan
    nxt = plan[0]
    target = _plan_dt_local(nxt)
    _save_scheduler_cfg(profile_id, cfg, next_at=target)
    return cfg, nxt, target


def _effective_last_publish(profile: dict[str, Any]) -> datetime | None:
    candidates: list[datetime] = []
    stored = _parse_iso_utc(profile.get("last_publish_at"))
    if stored:
        candidates.append(stored)
    page_id = str(profile.get("facebook_page_id") or "").strip()
    if page_id:
        with conn() as c:
            row = c.execute(
                "SELECT updated_at,created_at FROM publish_jobs WHERE page_id=? AND status IN ('submitted','dry_run_ok') ORDER BY updated_at DESC LIMIT 1",
                (page_id,),
            ).fetchone()
        if row:
            dt = _parse_iso_utc(row["updated_at"] or row["created_at"])
            if dt:
                candidates.append(dt)
    return max(candidates) if candidates else None


def _reconcile_interval_next(profile_id: str, profile: dict[str, Any], cfg: dict[str, Any], *, startup: bool = False, now_utc: datetime | None = None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    interval = int(profile.get("publish_interval_minutes") or 180)
    last = _effective_last_publish(profile)
    stored_last = _parse_iso_utc(profile.get("last_publish_at"))
    if last and (not stored_last or last > stored_last):
        with conn() as c:
            c.execute("UPDATE page_profiles SET last_publish_at=?,updated_at=? WHERE id=?", (last.isoformat(timespec="seconds"), utcnow(), profile_id))
    stored_next = _parse_iso_utc(profile.get("next_publish_at"))
    due = (last + timedelta(minutes=interval)) if last else stored_next
    if due is None:
        due = now
    if due <= now and startup:
        jitter = max(0, int(30 if cfg.get("resume_random_minutes") is None else cfg.get("resume_random_minutes")))
        due = now + timedelta(minutes=random.randint(0, jitter) if jitter else 0)
        _scheduler_set_next(profile_id, due)
        cfg["resume_due_from"] = (last + timedelta(minutes=interval)).isoformat(timespec="seconds") if last else None
        cfg["resume_planned_at"] = due.isoformat(timespec="seconds")
        _save_scheduler_cfg(profile_id, cfg, next_at=due)
    elif stored_next is None or abs((stored_next - due).total_seconds()) > 2:
        _scheduler_set_next(profile_id, due)
    return due


def scheduler_status(profile_id: str) -> dict[str, Any]:
    profile = get_page_profile(profile_id)
    if not profile:
        raise ValueError("Không thấy Page Profile")
    with conn() as c:
        rows = c.execute(
            "SELECT status,COUNT(*) n FROM content_queue WHERE page_profile_id=? GROUP BY status",
            (profile_id,),
        ).fetchall()
    counts = {str(r["status"]): int(r["n"]) for r in rows}
    active = counts.get("generating", 0) + counts.get("ready", 0) + counts.get("publishing", 0)
    cfg = _scheduler_cfg(profile)
    return {
        "profile_id": profile_id,
        "profile_name": profile.get("name"),
        "enabled": bool(profile.get("scheduler_enabled")),
        "scheduler_mode": cfg.get("scheduler_mode"),
        "interval_minutes": int(profile.get("publish_interval_minutes") or 180),
        "buffer_target": int(profile.get("buffer_target") or 2),
        "dry_run": bool(profile.get("scheduler_dry_run", True)),
        "warmup": bool(profile.get("scheduler_warmup", True)),
        "next_publish_at": profile.get("next_publish_at"),
        "last_publish_at": profile.get("last_publish_at"),
        "ready": counts.get("ready", 0),
        "generating": counts.get("generating", 0),
        "publishing": counts.get("publishing", 0),
        "published": counts.get("published", 0),
        "failed": counts.get("failed", 0) + counts.get("publish_failed", 0),
        "buffer_active": active,
        "daily_slots": cfg.get("daily_slots"),
        "daily_random_minutes": cfg.get("daily_random_minutes"),
        "resume_random_minutes": cfg.get("resume_random_minutes"),
        "daily_plan_date": cfg.get("daily_plan_date"),
        "daily_plan": cfg.get("daily_plan") or [],
        "config": cfg,
        "queue": list_content_queue(profile_id, 30),
    }


def _scheduler_factory_request(profile_id: str, cfg: dict[str, Any], videos: int) -> FactoryV2GenerateRequest:
    return FactoryV2GenerateRequest(
        page_profile_id=profile_id,
        videos=videos,
        mode=str(cfg.get("mode") or "AUTO"),
        beat_image_count=int(cfg.get("beat_image_count") or 7),
        beat_duration_sec=float(cfg.get("beat_duration_sec") or 10),
        beat_motion_preset=str(cfg.get("beat_motion_preset") or "capcut_beat"),
        i2v_clip_count=int(cfg.get("i2v_clip_count") or 3),
        i2v_clip_duration=str(cfg.get("i2v_clip_duration") or "4s"),
        image_concurrency=int(cfg.get("image_concurrency") or 9),
        video_concurrency=int(cfg.get("video_concurrency") or 4),
        auto_publish=False,
        facebook_dry_run=True,
    )


async def _scheduler_fill_profile_unlocked(profile_id: str, force_count: int | None = None) -> dict[str, Any]:
    profile = get_page_profile(profile_id)
    if not profile:
        raise ValueError("Không thấy Page Profile")
    cfg = profile.get("scheduler_config") or {}
    target = int(profile.get("buffer_target") or 2)
    with conn() as c:
        active = int(c.execute(
            "SELECT COUNT(*) n FROM content_queue WHERE page_profile_id=? AND status IN ('generating','ready','publishing')",
            (profile_id,),
        ).fetchone()["n"])
    missing = max(0, int(force_count) if force_count is not None else target - active)
    if missing <= 0:
        return {"created": 0, "active_before": active, "target": target, "jobs": []}
    req = _scheduler_factory_request(profile_id, cfg, missing)
    provisional = f"sched_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    created: list[dict[str, Any]] = []
    for i in range(missing):
        mode = choose_factory_mode(profile, req.mode)
        scenes, flow, kind = build_factory_v2_job(profile, req, provisional, i + 1, mode)
        jid = create_flow_job(kind, scenes, flow)
        qid = create_content_queue_item(profile_id, jid)
        created.append({"job_id": jid, "queue_id": qid, "mode": mode})
    run_id = create_factory_run(profile_id, req, [x["job_id"] for x in created])
    with conn() as c:
        c.execute("UPDATE factory_runs SET id=id,config_json=? WHERE id=?", (dumps({**req.model_dump(), "scheduler": True}), run_id))
    persist_event_log({"type": "SCHEDULER_FILL", "profileId": profile_id, "message": f"Tạo bù {missing} video · buffer target={target}"})
    await dispatch_jobs()
    return {"created": len(created), "active_before": active, "target": target, "jobs": created}


async def scheduler_fill_profile(profile_id: str, force_count: int | None = None) -> dict[str, Any]:
    async with SCHEDULER_LOCK:
        return await _scheduler_fill_profile_unlocked(profile_id, force_count)


def _scheduler_set_next(profile_id: str, when: datetime, *, last: datetime | None = None, warmup: bool | None = None) -> None:
    fields: dict[str, Any] = {"next_publish_at": when.astimezone(timezone.utc).isoformat(timespec="seconds"), "updated_at": utcnow()}
    if last is not None:
        fields["last_publish_at"] = last.astimezone(timezone.utc).isoformat(timespec="seconds")
    if warmup is not None:
        fields["scheduler_warmup"] = 1 if warmup else 0
    cols = ",".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE page_profiles SET {cols} WHERE id=?", list(fields.values()) + [profile_id])


def _mark_daily_entry_published(profile_id: str, cfg: dict[str, Any], target_utc: datetime) -> dict[str, Any]:
    plan = cfg.get("daily_plan") if isinstance(cfg.get("daily_plan"), list) else []
    candidates = [e for e in plan if str(e.get("state") or "pending") == "pending"]
    if candidates:
        entry = min(candidates, key=lambda e: abs((_plan_dt_local(e).astimezone(timezone.utc) - target_utc).total_seconds()) if _plan_dt_local(e) else 10**12)
        entry["state"] = "published"
        entry["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cfg["daily_plan"] = plan
        _save_scheduler_cfg(profile_id, cfg)
    return cfg


async def scheduler_publish_due(profile: dict[str, Any]) -> dict[str, Any] | None:
    profile_id = str(profile["id"])
    if not profile.get("scheduler_enabled"):
        return None
    cfg = _scheduler_cfg(profile)
    now = datetime.now(timezone.utc)
    next_at = _parse_iso_utc(profile.get("next_publish_at")) or now
    status = scheduler_status(profile_id)
    target = int(profile.get("buffer_target") or 2)
    if status.get("publishing", 0) > 0:
        return None
    if profile.get("scheduler_warmup") and status["ready"] < target:
        return None
    if now < next_at or status["ready"] <= 0:
        return None
    page_id = str(profile.get("facebook_page_id") or "").strip()
    if not page_id or not get_fb_page_secret(page_id):
        persist_event_log({"type": "SCHEDULER_BLOCKED", "profileId": profile_id, "message": "Chưa map Facebook Page/token"})
        return None
    with conn() as c:
        row = c.execute(
            "SELECT * FROM content_queue WHERE page_profile_id=? AND status='ready' ORDER BY created_at ASC LIMIT 1",
            (profile_id,),
        ).fetchone()
    if not row:
        return None
    q = dict(row)
    req = FacebookPublishRequest(
        page_id=page_id,
        video_path=str(q.get("video_path") or ""),
        title=str(q.get("title") or profile.get("name") or ""),
        description=str(q.get("description") or ""),
        dry_run=bool(profile.get("scheduler_dry_run", True)),
    )
    pub_id = create_publish_job(req, bool(profile.get("scheduler_dry_run", True)))
    with conn() as c:
        c.execute(
            "UPDATE content_queue SET status='publishing',publish_job_id=?,scheduled_for=?,updated_at=? WHERE id=?",
            (pub_id, next_at.isoformat(timespec="seconds"), utcnow(), q["id"]),
        )
    persist_event_log({"type": "SCHEDULED_PUBLISH_START", "profileId": profile_id, "jobId": q.get("flow_job_id"), "message": f"Đến giờ đăng · queue={q['id']} · dry={bool(profile.get('scheduler_dry_run', True))}"})
    await asyncio.to_thread(run_fb_publish, pub_id)
    with conn() as c:
        pub = c.execute("SELECT * FROM publish_jobs WHERE id=?", (pub_id,)).fetchone()
    pub_status = str(pub["status"] if pub else "failed")
    if pub_status in {"submitted", "dry_run_ok"}:
        with conn() as c:
            c.execute("UPDATE content_queue SET status='published',error=NULL,updated_at=? WHERE id=?", (utcnow(), q["id"]))
        if cfg.get("scheduler_mode") == "DAILY_SLOTS":
            cfg = _mark_daily_entry_published(profile_id, cfg, next_at)
            fresh = get_page_profile(profile_id) or profile
            cfg = _scheduler_cfg(fresh)
            cfg, nxt_entry, next_local = _next_daily_entry(profile_id, fresh, cfg, _scheduler_local_now(), startup_reconcile=False)
            _scheduler_set_next(profile_id, next_local, last=now, warmup=False)
            msg = f"Đăng xong · slot kế tiếp {next_local.strftime('%d/%m %H:%M')}"
        else:
            interval = int(profile.get("publish_interval_minutes") or 180)
            next_due = now + timedelta(minutes=interval)
            _scheduler_set_next(profile_id, next_due, last=now, warmup=False)
            msg = f"Đăng xong · bài kế tiếp sau {interval} phút"
        persist_event_log({"type": "SCHEDULED_PUBLISH_DONE", "profileId": profile_id, "message": msg})
        return {"ok": True, "queue_id": q["id"], "publish_job_id": pub_id, "status": pub_status}
    with conn() as c:
        c.execute("UPDATE content_queue SET status='ready',error=?,updated_at=? WHERE id=?", (f"publish {pub_status}", utcnow(), q["id"]))
    retry = now + timedelta(minutes=15)
    _scheduler_set_next(profile_id, retry)
    persist_event_log({"type": "SCHEDULED_PUBLISH_FAILED", "profileId": profile_id, "message": f"Publish lỗi {pub_status} · retry sau 15 phút"})
    return {"ok": False, "status": pub_status}


async def scheduler_tick_profile(profile_id: str) -> None:
    profile = get_page_profile(profile_id)
    if not profile or not profile.get("scheduler_enabled"):
        return
    cfg = _scheduler_cfg(profile)
    if cfg.get("scheduler_mode") == "DAILY_SLOTS":
        cfg, _, target_local = _next_daily_entry(profile_id, profile, cfg, _scheduler_local_now(), startup_reconcile=False)
        current_next = _parse_iso_utc(profile.get("next_publish_at"))
        if not current_next or abs((current_next - target_local.astimezone(timezone.utc)).total_seconds()) > 2:
            _scheduler_set_next(profile_id, target_local)
    await scheduler_fill_profile(profile_id)
    profile = get_page_profile(profile_id) or profile
    result = await scheduler_publish_due(profile)
    if result and result.get("ok"):
        await scheduler_fill_profile(profile_id)


async def reconcile_scheduler_on_startup() -> None:
    for profile in list_page_profiles():
        if not profile.get("scheduler_enabled") or not profile.get("enabled"):
            continue
        profile_id = str(profile["id"])
        try:
            cfg = _scheduler_cfg(profile)
            if cfg.get("scheduler_mode") == "DAILY_SLOTS":
                cfg, _, target = _next_daily_entry(profile_id, profile, cfg, _scheduler_local_now(), startup_reconcile=True)
                _scheduler_set_next(profile_id, target)
            else:
                _reconcile_interval_next(profile_id, profile, cfg, startup=True)
            persist_event_log({"type": "SCHEDULER_RESTORED", "profileId": profile_id, "message": f"Khôi phục scheduler {cfg.get('scheduler_mode')} sau restart"})
        except Exception as exc:
            persist_event_log({"type": "SCHEDULER_RESTORE_ERROR", "profileId": profile_id, "message": str(exc)})


async def recover_stale_parenting_flow_jobs() -> int:
    """Lease watchdog for Parenting jobs.

    DB state can say running/dispatching/downloading after Chrome/service-worker/network
    dies without a clean result. Artifacts/checkpoints are truth; a stale lease is not
    a reason to redo successful scenes.
    """
    now=datetime.now(timezone.utc)
    stale_after=max(180,int(os.getenv("PARENTING_FLOW_STALE_SECONDS","180") or 600))
    with conn() as c:
        rows=c.execute(
            "SELECT id,status,updated_at,agent_id,kind FROM flow_jobs "
            "WHERE kind IN ('parenting_story','parenting_test_scene') "
            "AND status IN ('running','dispatching','interrupted','checkpointing','downloading') "
            "ORDER BY updated_at ASC LIMIT 100"
        ).fetchall()
    recovered=0
    for row in rows:
        jid=str(row['id']); status=str(row['status'] or '')
        # An in-memory download recovery owns this job and should finish normally.
        if status=='downloading' and jid in DOWNLOAD_RECOVERY:
            continue
        owner=None
        aid=str(row['agent_id'] or '')
        if aid:
            owner=AGENTS.get(aid)
        if owner is None:
            owner=next((a for a in AGENTS.values() if jid in a.active_job_ids),None)
        if owner is not None:
            try:
                seen=datetime.fromisoformat(str(owner.last_seen).replace('Z','+00:00')).astimezone(timezone.utc)
                if (now-seen).total_seconds() < 75:
                    continue
            except Exception:
                continue
        try:
            updated=datetime.fromisoformat(str(row['updated_at'] or '').replace('Z','+00:00')).astimezone(timezone.utc)
            age=(now-updated).total_seconds()
        except Exception:
            age=stale_after+1
        # interrupted with no owner can be recovered quickly; active-looking states get grace.
        threshold=45 if status=='interrupted' else stale_after
        if age < threshold:
            continue
        await _reconcile_parenting_flow_job(jid,agent=None,reason=f"lease_watchdog:{status}:{int(age)}s",increment_retry=True)
        persist_event_log({"type":"FLOW_LEASE_RECOVERY","jobId":jid,"status":status,"ageSec":int(age),"message":"Checkpoint resume; giữ artifact đã có"})
        recovered+=1
    return recovered


async def scheduler_loop() -> None:
    await reconcile_scheduler_on_startup()
    while True:
        try:
            for p in list_page_profiles():
                if p.get("scheduler_enabled") and p.get("enabled"):
                    try:
                        await scheduler_tick_profile(str(p["id"]))
                    except Exception as exc:
                        persist_event_log({"type": "SCHEDULER_ERROR", "profileId": p.get("id"), "message": str(exc)})
            if PARENTING_HANDLER is not None:
                try:
                    await PARENTING_HANDLER.campaign_tick()
                except Exception as exc:
                    persist_event_log({"type":"AUTO_FB_CAMPAIGN_TICK_ERROR","message":str(exc)})
            # V4 checkpoint retries can be delayed via next_retry_at. Pump the dispatcher
            # every scheduler tick so a retry does not depend on a new WS event/user click.
            try:
                await recover_stale_parenting_flow_jobs()
            except Exception as exc:
                persist_event_log({"type":"FLOW_LEASE_WATCHDOG_ERROR","message":str(exc)})
            try:
                await dispatch_jobs()
            except Exception as exc:
                persist_event_log({"type":"FLOW_RETRY_DISPATCH_ERROR","message":str(exc)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            persist_event_log({"type": "SCHEDULER_LOOP_ERROR", "message": str(exc)})
        await asyncio.sleep(20)


def get_factory_meta(job: dict[str, Any]) -> dict[str, Any]:
    scenes = job.get("scenes") or []
    if not scenes:
        return {}
    metadata = scenes[0].get("metadata") or {}
    out = dict(metadata.get("factoryV2") or {})
    for key in ["title", "caption", "hashtags", "styleNote", "aiUsed", "aiModel", "titleHint", "personaMasterPath"]:
        if key in metadata:
            out[key] = metadata.get(key)
    return out


def _cached_images_for_job(job_id: str) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM assets WHERE job_id=? AND kind='image' ORDER BY scene_id ASC, created_at ASC", (job_id,)).fetchall()
    by_scene: dict[int, dict[str, Any]] = {}
    for r in rows:
        d = dict(r); sid = int(d.get("scene_id") or 0); lp = d.get("local_path")
        if sid and sid not in by_scene and lp and Path(lp).exists():
            by_scene[sid] = d
    return [by_scene[k] for k in sorted(by_scene)]


def _downloaded_videos_for_job(job_id: str) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM assets WHERE job_id=? AND kind='video' AND scene_id>0 ORDER BY scene_id ASC, created_at ASC", (job_id,)).fetchall()
    by_scene: dict[int, dict[str, Any]] = {}
    for r in rows:
        d = dict(r); sid = int(d.get("scene_id") or 0); lp = d.get("local_path")
        if sid and sid not in by_scene and lp and Path(lp).exists():
            by_scene[sid] = d
    return [by_scene[k] for k in sorted(by_scene)]


def _make_ahash(path: Path) -> str | None:
    try:
        with Image.open(path) as im:
            im = im.convert("L").resize((16, 16))
            px = list(im.getdata())
        avg = sum(px) / len(px)
        bits = ''.join('1' if x >= avg else '0' for x in px)
        return hex(int(bits, 2))[2:].zfill(64)
    except Exception:
        return None


def _hash_distance(a: str, b: str) -> int:
    try:
        return (int(a,16) ^ int(b,16)).bit_count()
    except Exception:
        return 256


def qc_video_sync(job_id: str, final_path: str) -> dict[str, Any]:
    p = Path(final_path)
    info = ffprobe_info(p)
    job = get_flow_job(job_id) or {}
    meta = get_factory_meta(job)
    expected = int(meta.get("expectedCount") or 1)
    mode = str(meta.get("mode") or "")
    source_rows = _cached_images_for_job(job_id) if mode == "IMAGE_BEAT" else _downloaded_videos_for_job(job_id)

    score = 0.0; checks: dict[str, Any] = {}
    good_res = int(info.get("width") or 0) == 1080 and int(info.get("height") or 0) == 1920
    score += 30 if good_res else 10 if int(info.get("height") or 0) > int(info.get("width") or 0) else 0
    checks["resolution"] = {"ok": good_res, "width": info.get("width"), "height": info.get("height")}

    duration = float(info.get("duration") or 0)
    good_duration = 4 <= duration <= 60
    score += 20 if good_duration else 5 if duration > 0 else 0
    checks["duration"] = {"ok": good_duration, "seconds": round(duration,2)}

    asset_ratio = min(1.0, len(source_rows)/max(1, expected))
    score += 20 * asset_ratio
    checks["source_assets"] = {"count": len(source_rows), "expected": expected, "ratio": round(asset_ratio,2)}

    uniqueness = 1.0
    if mode == "IMAGE_BEAT" and len(source_rows) >= 2:
        hashes = [_make_ahash(Path(r["local_path"])) for r in source_rows]
        hashes = [h for h in hashes if h]
        close = 0; pairs = 0
        for i in range(len(hashes)):
            for j in range(i+1, len(hashes)):
                pairs += 1
                if _hash_distance(hashes[i],hashes[j]) < 18:
                    close += 1
        if pairs:
            uniqueness = max(0.0, 1.0 - close/pairs)
    score += 20 * uniqueness
    checks["uniqueness"] = {"score": round(uniqueness,3)}

    bitrate = float(info.get("bit_rate") or 0)
    size = p.stat().st_size if p.exists() else 0
    density = size/max(duration,1)
    media_ok = size > 250_000 and density > 30_000
    score += 10 if media_ok else 3 if size > 0 else 0
    checks["file"] = {"ok": media_ok, "bytes": size, "bytes_per_sec": int(density), "bit_rate": bitrate}

    score = round(min(100.0, score), 1)
    passed = score >= 70 and good_duration and len(source_rows) >= max(2, min(expected, 2))
    result = {"score": score, "passed": passed, "checks": checks, "mode": mode, "preflight": info}
    with conn() as c:
        c.execute("INSERT INTO qc_results(id,job_id,score,passed,details_json,created_at) VALUES(?,?,?,?,?,?)",
                  (f"qc_{uuid.uuid4().hex}", job_id, score, 1 if passed else 0, dumps(result), utcnow()))
    return result


def _concat_video_clips(job_id: str, rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Không thấy ffmpeg trong PATH")
    out_dir = OUTPUT_DIR / "factory_v2" / job_id
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    normalized: list[Path] = []
    for idx,row in enumerate(rows):
        src = Path(row["local_path"])
        dst = work / f"clip_{idx+1:03d}.mp4"
        _run_cmd([
            "ffmpeg","-y","-i",str(src),"-an","-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,eq=contrast=1.02:saturation=1.03,format=yuv420p",
            "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p",str(dst)
        ], timeout=240)
        normalized.append(dst)
    concat_file = work / "concat.txt"
    concat_file.write_text("\n".join(f"file '{x.as_posix()}'" for x in normalized)+"\n",encoding="utf-8")
    raw = work / "video_no_audio.mp4"
    _run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_file),"-c","copy",str(raw)],timeout=240)
    return _attach_music_or_silence(raw, out_dir / "final.mp4", str(meta.get("musicPath") or ""))


def _render_image_beat(job_id: str, rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Không thấy ffmpeg trong PATH")
    total = float(meta.get("beatDurationSec") or 10.0)
    preset = str(meta.get("motionPreset") or "capcut_beat")
    out_dir = OUTPUT_DIR / "factory_v2" / job_id
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    per = max(0.5, total/max(1,len(rows)))
    segs: list[Path] = []
    for idx,row in enumerate(rows):
        src=Path(row["local_path"]); seg=work/f"seg_{idx+1:03d}.mp4"
        style = preset
        if preset == "mix": style = "capcut_beat" if idx%2==0 else "smooth"
        if style == "smooth":
            z="min(1.13,1.0+on*0.0016)"; x="iw/2-(iw/zoom/2)+4*sin(on*0.10)"; y="ih/2-(ih/zoom/2)+3*cos(on*0.08)"
            tail=""
        elif style == "flash_cut":
            z="1.03+0.055*abs(sin(on*0.38))"; x="iw/2-(iw/zoom/2)+14*sin(on*0.50)"; y="ih/2-(ih/zoom/2)+9*cos(on*0.43)"
            st=max(0.05,per-0.09); tail=f",fade=t=out:st={st:.3f}:d=0.09:color=white"
        else:
            z="1.035+0.050*abs(sin(on*0.36))"; x="iw/2-(iw/zoom/2)+13*sin(on*0.49)"; y="ih/2-(ih/zoom/2)+9*cos(on*0.42)"; tail=""
        vf=(f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s=1080x1920:fps=30,"
            f"eq=contrast=1.03:saturation=1.04{tail},format=yuv420p")
        _run_cmd(["ffmpeg","-y","-loop","1","-framerate","30","-i",str(src),"-t",f"{per:.3f}","-vf",vf,"-an","-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-r","30",str(seg)],timeout=240)
        segs.append(seg)
    concat_file=work/"concat.txt"; concat_file.write_text("\n".join(f"file '{x.as_posix()}'" for x in segs)+"\n",encoding="utf-8")
    raw=work/"video_no_audio.mp4"
    _run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_file),"-c","copy",str(raw)],timeout=240)
    return _attach_music_or_silence(raw,out_dir/"final.mp4",str(meta.get("musicPath") or ""),target_duration=total)


def _attach_music_or_silence(raw: Path, final: Path, music_path: str, target_duration: float | None = None) -> str:
    final.parent.mkdir(parents=True,exist_ok=True)
    if target_duration is None:
        try: target_duration=float(ffprobe_info(raw).get("duration") or 0)
        except Exception: target_duration=0
    if music_path and Path(music_path).exists():
        cmd=["ffmpeg","-y","-i",str(raw),"-stream_loop","-1","-i",music_path]
        if target_duration: cmd += ["-t",f"{target_duration:.3f}"]
        cmd += ["-filter_complex","[1:a]volume=0.82,afade=t=in:st=0:d=0.15[a]","-map","0:v:0","-map","[a]","-c:v","copy","-c:a","aac","-b:a","192k","-movflags","+faststart","-shortest",str(final)]
        _run_cmd(cmd,timeout=240)
    else:
        cmd=["ffmpeg","-y","-i",str(raw),"-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=44100"]
        if target_duration: cmd += ["-t",f"{target_duration:.3f}"]
        cmd += ["-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","128k","-movflags","+faststart","-shortest",str(final)]
        _run_cmd(cmd,timeout=240)
    return str(final.resolve())


async def maybe_auto_publish_factory(job_id: str, final_path: str, qc: dict[str, Any]) -> None:
    if not qc.get("passed"):
        return
    job=get_flow_job(job_id) or {}; meta=get_factory_meta(job)
    if not meta.get("autoPublish"):
        return
    page_id=str(meta.get("facebookPageId") or "").strip()
    if not page_id or not get_fb_page_secret(page_id):
        await ui_broadcast({"type":"AUTO_PUBLISH_SKIPPED","jobId":job_id,"error":"Page Profile chưa map Facebook Page/token"})
        return
    title = str(meta.get("title") or meta.get("profileName") or "")
    caption = str(meta.get("caption") or "")
    tags = meta.get("hashtags") or []
    if tags and not any(str(t) in caption for t in tags):
        caption = (caption + "\n\n" + " ".join(str(t) for t in tags)).strip()
    req=FacebookPublishRequest(page_id=page_id,video_path=final_path,title=title,description=caption,dry_run=bool(meta.get("facebookDryRun",True)))
    pub_id=create_publish_job(req,bool(meta.get("facebookDryRun",True)))
    await ui_broadcast({"type":"AUTO_PUBLISH_QUEUED","jobId":job_id,"publishJobId":pub_id,"dryRun":bool(meta.get("facebookDryRun",True))})
    await asyncio.to_thread(run_fb_publish,pub_id)


async def render_factory_v2(job_id: str) -> None:
    try:
        update_flow_job(job_id,status="rendering",error=None)
        await ui_broadcast({"type":"FACTORY_RENDER_STARTED","jobId":job_id})
        job=get_flow_job(job_id) or {}; meta=get_factory_meta(job); mode=str(meta.get("mode") or "IMAGE_BEAT")
        if mode=="IMAGE_TO_VIDEO":
            rows=_downloaded_videos_for_job(job_id)
            if len(rows)<2: raise RuntimeError(f"Chỉ nhận {len(rows)} video local từ extension; cần ít nhất 2 clip")
            final=await asyncio.to_thread(_concat_video_clips,job_id,rows,meta)
        else:
            rows=_cached_images_for_job(job_id)
            if len(rows)<2: raise RuntimeError(f"Chỉ cache được {len(rows)} ảnh; cần ít nhất 2")
            final=await asyncio.to_thread(_render_image_beat,job_id,rows,meta)
        asset_id=add_asset(job_id,0,"final_video",local_path=final,title=f"{meta.get('profileName','Factory')} · {mode}",metadata={"source":"FACTORY_V2","mode":mode})
        update_flow_job(job_id,status="qc",error=None)
        qc=await asyncio.to_thread(qc_video_sync,job_id,final)
        update_flow_job(job_id,status="done" if qc.get("passed") else "qc_failed",error=None if qc.get("passed") else f"QC score {qc.get('score')}")
        await ui_broadcast({"type":"FACTORY_VIDEO_READY","jobId":job_id,"assetId":asset_id,"localPath":final,"qc":qc})
        queue_item = get_content_queue_by_flow(job_id)
        if queue_item:
            if qc.get("passed"):
                job_meta = get_factory_meta(job)
                caption = str(job_meta.get("caption") or "")
                tags = job_meta.get("hashtags") or []
                if tags and not any(str(t) in caption for t in tags):
                    caption = (caption + "\n\n" + " ".join(str(t) for t in tags)).strip()
                update_content_queue_by_flow(
                    job_id,
                    status="ready",
                    video_path=final,
                    title=str(job_meta.get("title") or job_meta.get("profileName") or ""),
                    description=caption,
                    error=None,
                )
                await ui_broadcast({"type":"SCHEDULER_VIDEO_READY","jobId":job_id,"profileId":queue_item.get("page_profile_id"),"localPath":final})
            else:
                update_content_queue_by_flow(job_id,status="failed",error=f"QC score {qc.get('score')}")
        else:
            await maybe_auto_publish_factory(job_id,final,qc)
    except Exception as exc:
        update_flow_job(job_id,status="failed",error=f"Factory render lỗi: {exc}")
        if get_content_queue_by_flow(job_id):
            update_content_queue_by_flow(job_id,status="failed",error=f"Factory render lỗi: {exc}")
        await ui_broadcast({"type":"FACTORY_RENDER_FAILED","jobId":job_id,"error":str(exc)})


def latest_qc(job_id: str) -> dict[str, Any] | None:
    with conn() as c:
        row=c.execute("SELECT * FROM qc_results WHERE job_id=? ORDER BY created_at DESC LIMIT 1",(job_id,)).fetchone()
    if not row: return None
    d=dict(row); d["passed"]=bool(d["passed"]); d["details"]=loads(d.pop("details_json"),{}); return d


def normalize_input_images(person_path: str | None, outfit_path: str | None) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for path, role in [(person_path, "person"), (outfit_path, "outfit")]:
        path = (path or "").strip()
        if path:
            p = Path(path)
            inputs.append({"path": str(p), "name": p.stem or role, "role": role})
    return inputs


def normalize_persona_pack(profile: dict[str, Any], outfit_path: str | None) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    refs = [
        (profile.get("persona_master_path"), "person_front"),
        (profile.get("persona_bust_path"), "person_bust"),
        (profile.get("persona_left_master_path") if profile.get("persona_left_enabled", True) else None, "person_left"),
        (profile.get("persona_right_master_path") if profile.get("persona_right_enabled", True) else None, "person_right"),
        (profile.get("persona_back_master_path") if profile.get("persona_back_enabled", True) else None, "person_back"),
        (outfit_path, "outfit"),
    ]
    seen=set()
    for path, role in refs:
        path=(path or '').strip()
        if not path or path in seen:
            continue
        seen.add(path)
        p=Path(path)
        if p.exists():
            inputs.append({"path": str(p), "name": p.stem or role, "role": role})
    return inputs


def build_factory_scenes(req: FactoryBatchRequest) -> list[dict[str, Any]]:
    # Adult-only prompts by design. The page/persona can be replaced later by a dedicated profile system.
    poses = [
        "standing naturally and looking toward camera",
        "casual mirror selfie pose",
        "walking toward camera with relaxed posture",
        "holding a water bottle after workout",
        "adjusting hair naturally",
        "side pose beside gym equipment",
        "sitting on a gym bench between sets",
        "light stretching pose",
        "checking smartwatch after workout",
        "walking through a bright modern lobby",
    ]
    scenes_bg = [
        "premium modern gym",
        "clean bright fitness studio",
        "upscale hotel gym",
        "minimal mirror workout studio",
        "bright lifestyle cafe",
        "modern shopping mall corridor",
        "sunlit apartment interior",
        "clean urban rooftop",
    ]
    outfit_texts = [
        "black athletic top and light fitness shorts",
        "white fitted sports top and neutral shorts",
        "pink coordinated adult fitness set",
        "grey athletic crop top and matching shorts",
        "burgundy fitness top and olive shorts",
        "beige athleisure set",
    ]
    refs = normalize_input_images(req.persona_path, req.outfit_path)
    scenes: list[dict[str, Any]] = []
    for i in range(req.count):
        prompt = (
            f"{req.base_prompt}. Adult model, age 21+. Theme: {req.theme}. "
            f"Scene: {random.choice(scenes_bg)}. Pose: {random.choice(poses)}. "
            f"Wardrobe: {random.choice(outfit_texts)}. "
            "Natural body proportions, realistic skin texture, clean background separation, no text, no watermark."
        )
        scenes.append(
            {
                "sceneId": i + 1,
                "imagePrompt": prompt,
                "videoPrompt": "",
                "inputImages": refs,
                "metadata": {
                    "pageProfile": req.page_profile,
                    "theme": req.theme,
                    "variation": i + 1,
                    "adultOnly": True,
                },
            }
        )
    return scenes


def build_video_test_scenes(req: VideoTestRequest) -> list[dict[str, Any]]:
    refs = normalize_input_images(req.person_path, req.outfit_path)
    poses = [
        "standing naturally and looking at camera",
        "casual mirror selfie pose",
        "walking slowly toward camera",
        "adjusting hair naturally",
        "holding a water bottle after workout",
        "relaxed side pose",
        "sitting briefly on a gym bench",
        "checking a smartwatch",
        "light stretching pose",
        "turning slightly toward camera",
    ]
    backgrounds = [
        "premium modern gym",
        "bright clean fitness studio",
        "upscale hotel gym",
        "minimal mirror workout studio",
        "modern lifestyle cafe",
        "sunlit apartment interior",
        "clean urban rooftop",
        "modern shopping mall corridor",
    ]
    outfits = [
        "black athletic top and light fitness shorts",
        "white fitted sports top and neutral shorts",
        "pink coordinated adult fitness set",
        "grey athletic crop top and matching shorts",
        "beige athleisure set",
        "burgundy fitness top and olive shorts",
    ]
    motion = req.motion_preset if req.motion_preset in {"capcut_beat", "smooth", "mix"} else "capcut_beat"
    settings = {
        "durationSec": float(req.duration_sec),
        "motionPreset": motion,
        "musicPath": (req.music_path or "").strip() or None,
        "width": 1080,
        "height": 1920,
        "fps": 30,
    }
    scenes: list[dict[str, Any]] = []
    for i in range(req.image_count):
        outfit_clause = "Keep the outfit/reference garment faithful to the uploaded outfit image." if req.outfit_path else f"Wardrobe: {outfits[i % len(outfits)]}."
        identity_clause = "Keep exactly the same adult woman's identity, face, hair and body proportions as the person reference." if req.person_path else "Use one consistent adult woman identity across this batch."
        prompt = (
            f"{req.prompt}. {identity_clause} Adult model, age 21+. "
            f"Scene variation {i+1}: {backgrounds[i % len(backgrounds)]}; pose: {poses[i % len(poses)]}. "
            f"{outfit_clause} Photorealistic, realistic skin texture, natural smartphone photography, "
            "vertical 9:16 composition, full body or three-quarter body, tasteful social media content, no text, no watermark."
        )
        scenes.append({
            "sceneId": i + 1,
            "imagePrompt": prompt,
            "videoPrompt": "",
            "inputImages": refs,
            "metadata": {
                "mode": "video_test",
                "variation": i + 1,
                "adultOnly": True,
                "videoTest": settings,
            },
        })
    return scenes


def _run_cmd(cmd: list[str], timeout: int = 180) -> None:
    cp = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "")[-5000:]
        raise RuntimeError(f"Command lỗi ({cp.returncode}): {' '.join(cmd[:8])} ...\n{tail}")


def _video_test_image_rows(job_id: str) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM assets WHERE job_id=? AND kind='image' ORDER BY scene_id ASC, created_at ASC",
            (job_id,),
        ).fetchall()
    # Keep the first cached image per scene; video test always asks Flow for x1 output.
    by_scene: dict[int, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        sid = int(d.get("scene_id") or 0)
        if sid not in by_scene and d.get("local_path") and Path(d["local_path"]).exists():
            by_scene[sid] = d
    return [by_scene[k] for k in sorted(by_scene)]


def render_video_test_sync(job_id: str) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Không thấy ffmpeg trong PATH")
    job = get_flow_job(job_id)
    if not job:
        raise RuntimeError("Không thấy video test job")
    scenes = job.get("scenes") or []
    meta = ((scenes[0].get("metadata") or {}).get("videoTest") or {}) if scenes else {}
    total = float(meta.get("durationSec") or 10.0)
    preset = str(meta.get("motionPreset") or "capcut_beat")
    width, height, fps = int(meta.get("width") or 1080), int(meta.get("height") or 1920), int(meta.get("fps") or 30)
    music_path = str(meta.get("musicPath") or "").strip()

    images = _video_test_image_rows(job_id)
    if len(images) < 2:
        raise RuntimeError(f"Chỉ cache được {len(images)} ảnh; cần ít nhất 2 ảnh để render video")

    out_dir = OUTPUT_DIR / "video_tests" / job_id
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    per = max(0.45, total / len(images))
    segments: list[Path] = []

    for idx, row in enumerate(images):
        src = Path(row["local_path"])
        seg = work / f"seg_{idx+1:03d}.mp4"
        style = preset
        if preset == "mix":
            style = "capcut_beat" if idx < max(2, len(images)//2) or idx % 2 == 0 else "smooth"
        if style == "smooth":
            z = "min(1.12,1.0+on*0.0017)"
            x = "iw/2-(iw/zoom/2)+4*sin(on*0.10)"
            y = "ih/2-(ih/zoom/2)+3*cos(on*0.08)"
        else:
            # CapCut-like pulse: short, visible zoom/shake instead of a static slideshow.
            z = "1.035+0.045*abs(sin(on*0.34))"
            x = "iw/2-(iw/zoom/2)+12*sin(on*0.47)"
            y = "ih/2-(ih/zoom/2)+8*cos(on*0.41)"
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps},"
            "eq=contrast=1.025:saturation=1.035,format=yuv420p"
        )
        _run_cmd([
            "ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(src),
            "-t", f"{per:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps), str(seg)
        ], timeout=180)
        segments.append(seg)

    concat_file = work / "concat.txt"
    concat_file.write_text("\n".join(f"file '{p.as_posix().replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for p in segments) + "\n", encoding="utf-8")
    raw_video = work / "video_no_audio.mp4"
    _run_cmd(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(raw_video)], timeout=180)

    final = out_dir / "final_test_video.mp4"
    if music_path and Path(music_path).exists():
        fade_out = max(0.0, total - 0.35)
        af = f"volume=0.82,afade=t=in:st=0:d=0.15,afade=t=out:st={fade_out:.3f}:d=0.35"
        _run_cmd([
            "ffmpeg", "-y", "-i", str(raw_video), "-stream_loop", "-1", "-i", music_path,
            "-t", f"{total:.3f}", "-filter_complex", f"[1:a]{af}[a]",
            "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest", str(final)
        ], timeout=180)
    else:
        _run_cmd([
            "ffmpeg", "-y", "-i", str(raw_video), "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", f"{total:.3f}", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", "-shortest", str(final)
        ], timeout=180)
    return str(final.resolve())


async def render_video_test(job_id: str) -> None:
    try:
        update_flow_job(job_id, status="rendering", error=None)
        await ui_broadcast({"type": "VIDEO_RENDER_STARTED", "jobId": job_id})
        final_path = await asyncio.to_thread(render_video_test_sync, job_id)
        info = ffprobe_info(Path(final_path))
        asset_id = add_asset(
            job_id, 0, "video", local_path=final_path, title="Full video test",
            metadata={"source": "LOCAL_FFMPEG", "testVideo": True, "preflight": info},
        )
        update_flow_job(job_id, status="done", error=None)
        await ui_broadcast({"type": "VIDEO_READY", "jobId": job_id, "assetId": asset_id, "localPath": final_path})
    except Exception as exc:
        update_flow_job(job_id, status="failed", error=f"Render video lỗi: {exc}")
        await ui_broadcast({"type": "VIDEO_RENDER_FAILED", "jobId": job_id, "error": str(exc)})


async def dispatch_jobs() -> None:
    """Push every queued job into the extension-side queue immediately.

    The extension is now the only concurrency owner. It keeps the global Flow slots
    (IMAGE<=9, VIDEO<=4) and starts work as soon as a slot is available. The server
    therefore must not hold jobs merely because the extension is already processing
    another job.
    """
    if SERVER_SHUTTING_DOWN:
        return
    async with DISPATCH_LOCK:
        if SERVER_SHUTTING_DOWN:
            return
        with conn() as c:
            queued = c.execute(
                "SELECT id,kind,flow_json,scenes_json,retry_count,max_retries,next_retry_at,dispatch_epoch "
                "FROM flow_jobs WHERE status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?) "
                "ORDER BY created_at ASC LIMIT 500",
                (utcnow(),),
            ).fetchall()
        if not queued:
            return
        agents = sorted(AGENTS.values(), key=agent_priority, reverse=True)
        if not agents:
            return
        for row in queued:
            kind = str(row["kind"] or "")
            candidates = [a for a in agents if agent_supports_job(a, kind)]
            if not candidates:
                continue
            agent = candidates[0]
            job_id = str(row["id"])
            original_scenes = loads(row["scenes_json"], [])
            dispatch_scenes = original_scenes
            if kind in {"parenting_story","parenting_test_scene"}:
                plan = _parenting_resume_plan(job_id)
                if plan["all_complete"]:
                    update_flow_job(job_id,status="flow_done",error=None,last_stage="flow_complete",next_retry_at=None)
                    if PARENTING_HANDLER is not None:
                        spawn(PARENTING_HANDLER.on_flow_complete(job_id,True))
                    continue
                if plan["download_missing"]:
                    started = await _request_video_download_recovery(
                        job_id, agent, plan["download_missing"], post_action="resume"
                    )
                    if started:
                        continue
                dispatch_scenes = plan["unresolved"]
                if not dispatch_scenes:
                    continue
            epoch=int(row["dispatch_epoch"] or 0)+1
            payload = {
                "type": "RUN_FLOW_JOB",
                "jobId": job_id,
                "kind": kind,
                "flow": loads(row["flow_json"], {}),
                "scenes": dispatch_scenes,
                "dispatchEpoch": epoch,
                "checkpointResume": kind in {"parenting_story","parenting_test_scene"} and len(dispatch_scenes)<len(original_scenes),
            }
            try:
                # Reserve server-side immediately so repeated dispatch calls cannot resend
                # the same job while it is waiting inside the extension queue.
                agent.active_job_ids.add(job_id)
                agent.busy = True
                agent.job_id = job_id
                update_flow_job(job_id, status="dispatching", agent_id=agent.id, error=None,dispatch_epoch=epoch,last_stage="dispatch")
                await agent.ws.send_text(dumps(payload))
                await ui_broadcast({
                    "type": "JOB_DISPATCHED", "jobId": job_id, "agentId": agent.id,
                    "agentVersion": agent.version, "agentExtensionId": agent.extension_id,
                    "extensionQueue": True, "queueDepth": len(agent.active_job_ids),
                })
            except Exception as exc:
                agent.active_job_ids.discard(job_id)
                agent.busy = bool(agent.active_job_ids)
                agent.job_id = next(iter(agent.active_job_ids), None)
                update_flow_job(job_id, status="queued", agent_id=None, error=f"Dispatch lỗi: {exc}")


def cache_image_sync(url: str, job_id: str, scene_id: int, media_id: str | None) -> str | None:
    if not AUTO_CACHE_FLOW_IMAGES or not url:
        return None
    try:
        folder = OUTPUT_DIR / "flow_images" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"scene_{scene_id:03d}_{(media_id or uuid.uuid4().hex[:8])[:16]}"
        tmp = folder / f"{stem}.tmp"
        with requests.get(url, stream=True, timeout=(15, FLOW_IMAGE_CACHE_TIMEOUT)) as r:
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
            final = folder / f"{stem}{ext}"
            with tmp.open("wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.replace(final)
        return str(final)
    except Exception:
        return None


def add_asset(
    job_id: str | None,
    scene_id: int | None,
    kind: str,
    url: str | None = None,
    local_path: str | None = None,
    media_id: str | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Idempotent asset upsert.

    Flow/extension may report the same media through IMAGE_READY, FILE_READY and
    FLOW_JOB_RESULT. V4.0 merges those signals instead of inserting duplicates.
    """
    job_id = str(job_id or "") or None
    scene_id = int(scene_id or 0) or None
    media_id = str(media_id or "").strip() or None
    local_path = str(local_path or "").strip() or None
    url = str(url or "").strip() or None
    with conn() as c:
        row = None
        if job_id and scene_id and media_id:
            row = c.execute(
                "SELECT id,local_path,url,title,metadata_json FROM assets "
                "WHERE job_id=? AND scene_id=? AND kind=? AND media_id=? ORDER BY created_at DESC LIMIT 1",
                (job_id, scene_id, kind, media_id),
            ).fetchone()
        if row is None and job_id and scene_id and local_path:
            row = c.execute(
                "SELECT id,local_path,url,title,metadata_json FROM assets "
                "WHERE job_id=? AND scene_id=? AND kind=? AND local_path=? ORDER BY created_at DESC LIMIT 1",
                (job_id, scene_id, kind, local_path),
            ).fetchone()
        if row is not None:
            asset_id = str(row["id"])
            merged = loads(row["metadata_json"], {}) or {}
            if isinstance(metadata, dict):
                merged.update(metadata)
            c.execute(
                "UPDATE assets SET url=COALESCE(?,url),local_path=COALESCE(?,local_path),"
                "media_id=COALESCE(?,media_id),title=COALESCE(?,title),metadata_json=? WHERE id=?",
                (url, local_path, media_id, title, dumps(merged), asset_id),
            )
            return asset_id
        asset_id = f"asset_{uuid.uuid4().hex}"
        c.execute(
            "INSERT INTO assets(id,job_id,scene_id,kind,url,local_path,media_id,title,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (asset_id, job_id, scene_id, kind, url, local_path, media_id, title, dumps(metadata or {}), utcnow()),
        )
    return asset_id



def _expected_scene_video_count(scene: dict[str, Any]) -> int:
    meta = scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {}
    try:
        return max(1, min(4, int(meta.get("videoChainFactor") or 1)))
    except Exception:
        return 1


def _checkpoint_rows(job_id: str) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM flow_scene_checkpoints WHERE job_id=? ORDER BY scene_id ASC",
            (job_id,),
        ).fetchall()
    out=[]
    for row in rows:
        d=dict(row)
        d["video_media_ids"]=loads(d.pop("video_media_ids_json", "[]"), []) or []
        d["video_local_paths"]=loads(d.pop("video_local_paths_json", "[]"), []) or []
        d["video_download_urls"]=loads(d.pop("video_download_urls_json", "{}"), {}) or {}
        d["video_download_meta"]=loads(d.pop("video_download_meta_json", "{}"), {}) or {}
        d["invalid_video_media_ids"]=loads(d.pop("invalid_video_media_ids_json", "[]"), []) or []
        d["complete"]=bool(d.get("video_local_paths")) and str(d.get("video_status") or "") == "ready"
        d["needs_download"]=bool(d.get("video_media_ids")) and not bool(d.get("video_local_paths"))
        d["can_resume_image"]=bool(d.get("image_media_id") or d.get("image_local_path"))
        out.append(d)
    return out

def _checkpoint_row(job_id: str, scene_id: int) -> dict[str, Any]:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM flow_scene_checkpoints WHERE job_id=? AND scene_id=?",
            (job_id, int(scene_id)),
        ).fetchone()
    if not row:
        return {
            "job_id": job_id, "scene_id": int(scene_id),
            "image_status": "pending", "image_media_id": None, "image_local_path": None,
            "video_status": "pending", "video_media_ids": [], "video_local_paths": [],
            "video_download_urls": {}, "video_download_meta": {},
            "invalid_video_media_ids": [], "video_regen_count": 0,
            "image_attempts": 0, "video_attempts": 0, "last_error": None,
        }
    d = dict(row)
    d["video_media_ids"] = loads(d.pop("video_media_ids_json"), []) or []
    d["video_local_paths"] = loads(d.pop("video_local_paths_json"), []) or []
    d["video_download_urls"] = loads(d.pop("video_download_urls_json", "{}"), {}) or {}
    d["video_download_meta"] = loads(d.pop("video_download_meta_json", "{}"), {}) or {}
    d["invalid_video_media_ids"] = loads(d.pop("invalid_video_media_ids_json", "[]"), []) or []
    d["video_regen_count"] = int(d.get("video_regen_count") or 0)
    return d


def _checkpoint_update(
    job_id: str,
    scene_id: int,
    *,
    image_status: str | None = None,
    image_media_id: str | None = None,
    image_local_path: str | None = None,
    video_status: str | None = None,
    video_media_ids: list[str] | None = None,
    video_local_paths: list[str] | None = None,
    replace_video_local_paths: list[str] | None = None,
    video_download_urls: dict[str, str] | None = None,
    video_download_meta: dict[str, Any] | None = None,
    invalidate_video_media_ids: list[str] | None = None,
    replace_video_media_ids: list[str] | None = None,
    video_regen_inc: int = 0,
    image_attempt_inc: int = 0,
    video_attempt_inc: int = 0,
    last_error: str | None = None,
) -> None:
    scene_id = int(scene_id)
    current = _checkpoint_row(job_id, scene_id)

    invalid = []
    for mid in list(current.get("invalid_video_media_ids") or []) + list(invalidate_video_media_ids or []):
        mid = str(mid or "").strip()
        if mid and mid not in invalid:
            invalid.append(mid)
    invalid_set = set(invalid)

    if replace_video_media_ids is not None:
        mids: list[str] = []
        source_ids = replace_video_media_ids
    else:
        mids = [str(x).strip() for x in (current.get("video_media_ids") or []) if str(x or "").strip()]
        source_ids = video_media_ids or []
    for mid in source_ids:
        mid = str(mid or "").strip()
        if mid and mid not in invalid_set and mid not in mids:
            mids.append(mid)
    mids = [mid for mid in mids if mid not in invalid_set]

    if replace_video_local_paths is not None:
        paths: list[str] = []
        source_paths = replace_video_local_paths
    else:
        paths = list(current.get("video_local_paths") or [])
        source_paths = video_local_paths or []
    for lp in source_paths:
        lp = str(lp or "").strip()
        if lp and lp not in paths:
            paths.append(lp)

    download_urls = dict(current.get("video_download_urls") or {})
    for dead in invalid_set:
        download_urls.pop(dead, None)
    for mid, url in (video_download_urls or {}).items():
        mid = str(mid or "").strip(); url = str(url or "").strip()
        if mid and url and mid not in invalid_set:
            download_urls[mid] = url

    download_meta = dict(current.get("video_download_meta") or {})
    if isinstance(video_download_meta, dict):
        for mid, meta in video_download_meta.items():
            mid = str(mid or "").strip()
            if mid:
                prev = download_meta.get(mid) if isinstance(download_meta.get(mid), dict) else {}
                merged = dict(prev)
                if isinstance(meta, dict):
                    merged.update(meta)
                else:
                    merged["value"] = meta
                download_meta[mid] = merged
    for dead in invalid_set:
        prev = download_meta.get(dead) if isinstance(download_meta.get(dead), dict) else {}
        download_meta[dead] = {**prev, "invalid": True}

    effective_image_mid = image_media_id or current.get("image_media_id")
    effective_image_path = image_local_path or current.get("image_local_path")
    next_image_status = image_status or current.get("image_status") or "pending"
    if effective_image_mid and str(next_image_status).lower() in {"error","failed","pending"}:
        next_image_status = "ready"
    next_video_status = video_status or current.get("video_status") or "pending"
    if mids and str(next_video_status).lower() in {"error","failed","pending"}:
        next_video_status = "generated"
    if paths:
        next_video_status = "ready"

    values = {
        "image_status": next_image_status,
        "image_media_id": effective_image_mid,
        "image_local_path": effective_image_path,
        "video_status": next_video_status,
        "video_media_ids_json": dumps(mids),
        "video_local_paths_json": dumps(paths),
        "video_download_urls_json": dumps(download_urls),
        "video_download_meta_json": dumps(download_meta),
        "invalid_video_media_ids_json": dumps(invalid),
        "video_regen_count": int(current.get("video_regen_count") or 0) + int(video_regen_inc or 0),
        "image_attempts": int(current.get("image_attempts") or 0) + int(image_attempt_inc or 0),
        "video_attempts": int(current.get("video_attempts") or 0) + int(video_attempt_inc or 0),
        "last_error": last_error if last_error is not None else current.get("last_error"),
    }
    with conn() as c:
        c.execute(
            """
            INSERT INTO flow_scene_checkpoints(
                job_id,scene_id,image_status,image_media_id,image_local_path,
                video_status,video_media_ids_json,video_local_paths_json,video_download_urls_json,video_download_meta_json,
                invalid_video_media_ids_json,video_regen_count,image_attempts,video_attempts,last_error,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id,scene_id) DO UPDATE SET
                image_status=excluded.image_status,
                image_media_id=COALESCE(excluded.image_media_id,flow_scene_checkpoints.image_media_id),
                image_local_path=COALESCE(excluded.image_local_path,flow_scene_checkpoints.image_local_path),
                video_status=excluded.video_status,
                video_media_ids_json=excluded.video_media_ids_json,
                video_local_paths_json=excluded.video_local_paths_json,
                video_download_urls_json=excluded.video_download_urls_json,
                video_download_meta_json=excluded.video_download_meta_json,
                invalid_video_media_ids_json=excluded.invalid_video_media_ids_json,
                video_regen_count=excluded.video_regen_count,
                image_attempts=excluded.image_attempts,
                video_attempts=excluded.video_attempts,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            """,
            (
                job_id, scene_id, values["image_status"], values["image_media_id"], values["image_local_path"],
                values["video_status"], values["video_media_ids_json"], values["video_local_paths_json"],
                values["video_download_urls_json"], values["video_download_meta_json"],
                values["invalid_video_media_ids_json"], values["video_regen_count"],
                values["image_attempts"], values["video_attempts"], values["last_error"], utcnow(),
            ),
        )


class InvalidVideoFile(RuntimeError):
    pass


def _strict_video_probe(path: str | Path) -> dict[str, Any]:
    """Fail closed: existence is not enough. ffprobe must see a real video stream."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise InvalidVideoFile(f"file không tồn tại: {p}")
    st = p.stat()
    if st.st_size < FLOW_VIDEO_MIN_VALID_BYTES:
        raise InvalidVideoFile(f"file video quá nhỏ: {st.st_size} bytes")
    key = str(p.resolve())
    cached = VIDEO_PROBE_CACHE.get(key)
    if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
        if cached[2]:
            try:
                return json.loads(cached[3])
            except Exception:
                return {"duration": FLOW_VIDEO_MIN_VALID_DURATION, "has_audio": True}
        raise InvalidVideoFile(cached[3] or "ffprobe validation failed")
    try:
        cp = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,duration",
            "-of", "json", str(p),
        ], capture_output=True, text=True, timeout=30)
    except Exception as exc:
        err=f"ffprobe chạy lỗi: {exc}"
        VIDEO_PROBE_CACHE[key]=(st.st_size,st.st_mtime_ns,False,err)
        raise InvalidVideoFile(err)
    if cp.returncode != 0:
        err=(cp.stderr or cp.stdout or "ffprobe không đọc được MP4").strip()[-1500:]
        VIDEO_PROBE_CACHE[key]=(st.st_size,st.st_mtime_ns,False,err)
        raise InvalidVideoFile(err)
    try:
        data=json.loads(cp.stdout or "{}")
    except Exception as exc:
        err=f"ffprobe JSON lỗi: {exc}"
        VIDEO_PROBE_CACHE[key]=(st.st_size,st.st_mtime_ns,False,err)
        raise InvalidVideoFile(err)
    streams=data.get("streams") if isinstance(data,dict) else []
    video=[x for x in (streams or []) if isinstance(x,dict) and x.get("codec_type")=="video"]
    if not video:
        err="file không có video stream"
        VIDEO_PROBE_CACHE[key]=(st.st_size,st.st_mtime_ns,False,err)
        raise InvalidVideoFile(err)
    v=video[0]
    try:
        width=int(v.get("width") or 0); height=int(v.get("height") or 0)
    except Exception:
        width=height=0
    if width <= 0 or height <= 0:
        err=f"video stream sai kích thước: {width}x{height}"
        VIDEO_PROBE_CACHE[key]=(st.st_size,st.st_mtime_ns,False,err)
        raise InvalidVideoFile(err)
    vals=[]
    for raw in [v.get("duration"), ((data.get("format") or {}).get("duration") if isinstance(data,dict) else None)]:
        try:
            if raw not in (None,""): vals.append(float(raw))
        except Exception:
            pass
    duration=max(vals or [0.0])
    if duration < FLOW_VIDEO_MIN_VALID_DURATION:
        err=f"video duration không hợp lệ: {duration:.3f}s"
        VIDEO_PROBE_CACHE[key]=(st.st_size,st.st_mtime_ns,False,err)
        raise InvalidVideoFile(err)
    out={"duration":duration,"width":width,"height":height,
         "has_audio":any(isinstance(x,dict) and x.get("codec_type")=="audio" for x in (streams or [])),
         "size":st.st_size}
    packed=json.dumps(out,ensure_ascii=False)
    VIDEO_PROBE_CACHE[key]=(st.st_size,st.st_mtime_ns,True,packed)
    return out


def _video_file_is_valid(path: str | Path) -> bool:
    try:
        _strict_video_probe(path)
        return True
    except Exception:
        return False


def _sync_checkpoint_from_assets(job_id: str, scene_id: int) -> dict[str, Any]:
    with conn() as c:
        rows = c.execute(
            "SELECT kind,media_id,local_path,created_at FROM assets WHERE job_id=? AND scene_id=? ORDER BY created_at ASC",
            (job_id, int(scene_id)),
        ).fetchall()
    image_mid = None
    image_path = None
    video_mids: list[str] = []
    video_paths: list[str] = []
    for row in rows:
        kind = str(row["kind"] or "")
        mid = str(row["media_id"] or "").strip()
        lp = str(row["local_path"] or "").strip()
        if kind == "image":
            if mid:
                image_mid = mid
            if lp and Path(lp).exists():
                image_path = lp
        elif kind == "video":
            if mid and mid not in video_mids:
                video_mids.append(mid)
            if lp and Path(lp).exists() and _video_file_is_valid(lp) and lp not in video_paths:
                video_paths.append(lp)
    cp = _checkpoint_row(job_id, scene_id)
    if image_mid or image_path or video_mids or video_paths:
        _checkpoint_update(
            job_id, scene_id,
            image_status="ready" if (image_mid or image_path) else cp.get("image_status"),
            image_media_id=image_mid,
            image_local_path=image_path,
            video_status="ready" if video_paths else cp.get("video_status"),
            video_media_ids=video_mids,
            replace_video_local_paths=video_paths,
        )
    return _checkpoint_row(job_id, scene_id)


def _merge_result_payload(job_id: str, new_results: list[dict[str, Any]], reported_ok: bool, failures: list[Any] | None = None) -> dict[str, Any]:
    job = get_flow_job(job_id) or {}
    old = job.get("result") if isinstance(job.get("result"), dict) else loads(job.get("result_json"), {})
    if not isinstance(old, dict):
        old = {}
    old_results = old.get("results") if isinstance(old.get("results"), list) else []
    by_scene: dict[int, dict[str, Any]] = {}
    for row in old_results + list(new_results or []):
        if not isinstance(row, dict):
            continue
        sid = int(row.get("sceneId") or row.get("scene_id") or (int(row.get("index") or 0) + 1))
        prev = by_scene.get(sid, {})
        merged = dict(prev)
        merged.update(row)
        # Preserve successful IDs from an earlier attempt if a later retry reports less.
        for key in ("videoMediaIds", "video_media_ids"):
            old_ids = list(prev.get(key) or [])
            new_ids = list(row.get(key) or [])
            if old_ids or new_ids:
                vals = []
                for x in old_ids + new_ids:
                    x = str(x or "").strip()
                    if x and x not in vals:
                        vals.append(x)
                merged["videoMediaIds"] = vals
        old_image = _normalize_result_image(prev)
        new_image = _normalize_result_image(row)
        if old_image.get("mediaId") and not new_image.get("mediaId"):
            merged["imageMediaId"] = old_image["mediaId"]
            if old_image.get("url"):
                merged["imageUrl"] = old_image["url"]
            if old_image.get("title"):
                merged["imageTitle"] = old_image["title"]
        by_scene[sid] = merged
    return {
        "ok": bool(reported_ok),
        "results": [by_scene[k] for k in sorted(by_scene)],
        "failures": list(failures or []),
        "updatedAt": utcnow(),
    }


def _sync_checkpoint_from_result(job_id: str, row: dict[str, Any]) -> None:
    scene_id = int(row.get("sceneId") or row.get("scene_id") or (int(row.get("index") or 0) + 1))
    image = _normalize_result_image(row)
    vids = [str(x) for x in (row.get("videoMediaIds") or row.get("video_media_ids") or []) if x]
    image_state = str(row.get("imageState") or row.get("image_state") or "").lower()
    video_state = str(row.get("videoState") or row.get("video_state") or "").lower()
    _checkpoint_update(
        job_id,
        scene_id,
        image_status="ready" if image.get("mediaId") else ("error" if image_state in {"error","failed"} else None),
        image_media_id=str(image.get("mediaId") or "") or None,
        video_status="generated" if vids else ("error" if video_state in {"error","failed"} else None),
        video_media_ids=vids,
        last_error=str(row.get("error") or "") or None,
    )


def _parenting_resume_plan(job_id: str) -> dict[str, Any]:
    """Build a non-destructive scene-level resume plan for Parenting jobs."""
    job = get_flow_job(job_id) or {}
    scenes = list(job.get("scenes") or [])
    # Upgrade/restart compatibility: rebuild checkpoints from any historical result_json
    # before looking at assets. Older extension versions often returned media IDs only
    # in the final/partial result payload. Those IDs are valuable and must prevent redo.
    old_result = job.get("result") if isinstance(job.get("result"), dict) else loads(job.get("result_json"), {})
    if isinstance(old_result, dict):
        for row in (old_result.get("results") or []):
            if isinstance(row, dict):
                try:
                    _sync_checkpoint_from_result(job_id, row)
                except Exception:
                    pass
    unresolved: list[dict[str, Any]] = []
    download_missing: dict[int, list[str]] = {}
    complete_scene_ids: list[int] = []
    permanent_failed_scenes: list[int] = []
    for pos, scene in enumerate(scenes, 1):
        sid = int(scene.get("sceneId") or pos)
        cp = _sync_checkpoint_from_assets(job_id, sid)
        expected = _expected_scene_video_count(scene)
        local_video_paths = [p for p in (cp.get("video_local_paths") or []) if p and Path(str(p)).exists() and _video_file_is_valid(str(p))]
        local_video_mids = _local_video_media_ids(job_id, sid)
        if len(local_video_paths) >= expected or len(local_video_mids) >= expected:
            complete_scene_ids.append(sid)
            _checkpoint_update(job_id, sid, video_status="ready", last_error=None)
            continue

        if str(cp.get("video_status") or "").lower() == "permanent_failed" or int(cp.get("video_regen_count") or 0) > FLOW_VIDEO_MEDIA_MAX_REGENERATIONS:
            permanent_failed_scenes.append(sid)
            continue

        invalid_video_ids = {str(x or "").strip() for x in (cp.get("invalid_video_media_ids") or []) if str(x or "").strip()}
        known_video_ids = []
        for mid in cp.get("video_media_ids") or []:
            mid = str(mid or "").strip()
            if mid and mid not in invalid_video_ids and mid not in known_video_ids:
                known_video_ids.append(mid)
        if len(known_video_ids) >= expected:
            missing = [mid for mid in known_video_ids[:expected] if mid not in local_video_mids]
            if missing:
                download_missing[sid] = missing
                continue

        retry_scene = json.loads(json.dumps(scene, ensure_ascii=False))
        meta = retry_scene.get("metadata") if isinstance(retry_scene.get("metadata"), dict) else {}
        meta = dict(meta)
        image_mid = str(cp.get("image_media_id") or "").strip()
        if image_mid:
            meta["resumeImageMediaId"] = image_mid
            meta["resumeSkipImage"] = True
            meta["resumeSource"] = "server_checkpoint"
        if invalid_video_ids:
            meta["forceRegenerateVideo"] = True
            meta["invalidVideoMediaIds"] = sorted(invalid_video_ids)
            meta["videoRegenerationAttempt"] = int(cp.get("video_regen_count") or 0)
            meta["resumeSource"] = "server_media_invalidated"
        meta["resumeAttempt"] = int((job.get("retry_count") or 0))
        retry_scene["metadata"] = meta
        unresolved.append(retry_scene)
    return {
        "job": job,
        "scenes": scenes,
        "unresolved": unresolved,
        "download_missing": download_missing,
        "complete_scene_ids": complete_scene_ids,
        "permanent_failed_scenes": permanent_failed_scenes,
        "all_complete": bool(scenes) and len(complete_scene_ids) == len(scenes),
    }


def _is_transient_flow_error(text: str | None) -> bool:
    t = str(text or "").lower()
    permanent = (
        "quota exhausted", "content policy", "blocked by policy", "invalid prompt",
        "không hỗ trợ model", "model not supported", "permission denied",
    )
    if any(x in t for x in permanent):
        return False
    transient = (
        "timeout", "network", "websocket", "ws ", "extension", "debugger", "settings",
        "picker", "asset", "mediaid", "không thấy video post", "không thấy đúng media",
        "connection", "server restart", "interrupted", "temporarily", "rate limit", "429",
        "502", "503", "504", "không mở", "không phản hồi",
    )
    return not t or any(x in t for x in transient)


def _coerce_flow_results(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("results")
    if raw is None:
        nested = message.get("result")
        if isinstance(nested, dict):
            raw = nested.get("results") or nested.get("jobs") or nested.get("records")
        elif isinstance(nested, list):
            raw = nested
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [x for x in (raw or []) if isinstance(x, dict)]


def _normalize_result_image(row: dict[str, Any]) -> dict[str, Any]:
    image = row.get("image") or row.get("selectedImage") or row.get("selected_image") or {}
    if not isinstance(image, dict):
        image = {}
    media_id = (image.get("mediaId") or image.get("media_id") or row.get("imageMediaId")
                or row.get("image_media_id") or (row.get("mediaId") if not row.get("videoMediaIds") else None))
    url = (image.get("url") or image.get("fifeUrl") or image.get("src") or row.get("imageUrl")
           or row.get("image_url") or row.get("fifeUrl"))
    title = image.get("title") or image.get("prompt") or row.get("imageTitle") or row.get("title")
    return {"mediaId": media_id, "url": url, "title": title}


def _local_video_media_ids(job_id: str, scene_id: int) -> set[str]:
    out=set()
    with conn() as c:
        rows=c.execute("SELECT media_id,local_path FROM assets WHERE job_id=? AND scene_id=? AND kind='video' ORDER BY created_at DESC",(job_id,scene_id)).fetchall()
    for r in rows:
        lp=str(r["local_path"] or "")
        if lp and Path(lp).exists() and _video_file_is_valid(lp) and r["media_id"]:
            out.add(str(r["media_id"]))
    return out




def _safe_flow_path_part(value: str, fallback: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())[:96].strip("._")
    return text or fallback


def _video_download_destination(job_id: str, scene_id: int, media_id: str, media_index: int = 0) -> Path:
    safe_job = _safe_flow_path_part(job_id, "job")
    safe_mid = _safe_flow_path_part(media_id, "media")[:20]
    outdir = OUTPUT_DIR / "flow_downloads" / safe_job / "raw"
    outdir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{int(media_index)+1}" if int(media_index or 0) > 0 else ""
    return outdir / f"{int(scene_id):03d}{suffix}_{safe_mid}.mp4"


def _signed_url_is_fresh(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    raw = str(meta.get("resolvedAt") or meta.get("resolved_at") or "").strip()
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return 0 <= age <= FLOW_VIDEO_SIGNED_URL_CACHE_MINUTES * 60
    except Exception:
        return False


class SignedVideoUrlExpired(RuntimeError):
    pass


async def _download_signed_video_to_server(url: str, dst: Path, *, media_id: str) -> str:
    """Download without browser UI. Keeps .part and resumes with HTTP Range."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("signed video URL rỗng")
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    max_bytes = FLOW_VIDEO_DOWNLOAD_MAX_MB * 1024 * 1024
    timeout = httpx.Timeout(
        connect=float(FLOW_VIDEO_DOWNLOAD_CONNECT_TIMEOUT),
        read=float(FLOW_VIDEO_DOWNLOAD_READ_TIMEOUT),
        write=30.0,
        pool=15.0,
    )
    last_error: Exception | None = None
    backoffs = [2, 5, 12, 30, 45, 60, 90, 120]
    for attempt in range(1, FLOW_VIDEO_DOWNLOAD_RETRIES + 1):
        existing = part.stat().st_size if part.exists() else 0
        headers = {
            "Accept": "video/mp4,video/*;q=0.9,application/octet-stream;q=0.8,*/*;q=0.5",
            "User-Agent": "Mozilla/5.0 ParentingContentFactory/4.5",
        }
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    status = int(response.status_code)
                    if status in {401, 403, 404, 410}:
                        raise SignedVideoUrlExpired(f"signed URL HTTP {status}")
                    if status == 416 and existing > 0:
                        total = None
                        m = re.search(r"\*/(\d+)", str(response.headers.get("content-range") or ""))
                        if m:
                            total = int(m.group(1))
                        if total is not None and existing >= total:
                            try:
                                _strict_video_probe(part)
                            except Exception as probe_exc:
                                try: part.unlink(missing_ok=True)
                                except Exception: pass
                                raise InvalidVideoFile(f"download đủ byte nhưng MP4 hỏng: {probe_exc}")
                            part.replace(dst)
                            _strict_video_probe(dst)
                            return str(dst.resolve())
                        raise RuntimeError(f"HTTP 416 nhưng .part chưa xác nhận đủ ({existing}/{total})")
                    if status not in {200, 206}:
                        raise RuntimeError(f"download video HTTP {status}")
                    ctype = str(response.headers.get("content-type") or "").lower()
                    if ctype and not any(x in ctype for x in ("video/", "application/octet-stream", "binary/octet-stream")):
                        raise RuntimeError(f"content-type không phải video: {ctype}")
                    append = existing > 0 and status == 206
                    if existing > 0 and status == 200:
                        existing = 0
                    mode = "ab" if append else "wb"
                    written = existing
                    iterator = response.aiter_bytes(chunk_size=1024 * 1024).__aiter__()
                    with part.open(mode) as fh:
                        while True:
                            try:
                                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=FLOW_VIDEO_DOWNLOAD_CHUNK_TIMEOUT)
                            except StopAsyncIteration:
                                break
                            if not chunk:
                                continue
                            written += len(chunk)
                            if written > max_bytes:
                                raise RuntimeError(f"video vượt FLOW_VIDEO_DOWNLOAD_MAX_MB={FLOW_VIDEO_DOWNLOAD_MAX_MB}MB")
                            fh.write(chunk)
                        fh.flush()
                        try: os.fsync(fh.fileno())
                        except OSError: pass
                    content_length = response.headers.get("content-length")
                    if content_length and status == 200 and int(content_length) > 0 and part.stat().st_size < int(content_length):
                        raise RuntimeError(f"file thiếu byte {part.stat().st_size}/{content_length}")
                    if part.stat().st_size <= 1024:
                        raise RuntimeError(f"file video quá nhỏ: {part.stat().st_size} bytes")
                    try:
                        _strict_video_probe(part)
                    except Exception as probe_exc:
                        try: part.unlink(missing_ok=True)
                        except Exception: pass
                        raise InvalidVideoFile(f"download xong nhưng ffprobe reject mediaId={media_id}: {probe_exc}")
                    part.replace(dst)
                    _strict_video_probe(dst)
                    return str(dst.resolve())
        except SignedVideoUrlExpired:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= FLOW_VIDEO_DOWNLOAD_RETRIES:
                break
            await asyncio.sleep(backoffs[min(attempt - 1, len(backoffs)-1)])
    raise RuntimeError(f"server download mediaId={media_id} thất bại sau {FLOW_VIDEO_DOWNLOAD_RETRIES} lần: {last_error}")


def _record_media_recovery_failure(job_id: str, scene_id: int, media_id: str, *, reason: str, failure_kind: str) -> int:
    cp = _checkpoint_row(job_id, scene_id)
    meta_all = dict(cp.get("video_download_meta") or {})
    old = meta_all.get(media_id) if isinstance(meta_all.get(media_id), dict) else {}
    count = int(old.get("recoveryFailureCycles") or 0) + 1
    _checkpoint_update(
        job_id, scene_id, video_status="generated", video_media_ids=[media_id],
        video_download_meta={media_id:{
            "recoveryFailureCycles": count,
            "lastFailureKind": failure_kind,
            "lastFailure": str(reason)[:1000],
            "lastFailureAt": utcnow(),
        }},
        last_error=str(reason)[:1000],
    )
    return count


def _invalidate_scene_video_chain(job_id: str, scene_id: int, failed_media_id: str, *, reason: str) -> tuple[int, list[str]]:
    cp = _checkpoint_row(job_id, scene_id)
    already_invalid = {str(x or "").strip() for x in (cp.get("invalid_video_media_ids") or []) if str(x or "").strip()}
    active = [str(x or "").strip() for x in (cp.get("video_media_ids") or []) if str(x or "").strip()]
    dead=[]
    for mid in active + [failed_media_id]:
        if mid and mid not in dead:
            dead.append(mid)
    # Idempotent against duplicate ERROR messages for the same already-invalid chain.
    increment = 0 if dead and all(mid in already_invalid for mid in dead) else 1
    _checkpoint_update(
        job_id, scene_id,
        video_status="regen_required",
        invalidate_video_media_ids=dead,
        replace_video_media_ids=[],
        replace_video_local_paths=[],
        video_download_meta={mid:{"invalid":True,"invalidAt":utcnow(),"invalidReason":str(reason)[:1000]} for mid in dead},
        video_regen_inc=increment,
        last_error=f"mediaId unusable -> regenerate: {reason}"[:1000],
    )
    cp2=_checkpoint_row(job_id, scene_id)
    return int(cp2.get("video_regen_count") or 0), dead


def _release_download_recovery(job_id: str, agent: AgentRuntime | None) -> None:
    DOWNLOAD_RECOVERY.pop(job_id, None)
    # Remove all server downloader tasks for this job from the registry. Running tasks
    # will still finish/cancel naturally but can no longer keep the job in DOWNLOAD_ONLY.
    for key, task in list(SERVER_VIDEO_DOWNLOAD_TASKS.items()):
        if key.startswith(f"{job_id}:"):
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
            SERVER_VIDEO_DOWNLOAD_TASKS.pop(key, None)
    if agent is not None:
        agent.active_job_ids.discard(job_id)
        agent.busy=bool(agent.active_job_ids)
        agent.job_id=next(iter(agent.active_job_ids),None)


async def _fail_video_after_regen_exhausted(job_id: str, scene_id: int, *, reason: str, agent: AgentRuntime | None) -> None:
    error=(f"permanent: scene {scene_id} video unusable after "
           f"{FLOW_VIDEO_MEDIA_MAX_REGENERATIONS} regenerations: {reason}")[:1000]
    _release_download_recovery(job_id, agent)
    _checkpoint_update(job_id, scene_id, video_status="permanent_failed", last_error=error)
    update_flow_job(job_id,status="failed",error=error,agent_id=None,last_stage="video_regen_exhausted",retry_reason=error,next_retry_at=None)
    # Persist permanence independently of the hook. This prevents CHECKPOINT_RECOVERY from
    # reviving a scene that already exhausted its bounded regeneration budget.
    with conn() as c:
        c.execute("UPDATE parenting_story_runs SET status='failed',error=?,updated_at=? WHERE flow_job_id=?", (error, utcnow(), job_id))
        c.execute("UPDATE parenting_auto_items SET status='failed',last_failure_class='flow_permanent',error=?,next_retry_at=NULL,updated_at=? WHERE flow_job_id=?", (error, utcnow(), job_id))
    await ui_broadcast({"type":"VIDEO_MEDIA_REGEN_EXHAUSTED","jobId":job_id,"sceneId":scene_id,"error":error})
    persist_event_log({"type":"VIDEO_MEDIA_REGEN_EXHAUSTED","jobId":job_id,"sceneId":scene_id,"error":error})
    if PARENTING_HANDLER is not None:
        try:
            await PARENTING_HANDLER.on_flow_complete(job_id,False)
        except Exception as exc:
            await ui_broadcast({"type":"PARENTING_HOOK_WARNING","jobId":job_id,"sceneId":scene_id,"error":str(exc)})
    await dispatch_jobs()


async def _handle_unusable_video_media(
    job_id: str,
    scene_id: int,
    media_id: str,
    *,
    reason: str,
    failure_kind: str,
    agent: AgentRuntime | None,
) -> str:
    """Bounded state machine: retry checking this ID, then regenerate, never spin forever."""
    cp0=_checkpoint_row(job_id, scene_id)
    # Artifact truth wins. If this exact mediaId is already a valid local MP4, a late
    # resolver/download error belongs to an older recovery request and MUST be ignored.
    if media_id in _local_video_media_ids(job_id, scene_id):
        _checkpoint_update(job_id, scene_id, video_status="ready", last_error=None)
        await ui_broadcast({"type":"VIDEO_MEDIA_STALE_RECOVERY_IGNORED","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"reason":"valid local video already exists"})
        return "local_already_ready"
    invalid0={str(x or "").strip() for x in (cp0.get("invalid_video_media_ids") or []) if str(x or "").strip()}
    active0={str(x or "").strip() for x in (cp0.get("video_media_ids") or []) if str(x or "").strip()}
    # Late URL/error messages can arrive from a recovery command queued before the scene
    # was regenerated. Never let a stale old mediaId invalidate the NEW mediaId.
    if media_id in invalid0 or (active0 and media_id not in active0):
        await ui_broadcast({"type":"VIDEO_MEDIA_STALE_RECOVERY_IGNORED","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"reason":reason})
        return "stale_ignored"
    cycle = _record_media_recovery_failure(job_id, scene_id, media_id, reason=reason, failure_kind=failure_kind)
    if cycle < FLOW_VIDEO_MEDIA_RESOLVE_CYCLES:
        if agent is not None and agent.ws:
            await asyncio.sleep(min(4, cycle))
            await agent.ws.send_text(dumps({
                "type":"DOWNLOAD_MEDIA_FILES","jobId":job_id,"sceneId":int(scene_id),
                "mediaIds":[media_id],"downloadMode":"server_signed_url","refreshSignedUrl":True,
            }))
            await ui_broadcast({"type":"VIDEO_SIGNED_URL_REFRESH","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"cycle":cycle,"reason":reason})
            return "retry_media_id"

    regen_count, dead = _invalidate_scene_video_chain(job_id, scene_id, media_id, reason=reason)
    _release_download_recovery(job_id, agent)
    await ui_broadcast({
        "type":"VIDEO_MEDIA_REGENERATE_REQUIRED","jobId":job_id,"sceneId":scene_id,
        "failedMediaId":media_id,"invalidMediaIds":dead,"regeneration":regen_count,
        "maxRegenerations":FLOW_VIDEO_MEDIA_MAX_REGENERATIONS,"reason":reason,
        "message":"Không verify/tải được mediaId sau recovery budget -> bỏ ID cũ và CREATE LẠI đúng scene, giữ image checkpoint.",
    })
    persist_event_log({
        "type":"VIDEO_MEDIA_REGENERATE_REQUIRED","jobId":job_id,"sceneId":scene_id,
        "failedMediaId":media_id,"invalidMediaIds":dead,"regeneration":regen_count,
        "maxRegenerations":FLOW_VIDEO_MEDIA_MAX_REGENERATIONS,"reason":reason,
    })
    if regen_count > FLOW_VIDEO_MEDIA_MAX_REGENERATIONS:
        await _fail_video_after_regen_exhausted(job_id, scene_id, reason=reason, agent=agent)
        return "failed"

    # Old mediaId is now filtered out by _parenting_resume_plan.  Reconcile therefore
    # produces an unresolved scene with resumeImageMediaId + forceRegenerateVideo, which
    # dispatches RUN_FLOW_JOB and clicks CREATE exactly for that missing scene.
    update_flow_job(job_id,status="interrupted",error=None,agent_id=None,next_retry_at=None,
                    retry_reason=f"regenerate_scene:{scene_id}"[:500],last_stage="video_regenerate_required")
    await _reconcile_parenting_flow_job(job_id,agent=None,reason=f"media_unusable_regenerate:{scene_id}",increment_retry=False)
    return "regenerate_scene"


async def _consume_video_download_url(message: dict[str, Any], agent: AgentRuntime | None = None) -> None:
    job_id = str(message.get("jobId") or (agent.job_id if agent else "") or "")
    scene_id = int(message.get("sceneId") or 0)
    media_id = str(message.get("mediaId") or "").strip()
    signed_url = str(message.get("signedUrl") or message.get("downloadUrl") or "").strip()
    media_index = int(message.get("mediaIndex") or 0)
    if not job_id or scene_id <= 0 or not media_id or not signed_url:
        return
    cp0=_checkpoint_row(job_id, scene_id)
    invalid0={str(x or "").strip() for x in (cp0.get("invalid_video_media_ids") or []) if str(x or "").strip()}
    active0={str(x or "").strip() for x in (cp0.get("video_media_ids") or []) if str(x or "").strip()}
    if media_id in invalid0 or (active0 and media_id not in active0):
        await ui_broadcast({"type":"VIDEO_MEDIA_STALE_URL_IGNORED","jobId":job_id,"sceneId":scene_id,"mediaId":media_id})
        return
    key = f"{job_id}:{scene_id}:{media_id}"
    existing_task = SERVER_VIDEO_DOWNLOAD_TASKS.get(key)
    if existing_task is not None and not existing_task.done():
        return

    async def run() -> None:
        resolved_at = str(message.get("resolvedAt") or utcnow())
        _checkpoint_update(
            job_id, scene_id, video_status="downloading", video_media_ids=[media_id],
            video_download_urls={media_id: signed_url},
            video_download_meta={media_id: {"resolvedAt": resolved_at, "source": str(message.get("source") or "extension"), "recoveryFailureCycles": 0, "invalid": False}},
            last_error=None,
        )
        await ui_broadcast({"type":"VIDEO_SERVER_DOWNLOAD_START","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"mediaIndex":media_index})
        dst = _video_download_destination(job_id, scene_id, media_id, media_index)
        try:
            local_path = await _download_signed_video_to_server(signed_url, dst, media_id=media_id)
            add_asset(job_id, scene_id, "video", url=signed_url, local_path=local_path, media_id=media_id,
                      metadata={"source":"SERVER_SIGNED_URL","resolvedAt":resolved_at,"mediaIndex":media_index})
            _checkpoint_update(job_id, scene_id, video_status="ready", video_media_ids=[media_id], video_local_paths=[local_path], last_error=None)
            await ui_broadcast({"type":"VIDEO_FILE_READY","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"localPath":local_path,"source":"server_signed_url"})
            persist_event_log({"type":"VIDEO_FILE_READY","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"localPath":local_path,"source":"server_signed_url"})
            await _finish_download_recovery_if_ready(job_id, agent)
        except Exception as exc:
            err = str(exc)
            # Critical V4.2 rule: a download failure NEVER discards a generated mediaId.
            _checkpoint_update(job_id, scene_id, video_status="generated", video_media_ids=[media_id], last_error=err)
            await ui_broadcast({"type":"VIDEO_SERVER_DOWNLOAD_RETRY","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"error":err})
            persist_event_log({"type":"VIDEO_SERVER_DOWNLOAD_ERROR","jobId":job_id,"sceneId":scene_id,"mediaId":media_id,"error":err})
            await _handle_unusable_video_media(
                job_id, scene_id, media_id, reason=err, failure_kind="server_download", agent=agent
            )
        finally:
            SERVER_VIDEO_DOWNLOAD_TASKS.pop(key, None)

    task = spawn(run())
    SERVER_VIDEO_DOWNLOAD_TASKS[key] = task

async def _request_video_download_recovery(
    job_id: str,
    agent: AgentRuntime,
    missing: dict[int, list[str]],
    *,
    post_action: str = "complete",
) -> bool:
    if not missing or not agent.ws:
        return False
    # Preserve Flow's video chain order. Sorting UUIDs breaks Veo Extend continuity.
    expected={}
    for scene,ids in missing.items():
        ordered=[]
        seen=set()
        for raw in ids or []:
            mid=str(raw or '').strip()
            if mid and mid not in seen:
                seen.add(mid); ordered.append(mid)
        if ordered:
            expected[int(scene)]=ordered
    if not expected:
        return False
    DOWNLOAD_RECOVERY[job_id]={
        "agent_id":agent.id,"expected":expected,"started_at":utcnow(),"errors":[],
        "post_action":str(post_action or "complete"),
    }
    update_flow_job(job_id,status="downloading",agent_id=agent.id,error=None,last_stage="download_only")
    for scene_id,ids in expected.items():
        cp = _checkpoint_row(job_id, int(scene_id))
        cached_urls = dict(cp.get("video_download_urls") or {})
        cached_meta = dict(cp.get("video_download_meta") or {})
        need_resolve=[]
        for index, mid in enumerate(ids):
            url = str(cached_urls.get(mid) or "").strip()
            if url and _signed_url_is_fresh(cached_meta.get(mid)):
                await _consume_video_download_url({
                    "jobId":job_id,"sceneId":int(scene_id),"mediaId":mid,"mediaIndex":index,
                    "signedUrl":url,"resolvedAt":cached_meta.get(mid,{}).get("resolvedAt"),"source":"checkpoint_cached_url",
                }, agent)
            else:
                need_resolve.append(mid)
        if need_resolve:
            await agent.ws.send_text(dumps({
                "type":"DOWNLOAD_MEDIA_FILES","jobId":job_id,"sceneId":scene_id,"mediaIds":need_resolve,
                "downloadMode":"server_signed_url"
            }))
        await ui_broadcast({"type":"VIDEO_DOWNLOAD_RECOVERY","jobId":job_id,"sceneId":scene_id,"count":len(ids),"mediaIds":ids,"serverDownload":True})
    return True


async def _finish_download_recovery_if_ready(job_id: str, agent: AgentRuntime | None = None) -> bool:
    rec=DOWNLOAD_RECOVERY.get(job_id)
    if not rec:
        return False
    for scene_id,expected in rec.get("expected",{}).items():
        if not set(expected).issubset(_local_video_media_ids(job_id,int(scene_id))):
            return False
    DOWNLOAD_RECOVERY.pop(job_id,None)
    if agent is None:
        aid=str(rec.get("agent_id") or "")
        agent=AGENTS.get(aid)
    if agent is not None:
        agent.active_job_ids.discard(job_id); agent.busy=bool(agent.active_job_ids); agent.job_id=next(iter(agent.active_job_ids),None)
    await ui_broadcast({"type":"VIDEO_DOWNLOAD_RECOVERED","jobId":job_id,"postAction":rec.get("post_action")})
    # Partial jobs must continue from their remaining scene checkpoints instead of
    # being falsely marked DONE after downloading only the successful clips.
    if str(rec.get("post_action") or "complete") == "resume":
        await _reconcile_parenting_flow_job(job_id, agent=agent, reason="download_recovered", increment_retry=False)
    else:
        update_flow_job(job_id,status="done",error=None)
        await ui_broadcast({"type":"FLOW_JOB_RESULT","jobId":job_id,"ok":True,"reportedOk":True,"downloadRecovered":True})
        if PARENTING_HANDLER is not None:
            try:
                await PARENTING_HANDLER.on_flow_complete(job_id,True)
            except Exception as exc:
                await ui_broadcast({"type":"PARENTING_HOOK_WARNING","jobId":job_id,"error":str(exc)})
        await dispatch_jobs()
    return True



async def _reconcile_parenting_flow_job(
    job_id: str,
    *,
    agent: AgentRuntime | None = None,
    reason: str = "",
    increment_retry: bool = False,
) -> dict[str, Any]:
    """Resume only missing Parenting scenes; never delete successful assets."""
    job = get_flow_job(job_id) or {}
    if str(job.get("kind") or "") not in {"parenting_story", "parenting_test_scene"}:
        return {"handled": False}
    plan = _parenting_resume_plan(job_id)
    if plan.get("permanent_failed_scenes"):
        error=f"permanent: scene(s) {','.join(str(x) for x in plan['permanent_failed_scenes'])} đã hết video regeneration budget"
        update_flow_job(job_id,status="failed",error=error,agent_id=None,last_stage="video_regen_exhausted",retry_reason=error,next_retry_at=None)
        with conn() as c:
            c.execute("UPDATE parenting_story_runs SET status='failed',error=?,updated_at=? WHERE flow_job_id=?",(error,utcnow(),job_id))
            c.execute("UPDATE parenting_auto_items SET status='failed',last_failure_class='flow_permanent',error=?,next_retry_at=NULL,updated_at=? WHERE flow_job_id=?",(error,utcnow(),job_id))
        return {"handled":True,"permanent":True,"scenes":plan["permanent_failed_scenes"]}
    if plan["all_complete"]:
        update_flow_job(job_id, status="flow_done", error=None, last_stage="flow_complete", next_retry_at=None)
        await ui_broadcast({
            "type": "FLOW_CHECKPOINT_COMPLETE", "jobId": job_id,
            "completeScenes": plan["complete_scene_ids"], "message": "Đủ video local từ checkpoint; không generate lại.",
        })
        if PARENTING_HANDLER is not None:
            try:
                await PARENTING_HANDLER.on_flow_complete(job_id, True)
            except Exception as exc:
                await ui_broadcast({"type":"PARENTING_HOOK_WARNING","jobId":job_id,"error":str(exc)})
        return {"handled": True, "complete": True}

    if plan["download_missing"]:
        if agent is None:
            candidates=[a for a in AGENTS.values() if agent_supports_job(a, str(job.get("kind") or ""))]
            agent=sorted(candidates,key=agent_priority,reverse=True)[0] if candidates else None
        if agent is not None:
            started = await _request_video_download_recovery(
                job_id, agent, plan["download_missing"], post_action="resume"
            )
            if started:
                await ui_broadcast({
                    "type":"FLOW_CHECKPOINT_DOWNLOAD","jobId":job_id,
                    "scenes":sorted(plan["download_missing"]),
                    "message":"Video đã tạo trên Flow; chỉ tải lại file local, không generate lại.",
                })
                return {"handled": True, "downloading": True}

    retry_count=int(job.get("retry_count") or 0)
    max_retries=max(1,int(job.get("max_retries") or 5))
    if increment_retry:
        retry_count += 1
    if retry_count > max_retries:
        # V4.1: retry budget is a CIRCUIT-BREAKER CYCLE, not a death sentence.
        # UI/picker/network failures can survive for minutes while already-created Flow
        # artifacts remain valid. Cool down, reset the cycle, then retry only missing scenes.
        cooldown=max(60,int(os.getenv("PARENTING_FLOW_RETRY_CYCLE_COOLDOWN","120") or 120))
        next_retry=(datetime.now(timezone.utc)+timedelta(seconds=cooldown)).isoformat(timespec="seconds")
        update_flow_job(
            job_id,status="queued",error=None,retry_count=0,agent_id=None,next_retry_at=next_retry,
            retry_reason="retry_cycle_cooldown",last_stage="retry_cycle_cooldown",
        )
        await ui_broadcast({
            "type":"FLOW_RETRY_CYCLE_COOLDOWN","jobId":job_id,"cooldownSec":cooldown,
            "completeScenes":plan["complete_scene_ids"],
            "missingScenes":[int(x.get('sceneId') or 0) for x in plan['unresolved']],
            "message":"Đã hết một chu kỳ retry tạm thời; giữ checkpoint, nghỉ rồi retry phần thiếu. Không đánh FAILED.",
        })
        return {"handled": True, "cooldown": True, "nextRetryAt": next_retry}

    delay=min(45, 2 ** max(0, retry_count-1))
    next_retry=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(timespec="seconds")
    update_flow_job(
        job_id,status="queued",agent_id=None,error=None,retry_count=retry_count,
        retry_reason=(reason or "checkpoint_resume")[:500],next_retry_at=next_retry,last_stage="resume_missing_scenes",
    )
    await ui_broadcast({
        "type":"FLOW_CHECKPOINT_REQUEUE","jobId":job_id,
        "retryCount":retry_count,"maxRetries":max_retries,
        "completeScenes":plan["complete_scene_ids"],
        "missingScenes":[int(x.get("sceneId") or 0) for x in plan["unresolved"]],
        "hasReusableImages":[int(x.get("sceneId") or 0) for x in plan["unresolved"] if (x.get("metadata") or {}).get("resumeImageMediaId")],
        "message":"Chỉ retry scene thiếu; giữ toàn bộ ảnh/video đã có.",
    })
    await dispatch_jobs()
    return {"handled": True, "requeued": True}


async def process_flow_result(job_id: str, message: dict[str, Any], agent: AgentRuntime) -> None:
    reported_ok = bool(message.get("ok"))
    results = _coerce_flow_results(message)
    result_payload = _merge_result_payload(job_id, results, reported_ok, message.get("failures") or [])
    result_payload["rawShape"] = sorted(message.keys())
    job = get_flow_job(job_id) or {}
    kind = str(job.get("kind") or "")
    is_video_test = kind == "video_test"
    is_factory_v2 = kind.startswith("factory_v2_")
    is_persona_angle_pack = kind in {"persona_angle_pack", "persona_angle"}
    is_parenting_master = kind == "parenting_character_master"
    is_parenting_video = kind in {"parenting_test_scene", "parenting_story"}
    needs_local_render = is_video_test or is_factory_v2

    # Parenting jobs are checkpointed scene-by-scene. A partial result is NOT a terminal
    # job failure; successful scene assets must survive and only missing scenes are retried.
    if is_parenting_video:
        provisional_status = "checkpointing"
    else:
        provisional_status = ("flow_done" if reported_ok and (needs_local_render or is_parenting_master) else "done" if reported_ok else "partial_failed")
    update_flow_job(
        job_id, status=provisional_status, result_json=dumps(result_payload),
        error=(None if is_parenting_video else message.get("error")),
        last_stage=("checkpoint_result" if is_parenting_video else None),
    )
    if not reported_ok and get_content_queue_by_flow(job_id) and not is_parenting_video:
        update_content_queue_by_flow(job_id, status="failed", error=message.get("error") or "Flow job partial_failed")

    local_image_count = 0
    image_signal_count = 0
    recovery_missing: dict[int, list[str]] = {}
    for r in results:
        scene_id = int(r.get("sceneId") or r.get("scene_id") or (int(r.get("index") or 0) + 1))
        if is_parenting_video:
            _sync_checkpoint_from_result(job_id, r)
        image = _normalize_result_image(r)
        if image.get("mediaId") or image.get("url"):
            image_signal_count += 1
            local_path = await asyncio.to_thread(
                cache_image_sync, str(image.get("url") or ""), job_id, scene_id, image.get("mediaId")
            )
            if local_path:
                local_image_count += 1
            asset_id = add_asset(
                job_id, scene_id, "image", url=image.get("url"), local_path=local_path,
                media_id=image.get("mediaId"), title=image.get("title"),
                metadata={"imageState": r.get("imageState") or r.get("image_state"), "source": "FLOW_JOB_RESULT"},
            )
            await ui_broadcast({
                "type": "IMAGE_READY", "jobId": job_id, "sceneId": scene_id, "assetId": asset_id,
                "mediaId": image.get("mediaId"), "title": image.get("title"), "localPath": local_path,
            })
            if PARENTING_HANDLER is not None:
                try:
                    await PARENTING_HANDLER.on_image_ready(job, scene_id, local_path, image.get("mediaId"), image.get("title"))
                except Exception as exc:
                    await ui_broadcast({"type":"PARENTING_HOOK_WARNING","jobId":job_id,"sceneId":scene_id,"error":str(exc)})
            if is_persona_angle_pack and local_path:
                scenes = job.get("scenes") or []
                scene_meta = (scenes[scene_id-1].get("metadata") or {}) if 0 < scene_id <= len(scenes) else {}
                profile_id = str(scene_meta.get("profileId") or "")
                angle = str(scene_meta.get("personaAngle") or "")
                if profile_id and angle:
                    try:
                        profile_after = await asyncio.to_thread(save_persona_angle_result, profile_id, angle, local_path)
                        await ui_broadcast({"type":"PERSONA_ANGLE_READY","jobId":job_id,"profileId":profile_id,"angle":angle,"angleCount":profile_after.get("persona_angle_count",0)})
                    except Exception as exc:
                        await ui_broadcast({"type":"PERSONA_ANGLE_FAILED","jobId":job_id,"profileId":profile_id,"angle":angle,"error":str(exc)})
        for download in r.get("downloads") or []:
            dl_path = download.get("localPath")
            dl_media = download.get("mediaId")
            with conn() as c:
                exists = c.execute(
                    "SELECT 1 FROM assets WHERE job_id=? AND scene_id=? AND kind='video' AND (media_id=? OR local_path=?) LIMIT 1",
                    (job_id, scene_id, dl_media, dl_path),
                ).fetchone()
            if not exists:
                add_asset(job_id, scene_id, "video", local_path=dl_path, media_id=dl_media, metadata={"source": "FLOW_JOB_RESULT"})
        if is_parenting_video:
            video_ids=[str(x) for x in (r.get("videoMediaIds") or r.get("video_media_ids") or []) if x]
            have=_local_video_media_ids(job_id,scene_id)
            missing=[mid for mid in video_ids if mid not in have]
            if missing: recovery_missing[scene_id]=missing

    if is_parenting_video and recovery_missing:
        started=await _request_video_download_recovery(job_id,agent,recovery_missing,post_action="resume")
        if started:
            await ui_broadcast({"type":"FLOW_DOWNLOAD_PENDING","jobId":job_id,"sceneCount":len(recovery_missing),"partial":not reported_ok})
            return

    if is_parenting_video:
        agent.active_job_ids.discard(job_id)
        agent.busy = bool(agent.active_job_ids)
        agent.job_id = next(iter(agent.active_job_ids), None)
        flow_err=str(message.get("error") or "")
        if not reported_ok and flow_err and not _is_transient_flow_error(flow_err):
            perr=f"PERMANENT: {flow_err}"
            update_flow_job(job_id,status="failed",error=perr,last_stage="permanent_failure")
            await ui_broadcast({"type":"FLOW_PERMANENT_FAILURE","jobId":job_id,"error":perr})
            if PARENTING_HANDLER is not None:
                try: await PARENTING_HANDLER.on_flow_complete(job_id,False)
                except Exception: pass
            return
        outcome = await _reconcile_parenting_flow_job(
            job_id, agent=agent,
            reason=str(message.get("error") or ("partial scene failure" if not reported_ok else "result checkpoint")),
            increment_retry=not reported_ok,
        )
        await ui_broadcast({
            "type":"FLOW_JOB_CHECKPOINTED","jobId":job_id,"reportedOk":reported_ok,
            "sceneCount":len(results),"outcome":outcome,
        })
        return

    final_ok = reported_ok
    if is_parenting_master and reported_ok:
        scenes = job.get("scenes") or []
        cid = str((((scenes[0].get("metadata") or {}) if scenes else {}).get("characterId")) or "")
        if image_signal_count:
            # mediaId itself is a valid reusable Flow reference. A short-lived CDN URL
            # expiring must never turn a successfully generated character master into
            # a failed job. Mark DONE now, then recover a local copy in the background.
            update_flow_job(job_id, status="done", error=None)
            if local_image_count <= 0:
                mids=[]
                for rr in results:
                    im=_normalize_result_image(rr)
                    mid=str(im.get("mediaId") or "").strip()
                    if mid and mid not in mids:
                        mids.append(mid)
                if mids:
                    try:
                        await agent.ws.send_text(dumps({"type":"DOWNLOAD_IMAGE_MEDIA_FILES","jobId":job_id,"sceneId":1,"mediaIds":mids}))
                        await ui_broadcast({"type":"IMAGE_DOWNLOAD_RECOVERY","jobId":job_id,"sceneId":1,"count":len(mids),"characterId":cid})
                    except Exception as exc:
                        await ui_broadcast({"type":"IMAGE_DOWNLOAD_RECOVERY_WARNING","jobId":job_id,"sceneId":1,"characterId":cid,"error":str(exc)})
        else:
            final_ok = False
            error = "Flow báo DONE nhưng worker không trả image mediaId/url cho server. Cần extension v14.6.0+."
            update_flow_job(job_id, status="failed", error=error)
            await ui_broadcast({"type":"PARENTING_CHARACTER_SYNC_FAILED","jobId":job_id,"characterId":cid,"error":error})

    agent.active_job_ids.discard(job_id)
    agent.busy = bool(agent.active_job_ids)
    agent.job_id = next(iter(agent.active_job_ids), None)
    await ui_broadcast({"type": "FLOW_JOB_RESULT", "jobId": job_id, "ok": final_ok, "reportedOk": reported_ok, "sceneCount": len(results)})
    if is_persona_angle_pack:
        scenes = job.get("scenes") or []
        profile_id = str(((scenes[0].get("metadata") or {}) if scenes else {}).get("profileId") or "")
        if profile_id:
            profile_after = get_page_profile(profile_id) or {}
            await ui_broadcast({"type":"PERSONA_PACK_READY" if profile_after.get("persona_pack_ready") else "PERSONA_PACK_PARTIAL","jobId":job_id,"profileId":profile_id,"angleCount":profile_after.get("persona_angle_count",0),"ok":final_ok})
    await dispatch_jobs()
    if final_ok and is_video_test:
        spawn(render_video_test(job_id))
    elif final_ok and is_factory_v2:
        spawn(render_factory_v2(job_id))
    if PARENTING_HANDLER is not None:
        try:
            await PARENTING_HANDLER.on_flow_complete(job_id, final_ok)
        except Exception as exc:
            await ui_broadcast({"type":"PARENTING_HOOK_WARNING","jobId":job_id,"error":str(exc)})


def get_assets(limit: int = 200, job_id: str | None = None) -> list[dict[str, Any]]:
    with conn() as c:
        if job_id:
            rows = c.execute("SELECT * FROM assets WHERE job_id=? ORDER BY created_at DESC LIMIT ?", (job_id, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM assets ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

        # Batch QC lookup instead of one SQLite connection/query per final video.
        final_job_ids = list(dict.fromkeys(
            str(r["job_id"]) for r in rows if r["kind"] == "final_video" and r["job_id"]
        ))
        qc_map: dict[str, dict[str, Any]] = {}
        if final_job_ids:
            ph = ",".join("?" for _ in final_job_ids)
            qrows = c.execute(
                f"SELECT * FROM qc_results WHERE job_id IN ({ph}) ORDER BY created_at DESC",
                final_job_ids,
            ).fetchall()
            for q in qrows:
                jid = str(q["job_id"])
                if jid not in qc_map:
                    qd = dict(q)
                    qd["passed"] = bool(qd.get("passed"))
                    qd["details"] = loads(qd.pop("details_json", None), {})
                    qc_map[jid] = qd

    out = []
    output_root = OUTPUT_DIR.resolve()
    for r in rows:
        d = dict(r)
        d["metadata"] = loads(d.pop("metadata_json"), {})
        if d.get("local_path"):
            try:
                rel = Path(d["local_path"]).resolve().relative_to(output_root)
                d["local_url"] = "/outputs/" + "/".join(rel.parts)
            except Exception:
                d["local_url"] = None
        else:
            d["local_url"] = None
        if d.get("kind") == "final_video" and d.get("job_id"):
            d["qc"] = qc_map.get(str(d["job_id"]))
        out.append(d)
    return out


def facebook_graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{path.lstrip('/')}"


def fb_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    r = requests.request(method, url, timeout=(20, 120), **kwargs)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:2000]}
    if not r.ok:
        raise RuntimeError(f"Facebook HTTP {r.status_code}: {data}")
    return data


def save_fb_page(page_id: str, name: str, token: str, tasks: Any = None) -> None:
    now = utcnow()
    with conn() as c:
        c.execute(
            """
            INSERT INTO fb_pages(id,name,access_token,tasks_json,enabled,created_at,updated_at)
            VALUES(?,?,?,?,1,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,access_token=excluded.access_token,
            tasks_json=excluded.tasks_json,updated_at=excluded.updated_at
            """,
            (page_id, name, token, dumps(tasks or []), now, now),
        )


def list_fb_pages() -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT id,name,tasks_json,enabled,last_test_json,created_at,updated_at FROM fb_pages ORDER BY name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tasks"] = loads(d.pop("tasks_json"), [])
        d["last_test"] = loads(d.pop("last_test_json"), None)
        out.append(d)
    return out


def get_fb_page_secret(page_id: str) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute("SELECT * FROM fb_pages WHERE id=?", (page_id,)).fetchone()
    return dict(r) if r else None


def list_ignored_fb_pages() -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT id,name,created_at FROM fb_page_ignored ORDER BY name,id").fetchall()
    return [dict(r) for r in rows]


def is_fb_page_ignored(page_id: str) -> bool:
    with conn() as c:
        r = c.execute("SELECT 1 FROM fb_page_ignored WHERE id=?", (page_id,)).fetchone()
    return bool(r)


def delete_fb_page(page_id: str, ignore: bool = True) -> dict[str, Any]:
    with conn() as c:
        row = c.execute("SELECT id,name FROM fb_pages WHERE id=?", (page_id,)).fetchone()
        if ignore:
            name = str(row["name"] if row else page_id)
            c.execute(
                "INSERT INTO fb_page_ignored(id,name,created_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                (page_id, name, utcnow()),
            )
        c.execute("DELETE FROM fb_pages WHERE id=?", (page_id,))
        # Tránh Page Profile giữ mapping tới Page đã xóa/token không còn.
        c.execute("UPDATE page_profiles SET facebook_page_id=NULL,updated_at=? WHERE facebook_page_id=?", (utcnow(), page_id))
    return {"ok": True, "page_id": page_id, "ignored": bool(ignore)}


def keep_only_fb_page(page_id: str) -> dict[str, Any]:
    if not get_fb_page_secret(page_id):
        raise ValueError("Không tìm thấy Page cần giữ")
    with conn() as c:
        rows = c.execute("SELECT id,name FROM fb_pages WHERE id<>?", (page_id,)).fetchall()
        now = utcnow()
        for r in rows:
            c.execute(
                "INSERT INTO fb_page_ignored(id,name,created_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                (str(r["id"]), str(r["name"]), now),
            )
            c.execute("UPDATE page_profiles SET facebook_page_id=NULL,updated_at=? WHERE facebook_page_id=?", (now, str(r["id"])))
        c.execute("DELETE FROM fb_pages WHERE id<>?", (page_id,))
    return {"ok": True, "kept_page_id": page_id, "removed": len(rows)}


def clear_ignored_fb_pages() -> int:
    with conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM fb_page_ignored").fetchone()["n"]
        c.execute("DELETE FROM fb_page_ignored")
    return int(n or 0)


def ffprobe_info(video_path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"ffprobe": False, "warning": "ffprobe không có trong PATH; bỏ qua kiểm tra video."}
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration,size",
        "-of", "json", str(video_path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        return {"ffprobe": True, "error": p.stderr.strip()}
    data = json.loads(p.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    duration = float(fmt.get("duration") or 0)
    ratio = width / height if height else 0
    warnings = []
    if width < 540 or height < 960:
        warnings.append("Facebook Reel API yêu cầu tối thiểu 540x960.")
    if ratio and abs(ratio - 9 / 16) > 0.03:
        warnings.append(f"Tỉ lệ hiện tại {width}x{height} không gần 9:16.")
    if duration and not (4 <= duration <= 60):
        warnings.append(f"Duration {duration:.2f}s nằm ngoài khoảng 4-60s của Reels Publishing API.")
    return {
        "ffprobe": True,
        "width": width,
        "height": height,
        "duration": duration,
        "size": int(fmt.get("size") or video_path.stat().st_size),
        "warnings": warnings,
    }


def create_publish_job(req: FacebookPublishRequest, dry_run: bool) -> str:
    pid = f"fb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    now = utcnow()
    with conn() as c:
        c.execute(
            "INSERT INTO publish_jobs(id,page_id,video_path,title,description,status,dry_run,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, req.page_id, req.video_path, req.title, req.description, "queued", int(dry_run), now, now),
        )
    return pid


def update_publish_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = utcnow()
    cols = ",".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [job_id]
    with conn() as c:
        c.execute(f"UPDATE publish_jobs SET {cols} WHERE id=?", vals)


def run_fb_publish(job_id: str) -> None:
    with conn() as c:
        job = c.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return
    job = dict(job)
    page = get_fb_page_secret(job["page_id"])
    if not page:
        update_publish_job(job_id, status="failed", error="Không tìm thấy Facebook Page/token")
        return
    path = Path(job["video_path"])
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        update_publish_job(job_id, status="failed", error=f"Không thấy video: {path}")
        return

    preflight = ffprobe_info(path)
    if bool(job["dry_run"]):
        update_publish_job(job_id, status="dry_run_ok", result_json=dumps({"preflight": preflight, "path": str(path)}))
        return

    token = page["access_token"]
    try:
        update_publish_job(job_id, status="starting")
        start = fb_request_json(
            "POST",
            facebook_graph_url(f"{page['id']}/video_reels"),
            params={"access_token": token, "upload_phase": "start"},
        )
        video_id = str(start.get("video_id") or "")
        upload_url = str(start.get("upload_url") or "")
        if not video_id or not upload_url:
            raise RuntimeError(f"Facebook không trả video_id/upload_url: {start}")

        update_publish_job(job_id, status="uploading", fb_video_id=video_id)
        size = path.stat().st_size
        headers = {
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(size),
            "Content-Type": "application/octet-stream",
        }
        with path.open("rb") as f:
            upload_resp = fb_request_json("POST", upload_url, headers=headers, data=f)

        update_publish_job(job_id, status="finishing")
        finish = fb_request_json(
            "POST",
            facebook_graph_url(f"{page['id']}/video_reels"),
            params={
                "access_token": token,
                "video_id": video_id,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "description": job.get("description") or "",
                "title": job.get("title") or "",
            },
        )
        status = fb_request_json(
            "GET",
            facebook_graph_url(video_id),
            params={"fields": "status", "access_token": token},
        )
        update_publish_job(
            job_id,
            status="submitted",
            result_json=dumps({"start": start, "upload": upload_resp, "finish": finish, "status": status, "preflight": preflight}),
        )
    except Exception as exc:
        update_publish_job(job_id, status="failed", error=str(exc))


def list_publish_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM publish_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["dry_run"] = bool(d["dry_run"])
        d["result"] = loads(d.pop("result_json"), None)
        out.append(d)
    return out


async def repair_failed_parenting_master_refs() -> int:
    """Repair old false-negative master jobs created by expired image CDN URLs."""
    repaired=0
    with conn() as c:
        rows=c.execute(
            "SELECT id,result_json,error FROM flow_jobs WHERE kind='parenting_character_master' AND status='failed' ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    for row in rows:
        err=str(row["error"] or "")
        if "server không cache được file ảnh local" not in err:
            continue
        payload=loads(row["result_json"],{})
        results=[x for x in (payload.get("results") or []) if isinstance(x,dict)] if isinstance(payload,dict) else []
        image=None
        for rr in results:
            im=_normalize_result_image(rr)
            if im.get("mediaId"):
                image=im;break
        if not image:
            continue
        job_id=str(row["id"]); job=get_flow_job(job_id) or {}
        update_flow_job(job_id,status="done",error=None)
        if PARENTING_HANDLER is not None:
            try:
                await PARENTING_HANDLER.on_image_ready(job,1,None,str(image.get("mediaId")),image.get("title"))
            except Exception:
                pass
        repaired+=1
        persist_event_log({"type":"PARENTING_MASTER_REPAIRED","jobId":job_id,"message":"Recovered old character master by Flow mediaId"})
    return repaired


async def recover_missing_character_local_refs(agent: AgentRuntime) -> int:
    if not agent_parenting_compatible(agent) or PARENTING_HANDLER is None:
        return 0
    count=0
    try:
        chars=PARENTING_HANDLER.list_characters()
    except Exception:
        return 0
    for ch in chars:
        mid=str(ch.get("reference_media_id") or "").strip()
        jid=str(ch.get("generated_job_id") or "").strip()
        if not mid or ch.get("reference_local_ready") or not jid:
            continue
        try:
            await agent.ws.send_text(dumps({"type":"DOWNLOAD_IMAGE_MEDIA_FILES","jobId":jid,"sceneId":1,"mediaIds":[mid]}))
            count+=1
        except Exception:
            continue
    return count


def repair_v1463_wrong_video_media_checkpoints() -> dict[str, int]:
    """One-time-compatible repair for 14.6.3 false VIDEO IDs.

    v14.6.3 accepted every response media[].name as a video ID. Input/generated images
    could therefore be persisted as VIDEO and the server would see image/jpeg. We clear
    only those broken scene checkpoints; valid local MP4 assets always win and are kept.
    """
    stats={"checked":0,"reset":0,"kept_local":0,"jobs":0}
    repaired_jobs=set()
    with conn() as c:
        rows=c.execute("SELECT job_id,scene_id,last_error,video_regen_count,video_status FROM flow_scene_checkpoints").fetchall()
    for row in rows:
        stats["checked"]+=1
        jid=str(row["job_id"]); sid=int(row["scene_id"])
        err=str(row["last_error"] or '').lower()
        wrong_type=("content-type không phải video: image/" in err or "content-type not video: image/" in err)
        exhausted=int(row["video_regen_count"] or 0)>FLOW_VIDEO_MEDIA_MAX_REGENERATIONS
        local_ids=_local_video_media_ids(jid,sid)
        if local_ids:
            # Any valid local MP4 is authoritative, even if a late resolver error later
            # marked its mediaId invalid.
            with conn() as c:
                ars=c.execute("SELECT media_id,local_path FROM assets WHERE job_id=? AND scene_id=? AND kind='video' ORDER BY created_at ASC",(jid,sid)).fetchall()
            mids=[]; paths=[]
            for a in ars:
                lp=str(a['local_path'] or ''); mid=str(a['media_id'] or '')
                if lp and Path(lp).exists() and _video_file_is_valid(lp):
                    if mid and mid not in mids: mids.append(mid)
                    if lp not in paths: paths.append(lp)
            if paths:
                with conn() as c:
                    c.execute("UPDATE flow_scene_checkpoints SET video_status='ready',video_media_ids_json=?,video_local_paths_json=?,invalid_video_media_ids_json='[]',video_regen_count=0,last_error=NULL,updated_at=? WHERE job_id=? AND scene_id=?",(dumps(mids),dumps(paths),utcnow(),jid,sid))
                stats['kept_local']+=1; repaired_jobs.add(jid)
                continue
        if wrong_type:
            # Clear only video side; image checkpoint remains intact, so next run creates
            # exactly one new video from the existing scene image.
            with conn() as c:
                c.execute("UPDATE flow_scene_checkpoints SET video_status='pending',video_media_ids_json='[]',video_local_paths_json='[]',video_download_urls_json='{}',video_download_meta_json='{}',invalid_video_media_ids_json='[]',video_regen_count=0,last_error=NULL,updated_at=? WHERE job_id=? AND scene_id=?",(utcnow(),jid,sid))
            stats['reset']+=1; repaired_jobs.add(jid)
    # Do not revive already-skipped AutoFB items automatically; that could violate rolling=1.
    # But if a repaired job is still active/failed and its item is not skipped, make it retryable.
    with conn() as c:
        for jid in repaired_jobs:
            item=c.execute("SELECT id,status FROM parenting_auto_items WHERE flow_job_id=?",(jid,)).fetchone()
            if item and str(item['status'] or '').lower()!='skipped':
                c.execute("UPDATE flow_jobs SET status='queued',error=NULL,agent_id=NULL,retry_count=0,next_retry_at=NULL,retry_reason='v45_wrong_media_type_repair',last_stage='checkpoint_repair',updated_at=? WHERE id=?",(utcnow(),jid))
                c.execute("UPDATE parenting_story_runs SET status='generating',error=NULL,updated_at=? WHERE flow_job_id=?",(utcnow(),jid))
                c.execute("UPDATE parenting_auto_items SET status='generating',error=NULL,last_failure_class='checkpoint_repair',resume_retry_count=0,next_retry_at=NULL,updated_at=? WHERE flow_job_id=?",(utcnow(),jid))
        stats['jobs']=len(repaired_jobs)
    return stats


async def agent_keepalive_loop() -> None:
    while True:
        try:
            for agent in list(AGENTS.values()):
                try:
                    await agent.ws.send_text(dumps({"type":"PING","serverTs":server_now()}))
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            persist_event_log({"type":"AGENT_KEEPALIVE_ERROR","message":str(exc)})
        await asyncio.sleep(20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global SERVER_SHUTTING_DOWN
    SERVER_SHUTTING_DOWN = False
    init_db()
    media_repair=repair_v1463_wrong_video_media_checkpoints()
    await repair_failed_parenting_master_refs()
    # User preference: each server boot starts with a clean log session.
    with conn() as c:
        c.execute("DELETE FROM event_logs")
    persist_event_log({"type": "SERVER_STARTED", "message": "Log mới cho phiên server hiện tại", "serverVersion": SERVER_VERSION})
    if media_repair.get("reset") or media_repair.get("kept_local"):
        persist_event_log({"type":"V45_MEDIA_TYPE_REPAIR","message":f"repair 14.6.3 wrong VIDEO ids: reset={media_repair['reset']} keep_local={media_repair['kept_local']} jobs={media_repair['jobs']}"})
    if PARENTING_HANDLER is not None:
        try:
            await PARENTING_HANDLER.campaign_resume_on_startup()
        except Exception as exc:
            persist_event_log({"type":"AUTO_FB_STARTUP_RESUME_ERROR","message":str(exc)})
    scheduler_task = asyncio.create_task(scheduler_loop(), name="publish-scheduler")
    keepalive_task = asyncio.create_task(agent_keepalive_loop(), name="agent-keepalive")
    try:
        yield
    finally:
        SERVER_SHUTTING_DOWN = True
        # Tell the extension to fail-closed BEFORE the websocket disappears.
        # If the process is killed hard, websocket onclose in v14.6.0 performs the same abort.
        for agent in list(AGENTS.values()):
            try:
                await agent.ws.send_text(dumps({"type":"SERVER_SHUTDOWN","reason":"Parenting server đang dừng; extension phải dừng mọi thao tác browser."}))
            except Exception:
                pass
        await asyncio.sleep(0.05)
        for agent in list(AGENTS.values()):
            try:
                await agent.ws.close(code=1001, reason="server shutdown")
            except Exception:
                pass
        scheduler_task.cancel(); keepalive_task.cancel()
        for task in (scheduler_task, keepalive_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title=APP_NAME, version=SERVER_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")


def _agent_supports_shopee_inspect(agent: AgentRuntime) -> bool:
    ver=_version_tuple(agent.version)
    padded=ver+(0,)*(3-len(ver))
    return str(agent.role or '').lower() in {"flow-extension","flow_extension","flow"} and padded[:3] >= (14,5,25)


async def inspect_shopee_product_via_extension(url: str, timeout_sec: int = 55) -> dict[str, Any]:
    # Shopee inspection uses its own temporary browser tab and is independent from the
    # Flow generation queue, so it remains available while image/video jobs are running.
    candidates=[a for a in AGENTS.values() if _agent_supports_shopee_inspect(a)]
    if not candidates:
        raise RuntimeError(f"Cần Flow Extension v14.6.0+ đang nối ws://127.0.0.1:{AGENT_PORT}/ws để đọc link Shopee.")
    agent=sorted(candidates,key=agent_priority,reverse=True)[0]
    request_id=f"shopee_{uuid.uuid4().hex[:12]}"
    loop=asyncio.get_running_loop()
    fut=loop.create_future()
    SHOPEE_INSPECT_WAITERS[request_id]=fut
    try:
        await agent.ws.send_text(dumps({"type":"SHOPEE_INSPECT_PRODUCT","requestId":request_id,"url":url}))
        await ui_broadcast({"type":"SHOPEE_INSPECT_STARTED","requestId":request_id,"agentId":agent.id,"url":url})
        result=await asyncio.wait_for(fut,timeout=max(10,min(60,int(timeout_sec))))
        if not isinstance(result,dict):
            raise RuntimeError("Extension trả dữ liệu Shopee không hợp lệ")
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "Không đọc được sản phẩm Shopee"))
        return dict(result.get("product") or {})
    finally:
        SHOPEE_INSPECT_WAITERS.pop(request_id,None)


def _agent_supports_shopee_search(agent: AgentRuntime) -> bool:
    ver=_version_tuple(agent.version)
    padded=ver+(0,)*(3-len(ver))
    return str(agent.role or '').lower() in {"flow-extension","flow_extension","flow"} and padded[:3] >= (14,5,25)


async def search_shopee_products_via_extension(keyword: str, limit: int = 6, timeout_sec: int = 45) -> list[dict[str, Any]]:
    kw=str(keyword or '').strip()[:120]
    if not kw:
        return []
    candidates=[a for a in AGENTS.values() if _agent_supports_shopee_search(a)]
    if not candidates:
        raise RuntimeError(f"Cần Flow Extension v14.6.0+ đang nối ws://127.0.0.1:{AGENT_PORT}/ws để tự tìm sản phẩm Shopee.")
    agent=sorted(candidates,key=agent_priority,reverse=True)[0]
    request_id=f"shopee_search_{uuid.uuid4().hex[:12]}"
    loop=asyncio.get_running_loop(); fut=loop.create_future(); SHOPEE_SEARCH_WAITERS[request_id]=fut
    try:
        await agent.ws.send_text(dumps({"type":"SHOPEE_SEARCH_PRODUCTS","requestId":request_id,"keyword":kw,"limit":max(1,min(20,int(limit))) }))
        await ui_broadcast({"type":"SHOPEE_SEARCH_STARTED","requestId":request_id,"agentId":agent.id,"keyword":kw})
        wait_sec=max(10,min(60,int(timeout_sec)))
        try:
            result=await asyncio.wait_for(fut,timeout=wait_sec)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"Shopee search timeout after {wait_sec}s; reload extension FLOW_WORKER de nap handler SHOPEE_SEARCH_PRODUCTS") from exc
        if not isinstance(result,dict):
            raise RuntimeError("Extension tra du lieu tim Shopee khong hop le")
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "Không tìm được sản phẩm Shopee"))
        rows=result.get("items") or []
        return [dict(x) for x in rows if isinstance(x,dict)]
    finally:
        SHOPEE_SEARCH_WAITERS.pop(request_id,None)


def _parenting_publish(req: FacebookPublishRequest) -> str:
    dry_run = FACEBOOK_DEFAULT_DRY_RUN if req.dry_run is None else bool(req.dry_run)
    pid = create_publish_job(req, dry_run)
    spawn(asyncio.to_thread(run_fb_publish, pid))
    return pid


from parenting import register_parenting_routes
PARENTING_HANDLER = register_parenting_routes(
    app,
    db_path=DB_PATH,
    output_dir=OUTPUT_DIR,
    create_flow_job=create_flow_job,
    default_flow_config=default_flow_config,
    dispatch_jobs=dispatch_jobs,
    get_flow_job=get_flow_job,
    update_flow_job=update_flow_job,
    add_asset=add_asset,
    router9_chat_json=router9_chat_json,
    router9_enabled=router9_enabled,
    ui_broadcast=ui_broadcast,
    spawn=spawn,
    create_publish_job=_parenting_publish,
    facebook_publish_request_cls=FacebookPublishRequest,
    inspect_product_url=inspect_shopee_product_via_extension,
    search_products=search_shopee_products_via_extension,
)


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")



@app.get("/api/logs")
def api_logs(limit: int = 300, mode: str = "short"):
    mode = "full" if str(mode).lower() == "full" else "short"
    return list_event_logs(limit=limit, mode=mode)


@app.delete("/api/logs")
def api_logs_clear():
    with conn() as c:
        c.execute("DELETE FROM event_logs")
    return {"ok": True}


@app.get("/api/ai/models")
def ai_models():
    try:
        models = router9_models() if router9_enabled() else []
        usable = router9_usable_models() if router9_enabled() else []
        policy = router9_model_policy_stats()
        return {"ok": True, "enabled": router9_enabled(), "base_url": ROUTER9_BASE_URL, "models": models,
                "usable": [m["id"] for m in usable],
                "disabled_count": sum(1 for m in models if m.get("disabled")),
                "blocked_github": policy["blocked_github"],
                "hard_disabled_count": policy["hard_disabled"],
                "soft_disabled_count": policy["soft_disabled"],
                "strict_mode": any(m.get("status") in {"ok","error"} for m in models if m.get("family") in {"gpt","gemini"})}
    except Exception as exc:
        return {"ok": False, "enabled": router9_enabled(), "base_url": ROUTER9_BASE_URL, "models": [], "error": str(exc)}


@app.post("/api/ai/models/test")
def ai_model_test(req: AiModelTestRequest):
    if not router9_enabled():
        raise HTTPException(400, "ROUTER9_API_KEY chưa cấu hình")
    return test_router9_model_sync(req.model_id)


@app.post("/api/ai/models/test-all")
def ai_models_test_all(background_tasks: BackgroundTasks):
    if not router9_enabled():
        raise HTTPException(400, "ROUTER9_API_KEY chưa cấu hình")
    rows = router9_models()
    ids = [m["id"] for m in rows if m["family"] in {"gpt","gemini"}]
    if not ids:
        raise HTTPException(400, "9router không trả GPT/Gemini model nào")
    for mid in ids:
        upsert_ai_model_status(mid, _model_family(mid), "testing", latency_ms=None, error=None)
    background_tasks.add_task(test_router9_models_background, ids)
    return {"ok": True, "testing": len(ids), "models": ids}




@app.post("/api/ai/models/clear-errors")
def ai_models_clear_errors():
    d = disable_failed_router9_models()
    return {"ok": True, **d, "disabled": d["soft_disabled"] + d["hard_disabled"],
            "message": f"Đã clear {d['soft_disabled']} lỗi tạm và permanent-block {d['hard_disabled']} model không hỗ trợ. GitHub luôn bị chặn."}


@app.post("/api/ai/models/reset-cleared")
def ai_models_reset_cleared():
    n = reset_disabled_router9_models()
    return {"ok": True, "restored": n}

@app.get("/api/health")
def health():
    with conn() as c:
        profile_count = int(c.execute("SELECT COUNT(*) n FROM page_profiles").fetchone()["n"])
    return {
        "ok": True,
        "app": APP_NAME,
        "time": server_now(),
        "agents_connected": len(AGENTS),
        "agents_idle": sum(1 for a in AGENTS.values() if not a.busy),
        "parenting_agents_compatible": sum(1 for a in AGENTS.values() if agent_parenting_compatible(a)),
        "parenting_agents_idle": sum(1 for a in AGENTS.values() if agent_parenting_compatible(a) and not a.busy),
        "incompatible_agents": sum(1 for a in AGENTS.values() if a.version and not agent_parenting_compatible(a)),
        "page_profiles": profile_count,
        "server_version": SERVER_VERSION,
        "graph_version": FB_GRAPH_VERSION,
        "web_port": WEB_PORT,
        "agent_port": AGENT_PORT,
        "extension_ws": f"ws://{HOST}:{AGENT_PORT}/ws",
        "legacy_observers": len(LEGACY_OBSERVER_CLIENTS),
        "legacy_compat": True,
        "extension_hint": f"Đặt extension WebSocket URL = ws://{HOST}:{AGENT_PORT}/ws",
        "agents": [a.public() for a in AGENTS.values()],
    }


@app.get("/api/dashboard/summary")
def dashboard_summary():
    """One cheap query for dashboard counters; replaces 4 large polling calls."""
    active_states = ("queued", "dispatching", "running", "flow_done", "rendering", "qc")
    placeholders = ",".join("?" for _ in active_states)
    with conn() as c:
        active_jobs = int(c.execute(
            f"SELECT COUNT(*) n FROM flow_jobs WHERE status IN ({placeholders})", active_states
        ).fetchone()["n"])
        profiles = int(c.execute("SELECT COUNT(*) n FROM page_profiles").fetchone()["n"])
        videos = int(c.execute(
            "SELECT COUNT(*) n FROM assets WHERE kind='final_video' OR (kind='video' AND scene_id=0)"
        ).fetchone()["n"])
    return {
        "ok": True,
        "server_version": SERVER_VERSION,
        "agents_connected": len(AGENTS),
        "agents_idle": sum(1 for a in AGENTS.values() if not a.busy),
        "parenting_agents_compatible": sum(1 for a in AGENTS.values() if agent_parenting_compatible(a)),
        "parenting_agents_idle": sum(1 for a in AGENTS.values() if agent_parenting_compatible(a) and not a.busy),
        "incompatible_agents": sum(1 for a in AGENTS.values() if a.version and not agent_parenting_compatible(a)),
        "active_jobs": active_jobs,
        "page_profiles": profiles,
        "videos_completed": videos,
        "agents": [a.public() for a in AGENTS.values()],
        "time": server_now(),
    }


@app.get("/api/dashboard")
def legacy_dashboard_readonly():
    """Read-only compatibility alias for an older dashboard tab."""
    data = dashboard_summary()
    data["compat"] = True
    data["read_only"] = True
    return data


@app.get("/api/jobs")
def legacy_jobs_readonly(limit: int = 100):
    """Read-only compatibility alias. Never creates/dispatches jobs."""
    n = min(max(int(limit), 1), 500)
    return list_flow_jobs_summary(n)


@app.get("/api/agents")
def agents():
    return [a.public() for a in AGENTS.values()]


@app.post("/api/agents/ping")
async def ping_agents():
    sent = 0
    for agent in list(AGENTS.values()):
        try:
            await agent.ws.send_text(dumps({"type": "PING"}))
            sent += 1
        except Exception:
            pass
    return {"sent": sent}


@app.post("/api/uploads")
async def upload_file(file: UploadFile = File(...)):
    safe_name = Path(file.filename or "upload.bin").name
    dest = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}_{safe_name}"
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    return {"ok": True, "path": str(dest.resolve()), "name": safe_name, "size": dest.stat().st_size}


@app.post("/api/flow/test")
async def flow_test(req: FlowTestRequest):
    refs = normalize_input_images(req.person_path, req.outfit_path)
    scene = {
        "sceneId": 1,
        "imagePrompt": req.prompt,
        "videoPrompt": "",
        "inputImages": refs,
        "metadata": {"mode": "test", "createdBy": "web-server-v1"},
    }
    flow = default_flow_config(
        imageModel=req.image_model,
        imageConcurrency=9,
        videoConcurrency=4,
        aspectRatio=req.aspect_ratio,
        imageOutputs=req.image_outputs,
    )
    job_id = create_flow_job("test", [scene], flow)
    await dispatch_jobs()
    return {"ok": True, "job_id": job_id, "status": get_flow_job(job_id)["status"]}


@app.post("/api/video/test")
async def video_test(req: VideoTestRequest):
    scenes = build_video_test_scenes(req)
    flow = default_flow_config(
        imageModel=req.image_model,
        imageConcurrency=min(req.image_concurrency, 10),
        aspectRatio="9:16",
        imageOutputs="x1",
        videoModel="NONE",
        maxSubmitsPerMinute=min(10, max(2, req.image_concurrency)),
    )
    job_id = create_flow_job("video_test", scenes, flow)
    await dispatch_jobs()
    return {
        "ok": True,
        "job_id": job_id,
        "scene_count": len(scenes),
        "status": get_flow_job(job_id)["status"],
        "render": {"duration_sec": req.duration_sec, "motion_preset": req.motion_preset, "size": "1080x1920"},
    }


@app.post("/api/flow/jobs")
async def flow_job(req: FlowJobRequest):
    flow = default_flow_config(**req.flow)
    job_id = create_flow_job(req.kind, req.scenes, flow)
    await dispatch_jobs()
    return {"ok": True, "job_id": job_id}

@app.post("/api/flow/download-media-test")
async def flow_download_media_test(req: FlowDownloadMediaTestRequest):
    media_id = str(req.media_id or "").strip()
    if not media_id:
        raise HTTPException(400, "missing media_id")
    agents = sorted(AGENTS.values(), key=agent_priority, reverse=True)
    candidates = [a for a in agents if agent_parenting_compatible(a) and a.ws]
    if not candidates:
        raise HTTPException(409, "Flow extension not connected")
    job_id = str(req.job_id or "").strip()
    if not job_id:
        scene = {
            "sceneId": int(req.scene_id),
            "imagePrompt": "Download media test",
            "videoPrompt": "Download media test",
            "inputImages": [],
            "metadata": {"mode": "download_media_test", "mediaId": media_id},
        }
        job_id = create_flow_job("download_media_test", [scene], default_flow_config(videoModel="NONE"))
        update_flow_job(job_id, status="downloading", next_retry_at=None, last_stage="manual_download_media_test")
    _checkpoint_update(job_id, int(req.scene_id), video_status="generated", video_media_ids=[media_id], replace_video_local_paths=[], last_error=None)
    started = await _request_video_download_recovery(job_id, candidates[0], {int(req.scene_id): [media_id]}, post_action="test")
    if not started:
        raise HTTPException(500, "DOWNLOAD_MEDIA_FILES send failed")
    return {"ok": True, "job_id": job_id, "scene_id": int(req.scene_id), "media_id": media_id, "agent_id": candidates[0].id}


@app.post("/api/factory/batch")
async def factory_batch(req: FactoryBatchRequest):
    scenes = build_factory_scenes(req)
    flow = default_flow_config(
        imageModel=req.image_model,
        imageConcurrency=min(req.image_concurrency, 10),
        aspectRatio=req.aspect_ratio,
        imageOutputs=req.image_outputs,
        maxSubmitsPerMinute=min(10, max(2, req.image_concurrency)),
    )
    job_id = create_flow_job("factory_batch", scenes, flow)
    await dispatch_jobs()
    return {"ok": True, "job_id": job_id, "scene_count": len(scenes), "flow": flow}


@app.get("/api/flow/jobs")
def flow_jobs(limit: int = 100, compact: bool = False):
    n = min(max(limit, 1), 500)
    return list_flow_jobs_summary(n) if compact else list_flow_jobs(n)


@app.get("/api/flow/jobs/{job_id}")
def flow_job_detail(job_id: str):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "Không thấy job")
    j["assets"] = get_assets(200, job_id)
    return j


@app.post("/api/flow/jobs/{job_id}/retry")
async def retry_flow_job(job_id: str):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "Khong thay job")
    update_flow_job(job_id, status="queued", error=None, agent_id=None, next_retry_at=None, last_stage="manual_retry")
    await dispatch_jobs()
    return {"ok": True, "job_id": job_id}

@app.post("/api/flow/jobs/{job_id}/cancel")
async def cancel_flow_job(job_id: str):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "Khong thay job")
    update_flow_job(
        job_id,
        status="failed",
        error="Manual cancel from V2.8 master",
        agent_id=None,
        next_retry_at=None,
        last_stage="manual_cancel",
    )
    if PARENTING_HANDLER is not None and str(j.get("kind") or "") in {"parenting_story", "parenting_test_scene"}:
        try:
            await PARENTING_HANDLER.on_flow_complete(job_id, False)
        except Exception:
            pass
    await ui_broadcast({"type": "FLOW_JOB_INTERRUPTED", "jobId": job_id, "retryable": False, "error": "Manual cancel"})
    return {"ok": True, "job_id": job_id, "status": "failed"}

@app.get("/api/flow/jobs/{job_id}/checkpoints")
def flow_job_checkpoints(job_id: str):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "Khong thay job")
    plan = _parenting_resume_plan(job_id) if str(j.get("kind") or "") in {"parenting_story", "parenting_test_scene"} else None
    return {"ok": True, "job_id": job_id, "job": j, "checkpoints": _checkpoint_rows(job_id), "resume_plan": plan}

@app.post("/api/flow/jobs/{job_id}/resume")
async def flow_job_resume(job_id: str):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "Khong thay job")
    update_flow_job(job_id, status="queued", error=None, agent_id=None, next_retry_at=None, last_stage="manual_resume")
    await dispatch_jobs()
    plan = _parenting_resume_plan(job_id) if str(j.get("kind") or "") in {"parenting_story", "parenting_test_scene"} else None
    return {"ok": True, "job_id": job_id, "queued": True, "resume_plan": plan}

@app.post("/api/flow/jobs/{job_id}/scenes/{scene_id}/retry")
async def flow_scene_retry(job_id: str, scene_id: int):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "Khong thay job")
    if scene_id < 1:
        raise HTTPException(400, "scene_id khong hop le")
    _checkpoint_update(
        job_id, int(scene_id), video_status="pending", video_media_ids=[], video_local_paths=[],
        video_download_urls={}, video_download_meta={}, last_error=None,
    )
    with conn() as c:
        c.execute(
            "UPDATE flow_scene_checkpoints SET invalid_video_media_ids_json='[]', video_regen_count=0, updated_at=? WHERE job_id=? AND scene_id=?",
            (utcnow(), job_id, int(scene_id)),
        )
    update_flow_job(job_id, status="queued", error=None, agent_id=None, next_retry_at=None, last_stage=f"manual_scene_retry_{scene_id}")
    await dispatch_jobs()
    return {"ok": True, "job_id": job_id, "scene_id": scene_id, "checkpoint": _checkpoint_row(job_id, scene_id)}

@app.post("/api/flow/jobs/{job_id}/finalize-parenting")
async def flow_job_finalize_parenting(job_id: str):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "Khong thay job")
    if PARENTING_HANDLER is None:
        raise HTTPException(503, "Parenting handler chua san sang")
    update_flow_job(job_id, status="flow_done", error=None, agent_id=None, next_retry_at=None, last_stage="manual_finalize")
    await PARENTING_HANDLER.on_flow_complete(job_id, True)
    return {"ok": True, "job_id": job_id, "status": "flow_done"}

@app.get("/api/assets")
def assets(limit: int = 200, job_id: str | None = None):
    return get_assets(min(max(limit, 1), 1000), job_id)


@app.get("/api/page-profiles")
def page_profiles():
    return list_page_profiles()


@app.get("/api/page-profiles/{profile_id}")
def page_profile_detail(profile_id: str):
    p = get_page_profile(profile_id)
    if not p:
        raise HTTPException(404, "Không thấy Page Profile")
    return p


@app.post("/api/page-profiles")
def page_profile_save(req: PageProfileSave):
    try:
        return {"ok": True, "profile": save_page_profile(req)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/page-profiles/{profile_id}/prepare-persona")
def page_profile_prepare_persona(profile_id: str):
    try:
        return {"ok": True, "profile": prepare_profile_persona(profile_id)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/page-profiles/{profile_id}/angles/{angle}/generate")
async def page_profile_generate_one_angle(profile_id: str, angle: str, force: bool = False):
    angle=str(angle or "").strip().lower()
    if angle not in {"left","right","back"}:
        raise HTTPException(400,"Góc phải là left/right/back")
    profile=get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404,"Không thấy Page Profile")
    if not profile.get("persona_ready"):
        try:
            profile=prepare_profile_persona(profile_id)
        except Exception as exc:
            raise HTTPException(400,str(exc))
    if profile.get(f"persona_{angle}_master_path") and not force:
        return {"ok":True,"already_ready":True,"angle":angle,"profile":profile}
    active=find_active_persona_angle_job(profile_id,angle)
    if active:
        return {"ok":True,"already_running":True,"angle":angle,"job_id":active["id"],"profile":profile}
    scene=build_persona_angle_scene(profile,angle)
    flow=default_flow_config(imageModel=profile.get("image_model") or "Nano Banana 2",imageConcurrency=9,videoConcurrency=4,videoModel="NONE",aspectRatio="9:16",imageOutputs="x1",maxSubmitsPerMinute=9,submitGapMs=700)
    job_id=create_flow_job("persona_angle",[scene],flow)
    await dispatch_jobs()
    return {"ok":True,"job_id":job_id,"angle":angle,"profile_id":profile_id}


@app.post("/api/page-profiles/{profile_id}/angles/generate-missing")
async def page_profile_generate_missing_angles(profile_id: str):
    profile=get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404,"Không thấy Page Profile")
    if not profile.get("persona_ready"):
        try:
            profile=prepare_profile_persona(profile_id)
        except Exception as exc:
            raise HTTPException(400,str(exc))

    jobs=[]
    missing=[]
    for angle in ("left","right","back"):
        if profile.get(f"persona_{angle}_master_path"):
            continue
        active=find_active_persona_angle_job(profile_id,angle)
        if active:
            jobs.append({"angle":angle,"job_id":active["id"],"already_running":True})
            continue
        missing.append(angle)

    # Important: missing angles belong in ONE Flow job so the extension can use
    # the shared IMAGE=9 pool. Three missing angles => 3 images run concurrently.
    if missing:
        scenes=build_persona_angle_scenes(profile, missing)
        flow=default_flow_config(
            imageModel=profile.get("image_model") or "Nano Banana 2",
            imageConcurrency=9,
            videoConcurrency=4,
            videoModel="NONE",
            aspectRatio="9:16",
            imageOutputs="x1",
            maxSubmitsPerMinute=9,
            submitGapMs=700,
        )
        jid=create_flow_job("persona_angle_pack",scenes,flow)
        for angle in missing:
            jobs.append({"angle":angle,"job_id":jid,"batched":True})

    await dispatch_jobs()
    return {"ok":True,"jobs":jobs,"profile":get_page_profile(profile_id),"pool":{"image":9,"video":4}}


# Backward-compatible alias. It now only generates missing angles and never force-regenerates all 3.
@app.post("/api/page-profiles/{profile_id}/generate-angles")
async def page_profile_generate_angles_compat(profile_id: str, force: bool = False):
    if force:
        # Do not spam three forced jobs. Tell the client to use per-angle regenerate.
        return {"ok":False,"blocked_bulk_force":True,"message":"REGEN 3 GÓC đã tắt để tránh spam. Hãy GEN LẠI từng góc.","profile":get_page_profile(profile_id)}
    return await page_profile_generate_missing_angles(profile_id)


@app.delete("/api/page-profiles/{profile_id}/angles/{angle}")
def page_profile_delete_angle(profile_id: str, angle: str):
    try:
        return {"ok":True,"profile":delete_persona_angle(profile_id,angle)}
    except RuntimeError as exc:
        raise HTTPException(409,str(exc))
    except Exception as exc:
        raise HTTPException(400,str(exc))


@app.post("/api/page-profiles/{profile_id}/angles/{angle}/use")
def page_profile_use_angle(profile_id: str, angle: str, enabled: bool = True):
    try:
        return {"ok":True,"profile":set_persona_angle_enabled(profile_id,angle,enabled)}
    except Exception as exc:
        raise HTTPException(400,str(exc))


@app.get("/api/page-profiles/{profile_id}/persona-pack-status")
def page_profile_persona_pack_status(profile_id: str):
    profile=get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404,"Không thấy Page Profile")
    return {"ok":True,"profile":profile,"active_jobs":list_active_persona_angle_jobs(profile_id),"server_version":SERVER_VERSION}


@app.delete("/api/page-profiles/{profile_id}")
def page_profile_delete(profile_id: str):
    delete_page_profile(profile_id)
    return {"ok": True}


@app.get("/api/scheduler/{profile_id}")
def scheduler_get(profile_id: str):
    try:
        return scheduler_status(profile_id)
    except Exception as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/scheduler/{profile_id}/start")
async def scheduler_start(profile_id: str, req: SchedulerConfigRequest):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "Không thấy Page Profile")
    if not profile.get("facebook_page_id"):
        raise HTTPException(400, "Page Profile chưa map Facebook Page")
    scheduler_mode = str(req.scheduler_mode or "INTERVAL").upper()
    if scheduler_mode not in {"INTERVAL", "DAILY_SLOTS"}:
        scheduler_mode = "INTERVAL"
    slots = _normalize_daily_slots(req.daily_slots)
    cfg = {
        "scheduler_mode": scheduler_mode,
        "daily_slots": slots,
        "daily_random_minutes": int(req.daily_random_minutes),
        "resume_random_minutes": int(req.resume_random_minutes),
        "mode": req.mode,
        "beat_image_count": req.beat_image_count,
        "beat_duration_sec": req.beat_duration_sec,
        "beat_motion_preset": req.beat_motion_preset,
        "i2v_clip_count": req.i2v_clip_count,
        "i2v_clip_duration": req.i2v_clip_duration,
        "image_concurrency": req.image_concurrency,
        "video_concurrency": req.video_concurrency,
    }
    old_cfg = _scheduler_cfg(profile)
    # Preserve today's randomized plan only when the daily-slot configuration is unchanged.
    if scheduler_mode == "DAILY_SLOTS" and old_cfg.get("scheduler_mode") == "DAILY_SLOTS":
        old_sig = old_cfg.get("daily_plan_signature")
        new_sig = {"slots": slots, "random": int(req.daily_random_minutes)}
        if old_sig == new_sig:
            for k in ("daily_plan_date", "daily_plan_signature", "daily_plan"):
                if k in old_cfg:
                    cfg[k] = old_cfg[k]
    now = datetime.now(timezone.utc)
    if scheduler_mode == "INTERVAL":
        last = _effective_last_publish(profile)
        if last:
            due = last + timedelta(minutes=int(req.publish_interval_minutes))
            if due <= now:
                jitter = int(req.resume_random_minutes)
                due = now + timedelta(minutes=random.randint(0, jitter) if jitter else 0)
        else:
            due = now + timedelta(minutes=int(req.first_publish_delay_minutes))
        next_at = due
    else:
        # Save config first so daily plan helper can persist into it.
        next_at = now
    with conn() as c:
        c.execute(
            "UPDATE page_profiles SET scheduler_enabled=1,publish_interval_minutes=?,buffer_target=?,scheduler_dry_run=?,scheduler_warmup=1,next_publish_at=?,scheduler_config_json=?,updated_at=? WHERE id=?",
            (int(req.publish_interval_minutes), int(req.buffer_target), 1 if req.facebook_dry_run else 0, next_at.isoformat(timespec="seconds"), dumps(cfg), utcnow(), profile_id),
        )
    profile = get_page_profile(profile_id) or profile
    if scheduler_mode == "DAILY_SLOTS":
        cfg = _scheduler_cfg(profile)
        cfg, _, target = _next_daily_entry(profile_id, profile, cfg, _scheduler_local_now(), startup_reconcile=True)
        _scheduler_set_next(profile_id, target)
    fill = await scheduler_fill_profile(profile_id)
    msg = (f"Scheduler ON · slots {','.join(slots)} · random ±{req.daily_random_minutes}m · buffer {req.buffer_target}" if scheduler_mode == "DAILY_SLOTS" else f"Scheduler ON · mỗi {req.publish_interval_minutes} phút · buffer {req.buffer_target}")
    persist_event_log({"type":"SCHEDULER_STARTED","profileId":profile_id,"message":msg + (" · DRY RUN" if req.facebook_dry_run else " · PUBLISH THẬT")})
    return {"ok": True, "fill": fill, "status": scheduler_status(profile_id)}


@app.post("/api/scheduler/{profile_id}/stop")
def scheduler_stop(profile_id: str):
    if not get_page_profile(profile_id):
        raise HTTPException(404, "Không thấy Page Profile")
    with conn() as c:
        c.execute("UPDATE page_profiles SET scheduler_enabled=0,updated_at=? WHERE id=?", (utcnow(), profile_id))
    persist_event_log({"type":"SCHEDULER_STOPPED","profileId":profile_id,"message":"Scheduler OFF"})
    return {"ok": True, "status": scheduler_status(profile_id)}


@app.post("/api/scheduler/{profile_id}/fill-now")
async def scheduler_fill_now(profile_id: str):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "Không thấy Page Profile")
    if not profile.get("scheduler_config"):
        raise HTTPException(400, "Chưa START Scheduler để lưu cấu hình generate")
    try:
        result = await scheduler_fill_profile(profile_id)
        return {"ok": True, "fill": result, "status": scheduler_status(profile_id)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/scheduler/{profile_id}/publish-now")
async def scheduler_publish_now(profile_id: str):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "Không thấy Page Profile")
    with conn() as c:
        c.execute("UPDATE page_profiles SET scheduler_warmup=0,next_publish_at=?,updated_at=? WHERE id=?", (utcnow(), utcnow(), profile_id))
    profile = get_page_profile(profile_id) or profile
    result = await scheduler_publish_due(profile)
    if result and result.get("ok"):
        await scheduler_fill_profile(profile_id)
    return {"ok": True, "publish": result, "status": scheduler_status(profile_id)}


@app.post("/api/factory/v2/generate")
async def factory_v2_generate(req: FactoryV2GenerateRequest):
    profile = get_page_profile(req.page_profile_id)
    if not profile:
        raise HTTPException(404, "Không thấy Page Profile")
    if not profile.get("enabled"):
        raise HTTPException(400, "Page Profile đang disabled")
    # Build first using a provisional run id, then persist final run + jobs.
    provisional = f"factory_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    created: list[dict[str, Any]] = []
    try:
        for i in range(req.videos):
            mode = choose_factory_mode(profile, req.mode)
            scenes, flow, kind = build_factory_v2_job(profile, req, provisional, i + 1, mode)
            jid = create_flow_job(kind, scenes, flow)
            created.append({"job_id": jid, "mode": mode, "scene_count": len(scenes)})
        with conn() as c:
            c.execute(
                "INSERT INTO factory_runs(id,page_profile_id,requested_count,requested_mode,auto_publish,facebook_dry_run,job_ids_json,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (provisional, profile["id"], req.videos, req.mode.upper(), 1 if req.auto_publish else 0, 1 if req.facebook_dry_run else 0,
                 dumps([x["job_id"] for x in created]), dumps(req.model_dump()), utcnow(), utcnow()),
            )
        await dispatch_jobs()
        return {"ok": True, "run_id": provisional, "profile": {"id": profile["id"], "name": profile["name"]}, "jobs": created}
    except Exception as exc:
        for item in created:
            update_flow_job(item["job_id"], status="failed", error=f"Factory setup lỗi: {exc}")
        raise HTTPException(400, str(exc))


@app.get("/api/factory/v2/runs")
def factory_v2_runs(limit: int = 50):
    return list_factory_runs(min(max(limit, 1), 200))


@app.get("/api/qc/{job_id}")
def qc_job(job_id: str):
    q = latest_qc(job_id)
    if not q:
        raise HTTPException(404, "Job chưa có QC")
    return q


@app.get("/api/facebook/pages")
def facebook_pages():
    return list_fb_pages()


@app.post("/api/facebook/pages/from-token")
def facebook_page_from_token(req: FacebookTokenResolveRequest):
    token = req.access_token.strip()
    if not token:
        raise HTTPException(400, "Thiếu Page Access Token")
    try:
        data = fb_request_json("GET", facebook_graph_url("me"), params={"fields": "id,name", "access_token": token})
        page_id = str(data.get("id") or "").strip()
        page_name = str(data.get("name") or page_id).strip()
        if not page_id:
            raise RuntimeError("Facebook token không trả Page ID")
        tasks = []
        try:
            detail = fb_request_json("GET", facebook_graph_url(page_id), params={"fields": "id,name,tasks", "access_token": token})
            page_name = str(detail.get("name") or page_name).strip() or page_name
            tasks = detail.get("tasks") or []
        except Exception:
            pass
        with conn() as c:
            c.execute("DELETE FROM fb_page_ignored WHERE id=?", (page_id,))
        save_fb_page(page_id, page_name, token, tasks)
        return {"ok": True, "page": {"id": page_id, "name": page_name, "tasks": tasks}}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Không nhận được Page từ token: {exc}")


@app.post("/api/facebook/pages")
def facebook_page_save(req: FacebookPageSave):
    page_id = req.page_id.strip()
    # Nếu người dùng chủ động Save thủ công thì bỏ Page khỏi ignore list.
    with conn() as c:
        c.execute("DELETE FROM fb_page_ignored WHERE id=?", (page_id,))
    save_fb_page(page_id, req.name.strip(), req.access_token.strip())
    return {"ok": True, "page_id": page_id}


@app.delete("/api/facebook/pages/{page_id}")
def facebook_page_delete(page_id: str, ignore: bool = True):
    return delete_fb_page(page_id.strip(), ignore=ignore)


@app.post("/api/facebook/pages/{page_id}/delete")
def facebook_page_delete_post(page_id: str):
    return delete_fb_page(page_id, ignore=True)


@app.post("/api/facebook/pages/{page_id}/keep-only")
def facebook_page_keep_only(page_id: str):
    try:
        return keep_only_fb_page(page_id.strip())
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/facebook/pages/ignored/list")
def facebook_pages_ignored():
    return list_ignored_fb_pages()


@app.delete("/api/facebook/pages/ignored/reset")
def facebook_pages_ignored_reset():
    return {"ok": True, "cleared": clear_ignored_fb_pages()}


@app.post("/api/facebook/pages/sync")
def facebook_pages_sync(req: FacebookSyncRequest):
    token = (req.user_access_token or os.getenv("FB_USER_ACCESS_TOKEN", "")).strip()
    if not token:
        raise HTTPException(400, "Thiếu User Access Token")
    try:
        data = fb_request_json(
            "GET",
            facebook_graph_url("me/accounts"),
            params={"fields": "id,name,access_token,tasks", "limit": 100, "access_token": token},
        )
        count = 0
        skipped = 0
        for p in data.get("data") or []:
            pid = str(p.get("id") or "").strip()
            if pid and p.get("access_token"):
                if is_fb_page_ignored(pid):
                    skipped += 1
                    continue
                save_fb_page(pid, str(p.get("name") or pid), str(p["access_token"]), p.get("tasks") or [])
                count += 1
        return {"ok": True, "saved": count, "skipped_ignored": skipped, "pages": list_fb_pages()}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/facebook/pages/{page_id}/test")
def facebook_page_test(page_id: str):
    page = get_fb_page_secret(page_id)
    if not page:
        raise HTTPException(404, "Chưa lưu Page/token")
    try:
        data = fb_request_json(
            "GET",
            facebook_graph_url(page_id),
            params={"fields": "id,name", "access_token": page["access_token"]},
        )
        with conn() as c:
            c.execute("UPDATE fb_pages SET last_test_json=?,updated_at=? WHERE id=?", (dumps(data), utcnow(), page_id))
        return {"ok": True, "data": data}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/facebook/publish/reel")
def facebook_publish_reel(req: FacebookPublishRequest, background_tasks: BackgroundTasks):
    if not get_fb_page_secret(req.page_id):
        raise HTTPException(404, "Chưa lưu Facebook Page/token")
    dry_run = FACEBOOK_DEFAULT_DRY_RUN if req.dry_run is None else bool(req.dry_run)
    job_id = create_publish_job(req, dry_run)
    background_tasks.add_task(run_fb_publish, job_id)
    return {"ok": True, "publish_job_id": job_id, "dry_run": dry_run}


@app.get("/api/facebook/publish/jobs")
def facebook_publish_jobs(limit: int = 100):
    return list_publish_jobs(min(max(limit, 1), 500))


@app.post("/api/facebook/preflight")
def facebook_preflight(payload: dict[str, Any]):
    raw = str(payload.get("video_path") or "").strip()
    if not raw:
        raise HTTPException(400, "Thiếu video_path")
    p = Path(raw)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    if not p.exists():
        raise HTTPException(404, f"Không thấy file: {p}")
    return {"ok": True, "path": str(p), "preflight": ffprobe_info(p)}


@app.websocket("/ws")
async def extension_ws(ws: WebSocket):
    await ws.accept()
    connection_id = f"agent_{uuid.uuid4().hex[:10]}"
    agent = AgentRuntime(connection_id, ws)
    AGENTS[connection_id] = agent
    try:
        while True:
            raw = await ws.receive_text()
            agent.last_seen = utcnow()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = str(msg.get("type") or "")
            if mtype == "AGENT_HELLO":
                agent.extension_id = msg.get("extensionId")
                agent.version = msg.get("version")
                agent.role = msg.get("role") or "flow-extension"
                agent.runtime = msg.get("runtime") or {}

                # Prevent one extension instance from appearing as several idle agents
                # after reconnects. Never terminate a duplicate that is actively running.
                if agent.extension_id:
                    for other_id, other in list(AGENTS.items()):
                        if other_id == agent.id or other.extension_id != agent.extension_id:
                            continue
                        if other.busy:
                            await ws.send_text(dumps({"type": "DUPLICATE_AGENT", "message": "Extension này đã có một kết nối đang chạy job."}))
                            await ws.close(code=1008)
                            return
                        try:
                            await other.ws.close(code=1000)
                        except Exception:
                            pass
                        AGENTS.pop(other_id, None)
                if agent_parenting_compatible(agent):
                    await ui_broadcast({"type": "AGENT_HELLO", "agent": agent.public()})
                    spawn(recover_missing_character_local_refs(agent))
                    if PARENTING_HANDLER is not None and hasattr(PARENTING_HANDLER, "sync_auto_flow_jobs_on_agent_online"):
                        try:
                            await PARENTING_HANDLER.sync_auto_flow_jobs_on_agent_online()
                        except Exception as exc:
                            await ui_broadcast({"type":"AUTO_FB_AGENT_SYNC_WARNING","error":str(exc)})
                await dispatch_jobs()
            elif mtype == "PONG":
                await ui_broadcast({"type": "AGENT_PONG", "agentId": agent.id})
            elif mtype == "AGENT_HEARTBEAT":
                if isinstance(msg.get("runtime"), dict):
                    agent.runtime = msg.get("runtime") or agent.runtime
                await ws.send_text(dumps({"type":"HEARTBEAT_ACK","serverTs":server_now()}))
            elif mtype == "FLOW_JOB_ACCEPTED":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                if job_id:
                    agent.active_job_ids.add(job_id)
                    agent.busy = True
                    agent.job_id = job_id
                    update_flow_job(job_id, status="running", agent_id=agent.id, error=None)
                    await ui_broadcast({"type": "FLOW_JOB_ACCEPTED", "jobId": job_id, "runId": msg.get("runId"), "queuePosition": msg.get("queuePosition"), "queueDepth": msg.get("queueDepth")})
            elif mtype == "FLOW_JOB_REJECTED":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                reason = str(msg.get("error") or "Agent rejected")
                if job_id:
                    agent.active_job_ids.discard(job_id)
                    job = get_flow_job(job_id) or {}
                    if str(job.get("kind") or "") in {"parenting_story","parenting_test_scene"}:
                        # Rejection is normally an availability/UI race, not proof that already
                        # generated artifacts are bad. Reconcile checkpoints and retry only gaps.
                        await _reconcile_parenting_flow_job(job_id, agent=None, reason=f"agent_rejected:{reason}", increment_retry=True)
                    else:
                        update_flow_job(job_id, status="failed", error=reason)
                        if get_content_queue_by_flow(job_id):
                            update_content_queue_by_flow(job_id, status="failed", error=reason)
                agent.busy = bool(agent.active_job_ids)
                agent.job_id = next(iter(agent.active_job_ids), None)
                await ui_broadcast({"type": "FLOW_JOB_REJECTED", "jobId": job_id, "error": reason})
                await dispatch_jobs()
            elif mtype == "SCENE_CHECKPOINT":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                scene_id = int(msg.get("sceneId") or 0)
                if job_id and scene_id:
                    job = get_flow_job(job_id) or {}
                    msg_epoch = int(msg.get("dispatchEpoch") or 0)
                    current_epoch = int(job.get("dispatch_epoch") or 0)
                    if msg_epoch and current_epoch and msg_epoch < current_epoch:
                        await ui_broadcast({"type":"STALE_SCENE_CHECKPOINT_IGNORED","jobId":job_id,"sceneId":scene_id,"dispatchEpoch":msg_epoch,"currentEpoch":current_epoch})
                        continue
                    image_mid = str(msg.get("imageMediaId") or "").strip() or None
                    video_ids = [str(x) for x in (msg.get("videoMediaIds") or []) if x]
                    stage = str(msg.get("stage") or "")
                    _checkpoint_update(
                        job_id, scene_id,
                        image_status=("ready" if image_mid else None),
                        image_media_id=image_mid,
                        video_status=("generated" if video_ids else ("error" if "FAILED" in stage.upper() else None)),
                        video_media_ids=video_ids,
                        last_error=str(msg.get("error") or "") or None,
                    )
                    if image_mid:
                        add_asset(job_id,scene_id,"image",media_id=image_mid,title="scene checkpoint",metadata={"source":"SCENE_CHECKPOINT","stage":stage})
                    update_flow_job(job_id,last_stage=f"scene_{scene_id}:{stage}"[:120])
                    await ui_broadcast({"type":"SCENE_CHECKPOINT","jobId":job_id,"sceneId":scene_id,"stage":stage,"imageMediaId":image_mid,"videoMediaIds":video_ids})
            elif mtype == "FLOW_JOB_RESULT":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                if job_id:
                    await process_flow_result(job_id, msg, agent)
            elif mtype == "FLOW_JOB_INTERRUPTED":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                retryable = bool(msg.get("retryable") or msg.get("controlledStop"))
                reason = str(msg.get("error") or "Agent interrupted")
                if job_id:
                    agent.active_job_ids.discard(job_id)
                    job = get_flow_job(job_id) or {}
                    is_parenting = str(job.get("kind") or "") in {"parenting_story","parenting_test_scene"}
                    if is_parenting and not SERVER_SHUTTING_DOWN and retryable:
                        # Only retry explicit retryable interruptions. Broker stale/manual cancel must not resurrect old jobs.
                        await _reconcile_parenting_flow_job(job_id, agent=None, reason=f"interrupted:{reason}", increment_retry=True)
                    else:
                        terminal_status = "interrupted" if retryable else "failed"
                        update_flow_job(job_id, status=terminal_status, error=reason, agent_id=None, next_retry_at=None)
                        if get_content_queue_by_flow(job_id):
                            if retryable:
                                update_content_queue_by_flow(job_id, status="queued", error=reason)
                            else:
                                update_content_queue_by_flow(job_id, status="failed", error=reason)
                agent.busy = bool(agent.active_job_ids)
                agent.job_id = next(iter(agent.active_job_ids), None)
                await ui_broadcast({"type": "FLOW_JOB_INTERRUPTED", "jobId": job_id, "retryable": retryable})
                if not SERVER_SHUTTING_DOWN:
                    await dispatch_jobs()
            elif mtype == "REFERENCE_MEDIA_REPLACED":
                # v14.6.0: a stable local character ref was re-uploaded because the
                # old Flow mediaId was no longer visible/usable. Persist the new mediaId
                # so future jobs and future server restarts do not keep probing the stale id.
                if PARENTING_HANDLER is not None and hasattr(PARENTING_HANDLER, "on_reference_media_replaced"):
                    try:
                        await PARENTING_HANDLER.on_reference_media_replaced(msg)
                    except Exception as exc:
                        await ui_broadcast({"type":"PARENTING_REFERENCE_MEDIA_WARNING","error":str(exc),"oldMediaId":msg.get("oldMediaId"),"newMediaId":msg.get("newMediaId")})
                await ui_broadcast({"type":"REFERENCE_MEDIA_REPLACED","role":msg.get("role"),"oldMediaId":msg.get("oldMediaId"),"newMediaId":msg.get("newMediaId"),"fileName":msg.get("fileName")})
            elif mtype == "IMAGE_READY":
                # Future-compatible: the current v14.5.4 extension sends images in FLOW_JOB_RESULT,
                # but V1 server already supports per-image streaming if the extension later emits IMAGE_READY.
                job_id = str(msg.get("jobId") or agent.job_id or "")
                scene_id = int(msg.get("sceneId") or 0)
                url = msg.get("url")
                media_id = msg.get("mediaId")
                local_path = await asyncio.to_thread(cache_image_sync, str(url or ""), job_id, scene_id, media_id)
                asset_id = add_asset(job_id, scene_id, "image", url=url, local_path=local_path, media_id=media_id, title=msg.get("title"), metadata=msg)
                if job_id and scene_id:
                    _checkpoint_update(job_id,scene_id,image_status="ready",image_media_id=str(media_id or "") or None,image_local_path=local_path)
                await ui_broadcast({"type": "IMAGE_READY", "jobId": job_id, "sceneId": scene_id, "assetId": asset_id, "mediaId": media_id, "title": msg.get("title"), "localPath": local_path})
                if PARENTING_HANDLER is not None:
                    try:
                        job = get_flow_job(job_id) or {}
                        await PARENTING_HANDLER.on_image_ready(job, scene_id, local_path, media_id, msg.get("title"))
                    except Exception as exc:
                        await ui_broadcast({"type":"PARENTING_HOOK_WARNING","jobId":job_id,"sceneId":scene_id,"error":str(exc)})
            elif mtype == "IMAGE_FILE_READY":
                ijob=str(msg.get("jobId") or agent.job_id or "")
                scene_id=int(msg.get("sceneId") or 0)
                media_id=str(msg.get("mediaId") or "") or None
                local_path=str(msg.get("localPath") or "") or None
                title=msg.get("title")
                asset_id=None
                if ijob and local_path:
                    with conn() as c:
                        row=c.execute("SELECT id FROM assets WHERE job_id=? AND scene_id=? AND kind='image' AND media_id=? ORDER BY created_at DESC LIMIT 1",(ijob,scene_id,media_id)).fetchone() if media_id else None
                        if row:
                            asset_id=str(row["id"]); c.execute("UPDATE assets SET local_path=? WHERE id=?",(local_path,asset_id))
                    if not asset_id:
                        asset_id=add_asset(ijob,scene_id,"image",local_path=local_path,media_id=media_id,title=title,metadata=msg)
                    _checkpoint_update(ijob,scene_id,image_status="ready",image_media_id=media_id,image_local_path=local_path)
                    await ui_broadcast({"type":"IMAGE_FILE_READY","jobId":ijob,"sceneId":scene_id,"assetId":asset_id,"mediaId":media_id,"localPath":local_path})
                    if PARENTING_HANDLER is not None:
                        try:
                            job=get_flow_job(ijob) or {}
                            await PARENTING_HANDLER.on_image_ready(job,scene_id,local_path,media_id,title)
                        except Exception as exc:
                            await ui_broadcast({"type":"PARENTING_HOOK_WARNING","jobId":ijob,"sceneId":scene_id,"error":str(exc)})
            elif mtype == "IMAGE_FILE_ERROR":
                ijob=str(msg.get("jobId") or agent.job_id or "")
                await ui_broadcast({"type":"IMAGE_FILE_ERROR","jobId":ijob,"sceneId":msg.get("sceneId"),"mediaId":msg.get("mediaId"),"error":str(msg.get("error") or "image recovery error")})
            elif mtype == "VIDEO_DOWNLOAD_URL_READY":
                vjob=str(msg.get("jobId") or agent.job_id or "")
                vscene=int(msg.get("sceneId") or 0)
                vmid=str(msg.get("mediaId") or "").strip()
                vurl=str(msg.get("signedUrl") or msg.get("downloadUrl") or "").strip()
                if vjob and vscene and vmid and vurl:
                    _checkpoint_update(
                        vjob,vscene,video_status="generated",video_media_ids=[vmid],
                        video_download_urls={vmid:vurl},
                        video_download_meta={vmid:{"resolvedAt":str(msg.get("resolvedAt") or utcnow()),"source":"extension_resolver","recoveryFailureCycles":0,"invalid":False}},
                        last_error=None,
                    )
                    await ui_broadcast({"type":"VIDEO_DOWNLOAD_URL_READY","jobId":vjob,"sceneId":vscene,"mediaId":vmid,"serverDownload":True})
                    await _consume_video_download_url(msg,agent)
            elif mtype == "VIDEO_DOWNLOAD_URL_ERROR":
                vjob=str(msg.get("jobId") or agent.job_id or "")
                vscene=int(msg.get("sceneId") or 0)
                vmid=str(msg.get("mediaId") or "").strip()
                err=str(msg.get("error") or "resolve signed URL error")
                if vjob and vscene and vmid:
                    _checkpoint_update(vjob,vscene,video_status="generated",video_media_ids=[vmid],last_error=err)
                    await _handle_unusable_video_media(
                        vjob,vscene,vmid,reason=err,failure_kind="signed_url_resolve",agent=agent
                    )
                await ui_broadcast({"type":"VIDEO_DOWNLOAD_URL_ERROR","jobId":vjob,"sceneId":vscene,"mediaId":vmid,"error":err})
            elif mtype == "VIDEO_FILE_READY":
                vjob=str(msg.get("jobId") or agent.job_id or "")
                vscene=int(msg.get("sceneId") or 0)
                vpath=str(msg.get("localPath") or "") or None
                vmid=str(msg.get("mediaId") or "") or None
                add_asset(vjob,vscene,"video",local_path=vpath,media_id=vmid,metadata=msg)
                if vjob and vscene:
                    _checkpoint_update(vjob,vscene,video_status="ready",video_media_ids=[vmid] if vmid else [],video_local_paths=[vpath] if vpath else [])
                await ui_broadcast({"type":"VIDEO_FILE_READY","jobId":vjob,"sceneId":vscene,"mediaId":vmid,"localPath":vpath})
                await _finish_download_recovery_if_ready(vjob,agent)
            elif mtype == "VIDEO_FILE_ERROR":
                vjob=str(msg.get("jobId") or agent.job_id or "")
                err=str(msg.get("error") or "download error")
                vscene=int(msg.get("sceneId") or 0)
                if vjob and vscene:
                    _checkpoint_update(vjob,vscene,video_status="download_error",last_error=err)
                if vjob in DOWNLOAD_RECOVERY:
                    DOWNLOAD_RECOVERY.pop(vjob,None)
                    job=get_flow_job(vjob) or {}
                    if str(job.get("kind") or "") in {"parenting_story","parenting_test_scene"}:
                        retry=int(job.get("retry_count") or 0)+1
                        max_retry=max(1,int(job.get("max_retries") or 5))
                        if retry<=max_retry:
                            delay=min(60,3*(2**max(0,retry-1)))
                            update_flow_job(
                                vjob,status="queued",error=None,agent_id=None,retry_count=retry,
                                retry_reason=f"download_error:{err}"[:500],
                                next_retry_at=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(timespec="seconds"),
                                last_stage="download_retry",
                            )
                            await ui_broadcast({"type":"VIDEO_DOWNLOAD_RETRY","jobId":vjob,"retryCount":retry,"delaySec":delay,"error":err})
                        else:
                            update_flow_job(vjob,status="failed",error=f"Video download retry exhausted: {err}",last_stage="download_failed")
                            if PARENTING_HANDLER is not None:
                                try: await PARENTING_HANDLER.on_flow_complete(vjob,False)
                                except Exception: pass
                    else:
                        cp=_checkpoint_row(vjob,vscene) if vscene else {}
                        if cp.get("video_media_ids"):
                            update_flow_job(vjob,status="queued",error=None,agent_id=None,retry_reason=f"download_only:{err}"[:500],next_retry_at=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat(timespec="seconds"),last_stage="download_only_retry")
                        else:
                            update_flow_job(vjob,status="failed",error=f"Video download recovery lỗi: {err}")
                    agent.active_job_ids.discard(vjob); agent.busy=bool(agent.active_job_ids); agent.job_id=next(iter(agent.active_job_ids),None)
                    await dispatch_jobs()
                await ui_broadcast({"type":"VIDEO_FILE_ERROR","jobId":vjob,"sceneId":vscene,"mediaId":msg.get("mediaId"),"error":err})
            elif mtype == "VIDEO_DOWNLOAD_SUMMARY":
                vjob=str(msg.get("jobId") or agent.job_id or "")
                await _finish_download_recovery_if_ready(vjob,agent)
            elif mtype == "SHOPEE_PRODUCT_RESULT":
                request_id=str(msg.get("requestId") or "")
                fut=SHOPEE_INSPECT_WAITERS.get(request_id)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                await ui_broadcast({"type":"SHOPEE_PRODUCT_RESULT","requestId":request_id,"ok":bool(msg.get("ok")),"error":msg.get("error"),"product":msg.get("product")})
            elif mtype == "SHOPEE_SEARCH_RESULT":
                request_id=str(msg.get("requestId") or "")
                fut=SHOPEE_SEARCH_WAITERS.get(request_id)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                await ui_broadcast({"type":"SHOPEE_SEARCH_RESULT","requestId":request_id,"ok":bool(msg.get("ok")),"error":msg.get("error"),"items":msg.get("items")})
            else:
                await ui_broadcast({"type": "AGENT_EVENT", "agentId": agent.id, "message": msg})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # One extension connection can own many jobs because the extension is the
        # concurrency/queue owner (IMAGE<=9, VIDEO<=4). On a socket reset, recover
        # EVERY active job, not only agent.job_id. Otherwise the other jobs stay
        # stuck as running/dispatching forever.
        active_jobs = set(agent.active_job_ids)
        if agent.job_id:
            active_jobs.add(str(agent.job_id))
        AGENTS.pop(connection_id, None)
        for lost_job in sorted(active_jobs):
            update_flow_job(lost_job, status="interrupted", error="Flow extension mất kết nối (retryable)", agent_id=None)
            if get_content_queue_by_flow(lost_job):
                # Keep it retryable. Parenting startup/agent-sync can safely requeue
                # the persisted Flow job when the extension reconnects.
                update_content_queue_by_flow(lost_job, status="queued", error="Flow extension mất kết nối; chờ reconnect")
        agent.active_job_ids.clear()
        agent.busy = False
        agent.job_id = None
        if agent_parenting_compatible(agent) or active_jobs:
            await ui_broadcast({"type": "AGENT_DISCONNECTED", "agentId": connection_id, "interruptedJobs": len(active_jobs)})


@app.websocket("/factory-ws")
async def legacy_factory_ws_observer(ws: WebSocket):
    """Compatibility sink for stale V2 UI clients.

    Critical safety rule: this socket is NOT added to AGENTS and therefore can
    never receive RUN_FLOW_JOB. It only observes lifecycle events.
    """
    await ws.accept()
    LEGACY_OBSERVER_CLIENTS.add(ws)
    try:
        await ws.send_text(dumps({
            "type": "SERVER_HELLO",
            "serverVersion": SERVER_VERSION,
            "compat": True,
            "readOnly": True,
            "message": "Legacy /factory-ws compatibility observer. Use /ws for Flow extension.",
        }))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                msg = {}
            if str(msg.get("type") or "").upper() == "PING":
                await ws.send_text(dumps({"type": "PONG", "compat": True, "readOnly": True}))
            else:
                await ws.send_text(dumps({"type": "COMPAT_READ_ONLY", "readOnly": True}))
    except Exception:
        pass
    finally:
        LEGACY_OBSERVER_CLIENTS.discard(ws)


@app.websocket("/ws/ui")
async def ui_ws(ws: WebSocket):
    await ws.accept()
    UI_CLIENTS.add(ws)
    try:
        await ws.send_text(dumps({"type": "UI_HELLO", "agents": [a.public() for a in AGENTS.values()]}))
        while True:
            _ = await ws.receive_text()
    except Exception:
        pass
    finally:
        UI_CLIENTS.discard(ws)


async def _proxy_pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _agent_proxy_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    peer = client_writer.get_extra_info("peername")
    try:
        server_reader, server_writer = await asyncio.open_connection(HOST, WEB_PORT)
    except Exception as exc:
        print(f"[AGENT BRIDGE] Không nối được web server {HOST}:{WEB_PORT}: {exc}")
        client_writer.close()
        try:
            await client_writer.wait_closed()
        except Exception:
            pass
        return

    # Transparent TCP proxy: WebSocket handshake / frames stay untouched.
    # Parenting extension bridge uses 8787 while FastAPI UI lives on a separate port.
    left = asyncio.create_task(_proxy_pipe(client_reader, server_writer))
    right = asyncio.create_task(_proxy_pipe(server_reader, client_writer))
    done, pending = await asyncio.wait({left, right}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def serve_dual_port() -> None:
    import uvicorn
    global WEB_PORT

    # Windows Proactor may emit a noisy WinError 10054 callback when Chrome closes
    # the extension WebSocket during reconnect/reload. The WS lifecycle below still
    # marks jobs interrupted/retryable; this filter only removes that redundant traceback.
    _install_windows_asyncio_exception_filter(asyncio.get_running_loop())
    init_db()
    WEB_PORT = _select_web_port()
    config = uvicorn.Config(
        app, host=HOST, port=WEB_PORT, reload=False, log_level=os.getenv("UVICORN_LOG_LEVEL","warning"), access_log=HTTP_ACCESS_LOG
    )
    web_server = uvicorn.Server(config)
    web_task = asyncio.create_task(web_server.serve())

    # Wait until the actual UI/API socket is listening before opening the bridge.
    for _ in range(200):
        if web_server.started:
            break
        if web_task.done():
            await web_task
            return
        await asyncio.sleep(0.05)

    proxy_server = None
    if AGENT_PORT != WEB_PORT:
        proxy_server = await asyncio.start_server(_agent_proxy_client, HOST, AGENT_PORT)
        print(f"[AGENT BRIDGE] TCP proxy {HOST}:{AGENT_PORT} -> {HOST}:{WEB_PORT}")

    print(f"\n{APP_NAME}")
    print(f"UI / REST API : http://{HOST}:{WEB_PORT}")
    print(f"Extension WS  : ws://{HOST}:{AGENT_PORT}/ws")
    print(f"API docs      : http://{HOST}:{WEB_PORT}/docs")
    print(f"Đặt WebSocket URL của extension thành ws://{HOST}:{AGENT_PORT}/ws; trình duyệt mở UI ở {WEB_PORT}.\n")

    try:
        await web_task
    finally:
        if proxy_server is not None:
            proxy_server.close()
            await proxy_server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(serve_dual_port())
    except KeyboardInterrupt:
        pass
