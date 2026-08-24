from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
import unicodedata
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import wave
from array import array
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
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
WEB_PORT = int(os.getenv("V28_PORT", "3000")) if os.getenv("V28_ISOLATED_FLOW") == "1" else int(os.getenv("WEB_PORT", "8897"))
WEB_PORT_FALLBACK_START = int(os.getenv("WEB_PORT_FALLBACK_START", "8897"))
WEB_PORT_FALLBACK_END = int(os.getenv("WEB_PORT_FALLBACK_END", "8910"))
# Legacy V1 used PORT=8786. In V1.1 that legacy value belongs to the extension bridge, not the UI.
AGENT_PORT = int(os.getenv("V28_PORT", "3000")) if os.getenv("V28_ISOLATED_FLOW") == "1" else int(os.getenv("AGENT_PORT", os.getenv("PORT", "8786")))
APP_NAME = os.getenv("APP_NAME", "Flow Content Factory V2.14.29")
SERVER_VERSION = "2.14.29"
MIN_EXTENSION_VERSION = "14.5.33"

# FULL AUTO recovery: keep Page running, but cap failure storms.
SCHEDULER_AUTO_RECOVERY_MAX_FAILURES = max(2, int(os.getenv("SCHEDULER_AUTO_RECOVERY_MAX_FAILURES", "3")))
SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES = max(1, int(os.getenv("SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES", "10")))
SCHEDULER_AUTO_RECOVERY_WINDOW_MINUTES = max(SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES, int(os.getenv("SCHEDULER_AUTO_RECOVERY_WINDOW_MINUTES", "30")))
FLOW_UI_BREAKER_MINUTES = max(2, min(60, int(os.getenv("FLOW_UI_BREAKER_MINUTES", "10"))))
AUTO_MUSIC_ENABLED = os.getenv("AUTO_MUSIC_ENABLED", "1").strip().lower() not in {"0","false","off","no"}
AUTO_MUSIC_VARIANTS = max(1, min(5, int(os.getenv("AUTO_MUSIC_VARIANTS", "3"))))
AUTO_MUSIC_SECONDS = max(15, min(60, int(os.getenv("AUTO_MUSIC_SECONDS", "24"))))
DISPATCH_ACK_TIMEOUT_SEC = max(8, min(60, int(os.getenv("DISPATCH_ACK_TIMEOUT_SEC", "20"))))
FLOW_JOB_STALE_MINUTES = max(8, min(120, int(os.getenv("FLOW_JOB_STALE_MINUTES", "12"))))
RECOVERY_IMAGE_TIMEOUT_PER_SCENE = max(10, min(90, int(os.getenv("RECOVERY_IMAGE_TIMEOUT_PER_SCENE", "30"))))
RECOVERY_VIDEO_TIMEOUT_PER_SCENE = max(20, min(180, int(os.getenv("RECOVERY_VIDEO_TIMEOUT_PER_SCENE", "70"))))

# V2.14.29 self-healing retry engine.
RETRY_TOTAL_MAX = max(4, min(30, int(os.getenv("RETRY_TOTAL_MAX", "12"))))
RETRY_JITTER_RATIO = max(0.0, min(0.5, float(os.getenv("RETRY_JITTER_RATIO", "0.20"))))
RETRY_MEDIA_ROUNDS = max(1, min(5, int(os.getenv("RETRY_MEDIA_ROUNDS", "3"))))
RETRY_RENDER_ATTEMPTS = max(1, min(4, int(os.getenv("RETRY_RENDER_ATTEMPTS", "2"))))
RETRY_PUBLISH_MAX = max(1, min(10, int(os.getenv("RETRY_PUBLISH_MAX", "5"))))
RETRY_DISK_MIN_FREE_GB = max(0.5, min(20.0, float(os.getenv("RETRY_DISK_MIN_FREE_GB", "2.0"))))
RETRY_AGENT_GRACE_SEC = max(10, min(90, int(os.getenv("RETRY_AGENT_GRACE_SEC", "30"))))
BUFFER_MAINTAIN_INTERVAL_SEC = max(3, min(20, int(os.getenv("BUFFER_MAINTAIN_INTERVAL_SEC", "5"))))
RETRY_VIDEO_MEDIA_ROUNDS = max(1, min(4, int(os.getenv("RETRY_VIDEO_MEDIA_ROUNDS", "2"))))

def _version_tuple(value: str | None) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(value or ""))
    return tuple(int(x) for x in nums[:4]) if nums else (0,)

def extension_version_compatible(value: str | None) -> bool:
    got = _version_tuple(value)
    need = _version_tuple(MIN_EXTENSION_VERSION)
    n = max(len(got), len(need))
    return got + (0,) * (n-len(got)) >= need + (0,) * (n-len(need))

def compatible_agents() -> list["AgentRuntime"]:
    return [
        a for a in AGENTS.values()
        if extension_version_compatible(a.version)
    ]

def require_compatible_agent() -> "AgentRuntime":
    agents = compatible_agents()
    if agents:
        return agents[0]
    seen = [str(a.version or "?") for a in AGENTS.values()]
    if seen:
        raise HTTPException(409, f"Extension quÃ¡ cÅ© ({', '.join(seen)}). Cáº§n >= {MIN_EXTENSION_VERSION}. Reload Ä‘Ãºng extension má»›i trÆ°á»›c khi cháº¡y Ä‘á»ƒ trÃ¡nh Ä‘á»‘t job Flow.")
    raise HTTPException(409, f"ChÆ°a cÃ³ Flow Agent tÆ°Æ¡ng thÃ­ch. Cáº§n extension >= {MIN_EXTENSION_VERSION}.")
FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v25.0").strip() or "v25.0"
AUTO_CACHE_FLOW_IMAGES = os.getenv("AUTO_CACHE_FLOW_IMAGES", "1").strip() not in {"0", "false", "False"}
FLOW_IMAGE_CACHE_TIMEOUT = int(os.getenv("FLOW_IMAGE_CACHE_TIMEOUT", "60"))
FACEBOOK_DEFAULT_DRY_RUN = os.getenv("FACEBOOK_DEFAULT_DRY_RUN", "1").strip() not in {"0", "false", "False"}
ROUTER9_BASE_URL = (os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or os.getenv("NINE_ROUTER_BASE_URL") or "http://127.0.0.1:20128/v1").rstrip("/")
ROUTER9_API_KEY = (os.getenv("9ROUTER_API_KEY") or os.getenv("ROUTER9_API_KEY") or os.getenv("NINE_ROUTER_API_KEY") or os.getenv("ROUTER_API_KEY") or "").strip()
ROUTER9_DEFAULT_MODEL = (os.getenv("9ROUTER_DEFAULT_MODEL") or os.getenv("ROUTER9_DEFAULT_MODEL") or "").strip()
ROUTER9_TIMEOUT = int(os.getenv("ROUTER9_TIMEOUT", "120"))
ROUTER9_ALLOWED_MODELS = [
    {"id":"cx/gpt-5.5","family":"gpt","label":"GPT 5.5"},
    {"id":"cx/gpt-5.4","family":"gpt","label":"GPT 5.4"},
    {"id":"ag/gemini-3.5-flash-lite","family":"gemini","label":"Gemini 3.5 Flash Lite"},
    {"id":"ag/gemini-3.1-pro-high","family":"gemini","label":"Gemini 3.1 Pro High"},
]
ROUTER9_ALLOWED_IDS = {x["id"] for x in ROUTER9_ALLOWED_MODELS}
SCHEDULER_TZ_NAME = os.getenv("SCHEDULER_TZ", "Asia/Ho_Chi_Minh")
try:
    SCHEDULER_TZ = ZoneInfo(SCHEDULER_TZ_NAME)
except (ZoneInfoNotFoundError, Exception) as exc:
    # Windows Python may not ship the IANA timezone database. tzdata is installed
    # by requirements.txt in V2.10.1, but never crash the whole server if it is missing.
    print(f"[TIMEZONE] KhÃ´ng load Ä‘Æ°á»£c {SCHEDULER_TZ_NAME}: {exc} -> fallback UTC+07:00")
    SCHEDULER_TZ = timezone(timedelta(hours=7))

# Visible server timestamps are ALWAYS Vietnam time. Scheduler timezone may be
# configurable, but it must never silently change the server/log clock.
SERVER_TZ_NAME = "Asia/Ho_Chi_Minh"
try:
    SERVER_TZ = ZoneInfo(SERVER_TZ_NAME)
except Exception:
    SERVER_TZ = timezone(timedelta(hours=7))

def server_now() -> datetime:
    return datetime.now(SERVER_TZ)

def server_now_iso() -> str:
    return server_now().isoformat(timespec="seconds")

def server_stamp() -> str:
    return server_now().strftime("%Y%m%d_%H%M%S")



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
    If that port is occupied, V1.2 automatically moves to 8897..8910.
    """
    preferred = int(WEB_PORT)
    if preferred != AGENT_PORT and _port_is_free(HOST, preferred):
        return preferred

    for port in range(WEB_PORT_FALLBACK_START, WEB_PORT_FALLBACK_END + 1):
        if port == AGENT_PORT:
            continue
        if _port_is_free(HOST, port):
            if port != preferred:
                print(f"[WEB PORT] {HOST}:{preferred} Ä‘ang báº­n -> tá»± chuyá»ƒn sang {HOST}:{port}")
            return port

    raise RuntimeError(
        f"KhÃ´ng tÃ¬m Ä‘Æ°á»£c cá»•ng web trá»‘ng. ÄÃ£ thá»­ {preferred} vÃ  "
        f"{WEB_PORT_FALLBACK_START}-{WEB_PORT_FALLBACK_END}. "
        "HÃ£y Ä‘áº·t WEB_PORT khÃ¡c trong .env."
    )


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        raise FileNotFoundError(f"KhÃ´ng tháº¥y áº£nh persona {slot}: {src}")
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
        raise FileNotFoundError(f"KhÃ´ng tháº¥y áº£nh persona: {src}")
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


@contextmanager
def conn(timeout: float = 30.0):
    c = sqlite3.connect(DB_PATH, timeout=float(timeout))
    c.row_factory = sqlite3.Row
    # WAL is configured once in init_db(). Re-running journal_mode on every
    # short-lived connection adds lock/IO overhead on Windows.
    c.execute("PRAGMA foreign_keys=ON")
    c.execute(f"PRAGMA busy_timeout={max(100, int(float(timeout) * 1000))}")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()




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
    # Startup must never appear frozen for 30+ seconds because an old process still
    # owns a SQLite write lock.  Use a short, bounded startup timeout.
    db_new = (not DB_PATH.exists()) or DB_PATH.stat().st_size == 0
    with conn(timeout=5.0) as c:
        if db_new:
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
        # Persistent retry state; restart must not reset retry budgets/backoff.
        ensure_column(c, "flow_jobs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "flow_jobs", "retry_stage_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "flow_jobs", "retry_stage", "TEXT")
        ensure_column(c, "flow_jobs", "retry_after", "TEXT")
        ensure_column(c, "flow_jobs", "failure_class", "TEXT")
        ensure_column(c, "flow_jobs", "last_error_at", "TEXT")
        ensure_column(c, "content_queue", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "content_queue", "retry_after", "TEXT")
        ensure_column(c, "content_queue", "failure_class", "TEXT")
        ensure_column(c, "publish_jobs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(c, "publish_jobs", "retry_after", "TEXT")
        ensure_column(c, "publish_jobs", "failure_class", "TEXT")
        _apply_model_block_policy_db(c)
        _migrate_legacy_content_defaults(c)
        # Restart recovery: same job enters RETRY_WAIT; do not create a replacement batch.
        restart_retry_at=(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat(timespec="seconds")
        c.execute(
            "UPDATE flow_jobs SET status='retry_wait',retry_stage='restart',retry_after=?,failure_class='restart',error='Server restart giá»¯a job',updated_at=? WHERE status IN ('dispatching','running')",
            (restart_retry_at,utcnow()),
        )
        c.execute(
            "UPDATE content_queue SET status='retry_wait',retry_after=?,failure_class='restart',error='Server restart Â· sáº½ resume SAME job',updated_at=? "
            "WHERE status='generating' AND flow_job_id IN (SELECT id FROM flow_jobs WHERE status IN ('retry_wait','interrupted','failed','qc_failed'))",
            (restart_retry_at,utcnow()),
        )
        c.execute(
            "UPDATE content_queue SET status='ready',error='Server restart khi Ä‘ang publish; sáº½ retry theo scheduler',updated_at=? WHERE status='publishing' AND video_path IS NOT NULL",
            (utcnow(),),
        )


def rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None



SUPER_SHAKE_PRESETS = {"impact_shake", "whip_shake", "flash_smash", "chaos_mix"}
LEGACY_SHAKE_PRESET_MAP = {
    "capcut_beat": "impact_shake",
    "flash_cut": "flash_smash",
    "mix": "chaos_mix",
    "smooth": "impact_shake",
    "dynamic_shake": "impact_shake",
    "super_shake": "chaos_mix",
}


def normalize_transition_preset(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = LEGACY_SHAKE_PRESET_MAP.get(raw, raw)
    return raw if raw in SUPER_SHAKE_PRESETS else "chaos_mix"


def strong_motion_filter_params(preset: Any, idx: int, per: float, fps: int = 30) -> tuple[str, str, str, str, str]:
    """Return (resolved_style, zoom, x, y, extra_filters) for aggressive hard-cut motion.

    The visible "impact" lives in the first/last few frames of each still segment.
    No dissolve/xfade is used; concat remains a true hard cut.
    """
    requested = normalize_transition_preset(preset)
    style = requested
    boost = 1.0
    if requested == "chaos_mix":
        cycle = ("impact_shake", "whip_shake", "flash_smash", "whip_shake", "impact_shake", "flash_smash")
        style = cycle[idx % len(cycle)]
        boost = 1.18

    frames = max(12, int(round(float(per) * max(1, int(fps)))))
    exit_start = max(7, frames - 5)
    direction = -1 if idx % 2 else 1

    if style == "whip_shake":
        # Large lateral whip during the first ~7 frames, then settle, then kick again at exit.
        z = (
            f"if(lt(on,7),{1.225*boost:.4f}-{0.013*boost:.4f}*on,"
            f"if(gt(on,{exit_start}),{1.145*boost:.4f}+{0.018*boost:.4f}*abs(sin((on-{exit_start})*2.4)),"
            f"{1.072*boost:.4f}+{0.010*boost:.4f}*abs(sin(on*0.24))))"
        )
        x = (
            "iw/2-(iw/zoom/2)+"
            f"if(lt(on,7),{direction}*{78*boost:.2f}*(1-on/7),"
            f"if(gt(on,{exit_start}),{direction*-1}*{34*boost:.2f}*sin((on-{exit_start})*2.8),"
            f"{7*boost:.2f}*sin(on*0.31)))"
        )
        y = (
            "ih/2-(ih/zoom/2)+"
            f"if(lt(on,7),{22*boost:.2f}*sin(on*2.7),"
            f"if(gt(on,{exit_start}),{18*boost:.2f}*cos((on-{exit_start})*2.9),"
            f"{5*boost:.2f}*cos(on*0.27)))"
        )
        extra = ""
    elif style == "flash_smash":
        z = (
            f"if(lt(on,6),{1.205*boost:.4f}-{0.015*boost:.4f}*on,"
            f"if(gt(on,{exit_start}),{1.125*boost:.4f}+{0.020*boost:.4f}*abs(sin((on-{exit_start})*2.7)),"
            f"{1.065*boost:.4f}+{0.011*boost:.4f}*abs(sin(on*0.23))))"
        )
        x = (
            "iw/2-(iw/zoom/2)+"
            f"if(lt(on,6),{31*boost:.2f}*sin(on*2.9),"
            f"if(gt(on,{exit_start}),{27*boost:.2f}*sin((on-{exit_start})*3.1),"
            f"{6*boost:.2f}*sin(on*0.29)))"
        )
        y = (
            "ih/2-(ih/zoom/2)+"
            f"if(lt(on,6),{24*boost:.2f}*cos(on*2.6),"
            f"if(gt(on,{exit_start}),{22*boost:.2f}*cos((on-{exit_start})*3.0),"
            f"{5*boost:.2f}*cos(on*0.25)))"
        )
        # 1â€“2 frame white smash at segment entry; avoids a long dissolve.
        extra = ",fade=t=in:st=0:d=0.055:color=white"
    else:  # impact_shake
        z = (
            f"if(lt(on,6),{1.185*boost:.4f}-{0.014*boost:.4f}*on,"
            f"if(gt(on,{exit_start}),{1.115*boost:.4f}+{0.020*boost:.4f}*abs(sin((on-{exit_start})*2.6)),"
            f"{1.060*boost:.4f}+{0.010*boost:.4f}*abs(sin(on*0.22))))"
        )
        x = (
            "iw/2-(iw/zoom/2)+"
            f"if(lt(on,6),{28*boost:.2f}*sin(on*2.8),"
            f"if(gt(on,{exit_start}),{24*boost:.2f}*sin((on-{exit_start})*3.0),"
            f"{6*boost:.2f}*sin(on*0.28)))"
        )
        y = (
            "ih/2-(ih/zoom/2)+"
            f"if(lt(on,6),{21*boost:.2f}*cos(on*2.5),"
            f"if(gt(on,{exit_start}),{19*boost:.2f}*cos((on-{exit_start})*2.9),"
            f"{5*boost:.2f}*cos(on*0.24)))"
        )
        extra = ""

    return style, z, x, y, extra


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
    motion_preset: str = "chaos_mix"


class FlowJobRequest(BaseModel):
    scenes: list[dict[str, Any]]
    flow: dict[str, Any] = Field(default_factory=dict)
    kind: str = "flow"


class FactoryBatchRequest(BaseModel):
    count: int = Field(default=8, ge=1, le=100)
    page_profile: str = "Vietnam Lifestyle"
    theme: str = "adult glamour lifestyle in Vietnam"
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
    theme: str = "adult glamour lifestyle in Vietnam"
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
    image_to_video_ratio: int = Field(default=0, ge=0, le=100)
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
    beat_motion_preset: str = "chaos_mix"
    i2v_clip_count: int = Field(default=3, ge=2, le=6)
    i2v_clip_duration: str = "4s"
    image_concurrency: int = Field(default=9, ge=1, le=10)
    video_concurrency: int = Field(default=4, ge=1, le=10)
    location_strategy: str = "distinct_vietnam_locations"
    location_anchor_strength: int = Field(default=85, ge=0, le=100)
    auto_publish: bool = False
    facebook_dry_run: bool = True


class SchedulerConfigRequest(BaseModel):
    enabled: bool = True
    scheduler_mode: str = "INTERVAL"
    scene_mode: str = "GYM"
    scene_mix: list[str] = Field(default_factory=list)
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
    beat_motion_preset: str = "chaos_mix"
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


class FacebookImportTokenRequest(BaseModel):
    token: str = Field(min_length=10)


class SimplePageSaveRequest(BaseModel):
    facebook_page_id: str
    profile_id: str | None = None
    profile_name: str | None = None
    persona_path: str | None = None
    video_mode: str = "AUTO"
    transition_preset: str = "chaos_mix"
    scene_mode: str = "GYM"
    scene_mix: list[str] = Field(default_factory=list)



class MusicUrlImportRequest(BaseModel):
    url: str = Field(min_length=10, max_length=3000)


class MusicRemoveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=5000)


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
        self.ready: bool = False
        self.phase: str = "idle"
        self.last_progress_monotonic: float = time.monotonic()
        self.last_progress_token: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "extension_id": self.extension_id,
            "version": self.version,
            "role": self.role,
            "busy": self.busy,
            "job_id": self.job_id,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "runtime": self.runtime,
            "ready": bool(self.ready),
            "phase": self.phase,
            "compatible": extension_version_compatible(self.version),
            "dispatchable": bool(self.ready and not self.busy and self.phase=="idle" and extension_version_compatible(self.version)),
            "required_version": MIN_EXTENSION_VERSION,
        }


AGENTS: dict[str, AgentRuntime] = {}
UI_CLIENTS: set[WebSocket] = set()
DISPATCH_LOCK = asyncio.Lock()
SCHEDULER_LOCK = asyncio.Lock()
BACKGROUND_TASKS: set[asyncio.Task] = set()
SIMPLE_START_TASKS: dict[str, asyncio.Task] = {}
SIMPLE_START_CANCELLED: set[str] = set()
SCHEDULER_RUNTIME_STATE: dict[str, str] = {}
FACTORY_RESUME_IN_FLIGHT: set[str] = set()
FACTORY_FINALIZE_IN_FLIGHT: set[str] = set()
FACTORY_RESUME_RETRY_AFTER: dict[str, float] = {}
AGENT_RECOVERY_LOCKS: dict[str, asyncio.Lock] = {}


def _touch_agent(agent: "AgentRuntime", phase: str | None = None, token: Any = None) -> None:
    if phase:
        agent.phase=phase
    if token is not None:
        token_text=str(token)
        if token_text==agent.last_progress_token:
            return
        agent.last_progress_token=token_text
    agent.last_progress_monotonic=time.monotonic()


def _agent_recovery_lock(agent: "AgentRuntime") -> asyncio.Lock:
    lock=AGENT_RECOVERY_LOCKS.get(agent.id)
    if lock is None:
        lock=asyncio.Lock(); AGENT_RECOVERY_LOCKS[agent.id]=lock
    return lock


def _agent_connected_ready(agent: "AgentRuntime") -> bool:
    return bool(AGENTS.get(agent.id) is agent and agent.ready)


def _scheduler_state_event(profile_id: str, state: str, message: str) -> None:
    key=str(profile_id)
    if SCHEDULER_RUNTIME_STATE.get(key)==state:
        return
    SCHEDULER_RUNTIME_STATE[key]=state
    persist_event_log({"type":f"SCHEDULER_{state}","profileId":profile_id,"message":message})



def spawn(coro: Any) -> asyncio.Task:
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


_EVENT_LOG_LAST: dict[str, float] = {}
_EVENT_LOG_EXACT_LAST: dict[str, float] = {}

def _skip_noisy_event_log(event: dict[str, Any]) -> bool:
    et=str(event.get("type") or "")
    msg=event.get("message")
    text=dumps(msg) if isinstance(msg,dict) else str(msg or "")
    now=time.monotonic()
    exact_src=f"{et}|{event.get('jobId') or event.get('job_id') or ''}|{text[-1600:]}"
    exact_key=hashlib.sha1(exact_src.encode("utf-8",errors="ignore")).hexdigest()
    last_exact=_EVENT_LOG_EXACT_LAST.get(exact_key,0.0)
    if now-last_exact<0.75:
        return True
    _EVENT_LOG_EXACT_LAST[exact_key]=now
    if et!="AGENT_EVENT":
        return False
    noisy=("MEDIA_GENERATION_STATUS_ACTIVE" in text or "VIDEO SUCCESS" in text or "IMAGE SUCCESS" in text or "áº¢nh Ä‘ang táº¡o song song" in text or "Video Ä‘ang táº¡o" in text)
    if not noisy:
        return False
    coarse=re.sub(r"\d+(?:\.\d+)?%","%",text)
    coarse=re.sub(r"(?:done|hoÃ n táº¥t)\s+\d+/\d+","done x/x",coarse,flags=re.I)
    coarse=re.sub(r"seq=\d+","seq=x",coarse)
    key=hashlib.sha1(coarse[-1400:].encode("utf-8",errors="ignore")).hexdigest()
    last=_EVENT_LOG_LAST.get(key,0.0)
    if now-last<2.0:
        return True
    _EVENT_LOG_LAST[key]=now
    if len(_EVENT_LOG_LAST)>2000 or len(_EVENT_LOG_EXACT_LAST)>4000:
        cutoff=now-120
        for store in (_EVENT_LOG_LAST,_EVENT_LOG_EXACT_LAST):
            for k,v in list(store.items()):
                if v<cutoff: store.pop(k,None)
    return False


def persist_event_log(event: dict[str, Any]) -> None:
    try:
        if _skip_noisy_event_log(event):
            return
        payload = {"ts": server_now_iso(), **event}
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
        return f"SERVER STARTED Â· v{payload.get('serverVersion') or '2.14.29'} Â· Asia/Ho_Chi_Minh +07:00 Â· log phiÃªn cÅ© Ä‘Ã£ clear"
    if et == "WS_ROUTE_SELFTEST_OK":
        return "WS ROUTE OK Â· /ws â†’ extension_ws"
    if et == "AUTO_MODE_SELECTED":
        return str(payload.get("message") or "AUTO RANDOM mode selected")
    if et == "AGENT_WS_ACCEPTED":
        return str(payload.get("message") or "WS ACCEPTED Â· Flow Agent")
    if et in {"AUTO_RETRY_SCHEDULED","AUTO_RETRY_DUE","IMAGE_RECOVERY_ROUND","VIDEO_RECOVERY_ROUND","FACTORY_RENDER_RETRY","QC_TECHNICAL_RETRY","SCHEDULER_HALF_OPEN_PROBE","SCHEDULED_PUBLISH_RETRY"}:
        return str(payload.get("message") or et)
    if et == "AGENT_HELLO":
        a = payload.get("agent") or {}
        rt = a.get("runtime") or {}
        version = a.get('version') or '?'
        ok = extension_version_compatible(str(version))
        parts.append(f"Flow Agent online Â· v{version} Â· {'COMPATIBLE' if ok else 'KHÃ”NG TÆ¯Æ NG THÃCH'}")
        if not ok:
            parts.append(f"cáº§n >= {MIN_EXTENSION_VERSION}")
        if a.get("extension_id"):
            parts.append(str(a.get("extension_id")))
        if rt.get("progressLabel"):
            label=str(rt.get("progressLabel"))
            if label.strip().lower() in {"Ä‘Ã£ dá»«ng","stopped","idle","chÆ°a cháº¡y"} and not rt.get("serverJobId"):
                label="IDLE Â· Sáº´N SÃ€NG"
            parts.append(label)
        return " Â· ".join(parts)
    if et == "AGENT_CONNECTED":
        return "Flow Agent connected Â· sáºµn sÃ ng nháº­n job"
    if et == "AGENT_DISCONNECTED":
        return "V2.8 internal Flow broker disconnected Â· master sáº½ tá»± reconnect"
    if et == "JOB_DISPATCHED":
        return f"DISPATCH â†’ Flow Â· job={job} Â· agent={payload.get('agentId') or payload.get('agent_id') or '-'}"
    if et == "FLOW_JOB_ACCEPTED":
        return f"Flow Ä‘Ã£ nháº­n job Â· {job}"
    if et == "IMAGE_READY":
        title = payload.get("title") or ""
        media = payload.get("mediaId") or ""
        return f"IMAGE READY Â· job={job} Â· scene={scene or '-'} Â· media={str(media)[:10]} Â· {title}".strip(" Â·")
    if et == "FACTORY_RESUME_EXISTING_MEDIA":
        return str(payload.get("message") or f"RESUME EXISTING Â· job={job}")
    if et == "SCHEDULER_RESUME_OLD_JOB":
        return str(payload.get("message") or f"RESUME OLD Â· job={job}")
    if et == "FACTORY_RESUME_EXISTING_FAILED":
        return str(payload.get("message") or f"RESUME FAILED Â· job={job}")
    if et == "IMAGE_FILE_READY":
        path = payload.get("localPath") or payload.get("path") or ""
        media = payload.get("mediaId") or ""
        return f"IMAGE LOCAL Â· job={job} Â· scene={scene or '-'} Â· media={str(media)[:10]} Â· {Path(str(path)).name if path else 'file ready'}"
    if et in {"IMAGE_DOWNLOAD_RECOVERY_START","IMAGE_DOWNLOAD_RECOVERY_PROGRESS","IMAGE_DOWNLOAD_RECOVERY_OK","IMAGE_DOWNLOAD_RECOVERY_PARTIAL"}:
        return str(payload.get("message") or et)
    if et == "VIDEO_FILE_READY":
        path = payload.get("localPath") or payload.get("path") or ""
        return f"VIDEO READY Â· job={job} Â· scene={scene or '-'} Â· {Path(str(path)).name if path else 'file ready'}"
    if et == "FLOW_JOB_RESULT":
        ok = bool(payload.get("ok"))
        result = payload.get("result") or {}
        jobs = result.get("jobs") if isinstance(result, dict) else None
        count = len(jobs) if isinstance(jobs, dict) else payload.get("sceneCount")
        return f"FLOW DONE Â· job={job} Â· {'OK' if ok else 'FAIL'}" + (f" Â· scenes={count}" if count is not None else "")
    if et == "FACTORY_RENDER_STARTED":
        return f"RENDER START Â· job={job} Â· Ä‘ang ghÃ©p final MP4"
    if et == "FACTORY_VIDEO_READY":
        qc = payload.get("qc") or {}
        path = payload.get("localPath") or ""
        return f"FINAL READY Â· job={job} Â· QC={qc.get('score','-')} {'PASS' if qc.get('passed') else 'FAIL'} Â· {Path(str(path)).name if path else ''}".strip(" Â·")
    if et == "AUTO_PUBLISH_QUEUED":
        return f"FACEBOOK QUEUE Â· job={job} Â· {'DRY RUN' if payload.get('dryRun') else 'PUBLISH THáº¬T'} Â· page={payload.get('pageId') or '-'}"
    if et in {"FACTORY_RENDER_FAILED","VIDEO_RENDER_FAILED","FLOW_JOB_REJECTED","FLOW_JOB_FAILED"}:
        return f"Lá»–I Â· job={job or '-'} Â· {payload.get('error') or payload.get('message') or ''}"
    if et == "AGENT_EVENT":
        msg = payload.get("message") or {}
        mt = str(msg.get("type") or "event")
        if mt == "FLOW_RUNTIME":
            rt = msg.get("runtime") or {}
            pct = rt.get("progressPercent")
            label = rt.get("progressLabel") or "Flow Ä‘ang cháº¡y"
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
            return " Â· ".join(x for x in seg if x)
        if mt == "FLOW_LOG":
            level = msg.get("level") or "info"
            text = msg.get("text") or msg.get("message") or ""
            return f"FLOW {str(level).upper()} Â· {text}"
        return f"Agent event Â· {mt} Â· {msg.get('text') or msg.get('message') or ''}".strip(" Â·")
    if model:
        parts.append(f"model={model}")
    if scene:
        parts.append(f"scene={scene}")
    return et + (f" Â· job={job}" if job else "") + ((" Â· " + " Â· ".join(parts)) if parts else "")


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


async def ui_broadcast(event: dict[str, Any]) -> None:
    persist_event_log(event)
    dead: list[WebSocket] = []
    payload = dumps({"ts": server_now_iso(), **event})
    for ws in list(UI_CLIENTS):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        UI_CLIENTS.discard(ws)


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
        raise HTTPException(400, "Job pháº£i cÃ³ Ã­t nháº¥t 1 scene")
    job_id = f"flow_{server_stamp()}_{uuid.uuid4().hex[:8]}"
    prompt = str(scenes[0].get("imagePrompt") or scenes[0].get("videoPrompt") or "")
    now = utcnow()
    with conn() as c:
        c.execute(
            "INSERT INTO flow_jobs(id,kind,status,prompt,flow_json,scenes_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, kind, "queued", prompt, dumps(flow), dumps(scenes), now, now),
        )
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

LEGACY_DEFAULT_OUTFITS = [
    "fitted sleeveless cooling crop top with high-waisted short gym shorts",
    "lightweight fitted camisole-style top with short sculpting shorts",
    "figure-flattering athletic crop top with high-waisted mini shorts",
    "cool breathable ribbed top with short sporty skirt and safety shorts",
    "sleek halter-style fitted top with short lounge shorts",
    "summer fitted square-neck top with short tailored shorts",
    "body-hugging sporty two-piece set with short bottoms, fully opaque",
    "glamorous fitted off-shoulder top with short skort, fully clothed and opaque",
]

DEFAULT_OUTFITS = [
    "Ã¡o crop top Ä‘en cá»• vuÃ´ng, quáº§n short cáº¡p cao mÃ u kem, cháº¥t liá»‡u mÃ¡t vÃ  opaque",
    "Ã¡o hai dÃ¢y rib mÃ u tráº¯ng ngÃ , quáº§n short denim xanh nháº¡t, phong cÃ¡ch phá»‘ Viá»‡t Nam mÃ¹a hÃ¨",
    "Ã¡o halter Ä‘á» rÆ°á»£u vang, mini skort Ä‘en cÃ³ quáº§n trong kÃ­n Ä‘Ã¡o",
    "Ã¡o sÃ¡t nÃ¡ch há»“ng pháº¥n, quáº§n short be cáº¡p cao dÃ¡ng gá»n",
    "Ã¡o crop top tÃ­m lavender, quáº§n short tráº¯ng, cháº¥t liá»‡u co giÃ£n khÃ´ng xuyÃªn tháº¥u",
    "Ã¡o cá»• vuÃ´ng mÃ u nÃ¢u chocolate, quáº§n short mÃ u kem, phá»¥ kiá»‡n vÃ ng máº£nh",
    "Ã¡o camisole xanh olive nháº¡t, quáº§n short Ä‘en cáº¡p cao, phong cÃ¡ch lifestyle",
    "Ã¡o off-shoulder mÃ u coral, mini skort tráº¯ng kem, fully clothed vÃ  opaque",
    "Ã¡o crop top vÃ ng bÆ¡, quáº§n short nÃ¢u nháº¡t, sneaker tráº¯ng",
    "Ã¡o sÃ¡t nÃ¡ch xÃ¡m than, quáº§n short tráº¯ng, phong cÃ¡ch tá»‘i giáº£n hiá»‡n Ä‘áº¡i",
    "Ã¡o cá»• yáº¿m mÃ u cam Ä‘áº¥t, quáº§n short Ä‘en, cháº¥t liá»‡u mÃ¡t mÃ¹a hÃ¨",
    "Ã¡o crop top navy Ä‘áº­m, quáº§n short be, phong cÃ¡ch thanh lá»‹ch nÄƒng Ä‘á»™ng",
]

OUTFIT_COLOR_PALETTES = [
    "Ä‘en + kem",
    "tráº¯ng ngÃ  + denim xanh nháº¡t",
    "Ä‘á» rÆ°á»£u vang + Ä‘en",
    "há»“ng pháº¥n + be",
    "tÃ­m lavender + tráº¯ng",
    "nÃ¢u chocolate + kem",
    "olive nháº¡t + Ä‘en",
    "coral + tráº¯ng kem",
    "vÃ ng bÆ¡ + nÃ¢u nháº¡t",
    "xÃ¡m than + tráº¯ng",
    "cam Ä‘áº¥t + Ä‘en",
    "navy Ä‘áº­m + be",
]

_RECENT_OUTFIT_COLORS: dict[str, list[str]] = {}

def _choose_outfit_color(profile_id: str) -> str:
    key = str(profile_id or "default")
    recent = _RECENT_OUTFIT_COLORS.setdefault(key, [])
    candidates = [c for c in OUTFIT_COLOR_PALETTES if c not in recent[-4:]] or list(OUTFIT_COLOR_PALETTES)
    picked = random.choice(candidates)
    recent.append(picked)
    if len(recent) > 8:
        del recent[:-8]
    return picked

LEGACY_VI_OUTFITS = [
    "Ã¡o crop top thá»ƒ thao thoÃ¡ng mÃ¡t Ã´m dÃ¡ng vá»›i quáº§n short gym cáº¡p cao",
    "Ã¡o hai dÃ¢y Ã´m dÃ¡ng nháº¹ mÃ¡t vá»›i quáº§n short Ä‘á»‹nh hÃ¬nh",
    "Ã¡o thá»ƒ thao há»“ng Ã´m dÃ¡ng vá»›i quáº§n legging gym",
    "Ä‘áº§m mini Ä‘en tÃ´n dÃ¡ng, váº£i kÃ­n hoÃ n toÃ n vÃ  khÃ´ng xuyÃªn tháº¥u",
    "bá»™ athleisure mÃ u be Ã´m dÃ¡ng vá»›i Ã¡o dÃ i tay dÃ¡ng crop",
]

LEGACY_VI_BACKGROUNDS = [
    "phÃ²ng gym hiá»‡n Ä‘áº¡i cao cáº¥p cÃ³ gÆ°Æ¡ng vÃ  Ã¡nh sÃ¡ng má»m",
    "studio thá»ƒ hÃ¬nh sÃ¡ng, sáº¡ch",
    "phÃ²ng gym khÃ¡ch sáº¡n cao cáº¥p",
    "quÃ¡n cÃ  phÃª phong cÃ¡ch sá»‘ng hiá»‡n Ä‘áº¡i vá»›i cá»­a sá»• lá»›n",
    "cÄƒn há»™ cao cáº¥p ngáº­p Ã¡nh náº¯ng",
    "Ä‘Æ°á»ng phá»‘ ban Ä‘Ãªm vá»›i Ã¡nh sÃ¡ng Ä‘iá»‡n áº£nh thá»±c táº¿",
]

LEGACY_DEFAULT_BACKGROUNDS = [
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

DEFAULT_BACKGROUNDS = [
    "phá»‘ Ä‘i bá»™ Nguyá»…n Huá»‡, Quáº­n 1, TP.HCM, buá»•i chiá»u tá»‘i vá»›i hÃ ng cÃ¢y vÃ  tÃ²a nhÃ  Ä‘Ã´ thá»‹",
    "BÆ°u Ä‘iá»‡n Trung tÃ¢m SÃ i GÃ²n, Quáº­n 1, TP.HCM, Ã¡nh sÃ¡ng ban ngÃ y vÃ  kiáº¿n trÃºc vÃ ng Ä‘áº·c trÆ°ng",
    "cÃ´ng viÃªn Vinhomes Central Park vá»›i Landmark 81 phÃ­a sau, BÃ¬nh Tháº¡nh, TP.HCM",
    "Cáº§u Má»‘ng nhÃ¬n vá» khu trung tÃ¢m vÃ  Bitexco, TP.HCM, golden hour",
    "Há»“ HoÃ n Kiáº¿m gáº§n ThÃ¡p RÃ¹a, HÃ  Ná»™i, buá»•i sÃ¡ng dá»‹u nháº¹",
    "Ä‘Æ°á»ng Thanh NiÃªn bÃªn Há»“ TÃ¢y, HÃ  Ná»™i, hÃ ng cÃ¢y vÃ  máº·t há»“ thoÃ¡ng",
    "khu NhÃ  thá» Lá»›n HÃ  Ná»™i, phá»‘ cá»•, Ã¡nh sÃ¡ng chiá»u",
    "phá»‘ Táº¡ Hiá»‡n, HoÃ n Kiáº¿m, HÃ  Ná»™i, buá»•i tá»‘i cÃ³ Ä‘Ã¨n phá»‘ vÃ  biá»ƒn hiá»‡u",
    "Cáº§u Rá»“ng bÃªn sÃ´ng HÃ n, ÄÃ  Náºµng, buá»•i tá»‘i vá»›i Ã¡nh Ä‘Ã¨n thÃ nh phá»‘",
    "bÃ£i biá»ƒn Má»¹ KhÃª, ÄÃ  Náºµng, sÃ¡ng sá»›m hoáº·c golden hour",
    "phá»‘ cá»• Há»™i An, Quáº£ng Nam, Ä‘Ã¨n lá»“ng vÃ ng vÃ  máº·t tiá»n nhÃ  cá»•",
    "Quáº£ng trÆ°á»ng LÃ¢m ViÃªn, ÄÃ  Láº¡t, LÃ¢m Äá»“ng, trá»i mÃ¡t vÃ  kiáº¿n trÃºc ná»¥ hoa atiso",
    "Ä‘Æ°á»ng Tráº§n PhÃº ven biá»ƒn Nha Trang, KhÃ¡nh HÃ²a, hÃ ng dá»«a vÃ  biá»ƒn xanh",
    "Báº¿n Ninh Kiá»u, Cáº§n ThÆ¡, chiá»u tá»‘i bÃªn sÃ´ng Háº­u",
    "khu vá»±c Äáº¡i Ná»™i Huáº¿, thÃ nh phá»‘ Huáº¿, kiáº¿n trÃºc cung Ä‘Ã¬nh vÃ  tÆ°á»ng Ä‘á»",
    "khu phá»‘ ven biá»ƒn Háº¡ Long nhÃ¬n ra vá»‹nh, Quáº£ng Ninh, hoÃ ng hÃ´n",
    "má»™t quÃ¡n cÃ  phÃª sÃ¢n vÆ°á»n kiá»ƒu Viá»‡t táº¡i Tháº£o Äiá»n, TP.Thá»§ Äá»©c, Ã¡nh sÃ¡ng tá»± nhiÃªn",
    "ban cÃ´ng cÄƒn há»™ hiá»‡n Ä‘áº¡i nhÃ¬n ra skyline TP.HCM, phong cÃ¡ch lifestyle Viá»‡t Nam",
]

LEGACY_VI_POSES = [
    "Ä‘á»©ng tá»± nhiÃªn vÃ  nhÃ¬n vá» phÃ­a mÃ¡y quay",
    "tÆ° tháº¿ selfie trÆ°á»›c gÆ°Æ¡ng tá»± nhiÃªn",
    "Ä‘i vá» phÃ­a mÃ¡y quay vá»›i dÃ¡ng tá»± tin thoáº£i mÃ¡i",
    "chá»‰nh tÃ³c tá»± nhiÃªn",
    "tÆ° tháº¿ nghiÃªng ba pháº§n tÆ° thÆ° giÃ£n",
    "Ä‘i ngang qua mÃ¡y quay rá»“i ngoÃ¡i nhÃ¬n tá»± nhiÃªn",
]

LEGACY_DEFAULT_POSES = [
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

DEFAULT_POSES = [
    "Ä‘á»©ng tá»± nhiÃªn nhÃ¬n vá» mÃ¡y quay, dÃ¡ng thoáº£i mÃ¡i",
    "Ä‘i bá»™ cháº­m trÃªn phá»‘ rá»“i nhÃ¬n sang mÃ¡y quay",
    "tá»±a nháº¹ lan can hoáº·c bá» tÆ°á»ng, gÃ³c ba pháº§n tÆ°",
    "chá»‰nh tÃ³c má»™t láº§n rá»“i má»‰m cÆ°á»i nháº¹",
    "cáº§m ly cÃ  phÃª mang Ä‘i vÃ  bÆ°á»›c cháº­m",
    "ngá»“i trÃªn gháº¿ cÃ´ng viÃªn vá»›i tÆ° tháº¿ thÆ° giÃ£n",
    "selfie tá»± nhiÃªn á»Ÿ khÃ´ng gian lifestyle, khÃ´ng pháº£i phÃ²ng gym",
    "Ä‘i ngang qua mÃ¡y quay rá»“i ngoÃ¡i nhÃ¬n má»™t láº§n",
    "Ä‘á»©ng cáº¡nh hÃ ng cÃ¢y hoáº·c máº·t há»“, xoay nháº¹ vai vá» mÃ¡y quay",
    "bÆ°á»›c xuá»‘ng báº­c thá»m hoáº·c vá»‰a hÃ¨ vá»›i chuyá»ƒn Ä‘á»™ng tá»± nhiÃªn",
]

def _migrate_legacy_content_defaults(c: sqlite3.Connection) -> None:
    """One-time-safe migration: only replace values that are exactly the old built-in defaults."""
    rows = c.execute("SELECT id,theme,outfit_prompts_json,backgrounds_json,poses_json FROM page_profiles").fetchall()
    now = utcnow()
    for row in rows:
        d = dict(row)
        updates = {}
        try:
            outfits = loads(d.get("outfit_prompts_json"), []) or []
            if outfits == LEGACY_DEFAULT_OUTFITS or outfits == LEGACY_VI_OUTFITS:
                updates["outfit_prompts_json"] = dumps(DEFAULT_OUTFITS)
        except Exception:
            pass
        try:
            bgs = loads(d.get("backgrounds_json"), []) or []
            if bgs == LEGACY_DEFAULT_BACKGROUNDS or bgs == LEGACY_VI_BACKGROUNDS:
                updates["backgrounds_json"] = dumps(DEFAULT_BACKGROUNDS)
        except Exception:
            pass
        try:
            poses = loads(d.get("poses_json"), []) or []
            if poses == LEGACY_DEFAULT_POSES or poses == LEGACY_VI_POSES:
                updates["poses_json"] = dumps(DEFAULT_POSES)
        except Exception:
            pass
        if str(d.get("theme") or "").strip() in {"adult glamour fitness lifestyle", "adult fitness lifestyle"}:
            updates["theme"] = "adult glamour lifestyle in Vietnam"
        if updates:
            updates["updated_at"] = now
            sets = ",".join(f"{k}=?" for k in updates)
            c.execute(f"UPDATE page_profiles SET {sets} WHERE id=?", (*updates.values(), d["id"]))


def apply_vietnam_lifestyle_preset(profile_id: str) -> dict[str, Any]:
    with conn() as c:
        row = c.execute("SELECT id FROM page_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            raise KeyError(profile_id)
        c.execute(
            "UPDATE page_profiles SET theme=?,outfit_prompts_json=?,backgrounds_json=?,poses_json=?,updated_at=? WHERE id=?",
            ("adult glamour lifestyle in Vietnam", dumps(DEFAULT_OUTFITS), dumps(DEFAULT_BACKGROUNDS), dumps(DEFAULT_POSES), utcnow(), profile_id),
        )
    return get_page_profile(profile_id)



SCENE_MODE_ALIASES = {
    "GYM": "GYM", "FITNESS": "GYM", "PHONG_GYM": "GYM",
    "BEACH": "BEACH", "BIEN": "BEACH",
    "VIETNAM": "VIETNAM", "VN": "VIETNAM", "CITY": "VIETNAM",
    "RANDOM": "RANDOM", "AUTO": "RANDOM",
    "MIX": "MIX", "CUSTOM_MIX": "MIX",
    "CUSTOM": "CUSTOM",
}

GYM_BACKGROUNDS = [
    "phÃ²ng gym cao cáº¥p táº¡i TP.HCM vá»›i cá»­a kÃ­nh lá»›n, rack táº¡ tay, gÆ°Æ¡ng lá»›n vÃ  Ã¡nh sÃ¡ng ban ngÃ y",
    "phÃ²ng gym chung cÆ° hiá»‡n Ä‘áº¡i á»Ÿ Tháº£o Äiá»n, TP.HCM, khu dumbbell sáº¡ch, gÆ°Æ¡ng lá»›n, skyline má» ngoÃ i cá»­a kÃ­nh",
    "fitness studio cao cáº¥p á»Ÿ Quáº­n 1, TP.HCM, mÃ¡y táº­p hiá»‡n Ä‘áº¡i, Ã¡nh sÃ¡ng tráº¯ng má»m vÃ  sÃ n cao su tá»‘i mÃ u",
    "phÃ²ng gym khÃ¡ch sáº¡n cao cáº¥p táº¡i ÄÃ  Náºµng, cá»­a kÃ­nh nhÃ¬n thÃ nh phá»‘, khu táº¡ tay vÃ  gháº¿ táº­p",
    "phÃ²ng gym boutique gáº§n Há»“ TÃ¢y, HÃ  Ná»™i, gÆ°Æ¡ng lá»›n, rack táº¡, Ã¡nh sÃ¡ng tá»± nhiÃªn dá»‹u",
    "phÃ²ng gym hiá»‡n Ä‘áº¡i táº¡i Nha Trang, khu free-weight thoÃ¡ng, cá»­a kÃ­nh lá»›n vÃ  Ã¡nh sÃ¡ng ban ngÃ y",
    "khu dumbbell trong phÃ²ng gym cao cáº¥p, táº¡ xáº¿p gá»n phÃ­a sau, gÆ°Æ¡ng pháº£n chiáº¿u tá»± nhiÃªn, khÃ´ng cÃ³ phá»‘ ngoÃ i trá»i",
    "khu mÃ¡y cable vÃ  gháº¿ táº­p trong phÃ²ng gym hiá»‡n Ä‘áº¡i, Ã¡nh sÃ¡ng tráº§n sáº¡ch, khÃ´ng gian fitness thá»±c táº¿",
    "gÃ³c stretching trong phÃ²ng gym premium, tháº£m táº­p, gÆ°Æ¡ng lá»›n, rack táº¡ phÃ­a xa",
    "khu squat rack vÃ  free-weight trong phÃ²ng gym cao cáº¥p, Ã¡nh sÃ¡ng Ä‘iá»‡n áº£nh thá»±c táº¿ nhÆ°ng váº«n sÃ¡ng rÃµ",
]

GYM_POSES = [
    "Ä‘á»©ng toÃ n thÃ¢n trong khu táº¡ tay, má»—i tay cáº§m má»™t dumbbell, nhÃ¬n mÃ¡y quay tá»± nhiÃªn",
    "Ä‘á»©ng gÃ³c ba pháº§n tÆ° cáº¡nh rack táº¡, má»™t tay cáº§m dumbbell, tay kia tháº£ lá»ng",
    "ngá»“i trÃªn gháº¿ táº­p, cáº§m hai dumbbell á»Ÿ hai bÃªn, tÆ° tháº¿ nghá»‰ giá»¯a hiá»‡p",
    "thá»±c hiá»‡n dumbbell curl nháº¹ vá»›i khuá»·u tay giá»¯ gáº§n thÃ¢n, biá»ƒu cáº£m tá»± nhiÃªn",
    "Ä‘á»©ng cáº¡nh gÆ°Æ¡ng gym, cáº§m bÃ¬nh nÆ°á»›c vÃ  má»™t dumbbell, tÆ° tháº¿ sau khi táº­p",
    "Ä‘á»©ng chÃ¢n rá»™ng vá»«a pháº£i, cáº§m hai dumbbell sÃ¡t Ä‘Ã¹i, vai tháº£ lá»ng",
    "tá»±a nháº¹ vÃ o gháº¿ táº­p, má»™t chÃ¢n bÆ°á»›c trÆ°á»›c, giá»¯ dumbbell tá»± nhiÃªn",
    "Ä‘á»©ng cáº¡nh cable machine, chá»‰nh tÃ³c má»™t láº§n rá»“i nhÃ¬n láº¡i mÃ¡y quay",
    "ngá»“i gháº¿ táº­p vÃ  nÃ¢ng má»™t dumbbell nháº¹ báº±ng tay pháº£i, tÆ° tháº¿ kiá»ƒm soÃ¡t",
    "Ä‘á»©ng trÆ°á»›c rack táº¡ vá»›i gÃ³c mÃ¡y tháº¥p nháº¹, body fitness rÃµ nhÆ°ng giáº£i pháº«u tá»± nhiÃªn",
]

GYM_OUTFITS = [
    "Ã¡o tank top gym Ã´m dÃ¡ng mÃ u Ä‘en + quáº§n short gym cáº¡p cao mÃ u tráº¯ng kem, váº£i thá»ƒ thao opaque",
    "Ã¡o halter gym Ã´m dÃ¡ng mÃ u xanh baby + quáº§n short gym tráº¯ng cáº¡p cao, váº£i co giÃ£n opaque",
    "Ã¡o crop top thá»ƒ thao mÃ u tráº¯ng + quáº§n short gym mÃ u be sÃ¡ng, phong cÃ¡ch fitness ná»¯ tÃ­nh",
    "Ã¡o tank top rib mÃ u há»“ng pastel + quáº§n short gym mÃ u xÃ¡m sÃ¡ng, cáº¡p cao",
    "Ã¡o gym sÃ¡t nÃ¡ch mÃ u tÃ­m lavender + quáº§n short tráº¯ng, cháº¥t liá»‡u thá»ƒ thao khÃ´ng xuyÃªn tháº¥u",
    "Ã¡o crop top gym mÃ u navy + quáº§n short be, form Ã´m thá»ƒ thao",
    "Ã¡o tank gym mÃ u Ä‘á» rÆ°á»£u vang + quáº§n short Ä‘en cáº¡p cao, phong cÃ¡ch energetic",
    "Ã¡o gym Ã´m dÃ¡ng mÃ u olive nháº¡t + quáº§n short tráº¯ng kem, outfit fitness hiá»‡n Ä‘áº¡i",
    "Ã¡o tank top mÃ u xÃ¡m than + quáº§n short gym tráº¯ng, phá»¥ kiá»‡n smartwatch tá»‘i giáº£n",
    "Ã¡o crop top gym mÃ u coral + quáº§n short kem, outfit mÃ¡t máº» nhÆ°ng kÃ­n vÃ  opaque",
    "Ã¡o tank gym mÃ u vÃ ng bÆ¡ + quáº§n short nÃ¢u sá»¯a, phong cÃ¡ch fitness lifestyle",
    "Ã¡o gym Ã´m dÃ¡ng mÃ u nÃ¢u chocolate + quáº§n short kem, cáº¡p cao vÃ  sáº¡ch sáº½",
]

GYM_VIDEO_MOTIONS = [
    "Perform two controlled dumbbell curls with natural arm mechanics, subtle breathing after exercise, small shoulder motion and realistic hair movement. Camera makes a short energetic push-in.",
    "Hold dumbbells beside the thighs, inhale naturally, then raise both forearms into one controlled curl and lower them. Slight body sway from breathing, no exaggerated motion.",
    "Shift weight naturally, lift one dumbbell once, exhale after the repetition and give a brief natural glance toward camera. Keep realistic gym posture.",
    "Begin resting with dumbbells down, take one deeper breath, tighten grip, then perform a short controlled repetition. Hair and clothing move subtly.",
    "Sit on the workout bench, adjust posture, raise one dumbbell in a controlled curl, then lower it while breathing naturally. Camera arcs slightly.",
    "Take one small step beside the dumbbell rack, reposition the weights, breathe after the set, then look toward camera briefly. Natural fitness movement.",
]

BEACH_BACKGROUNDS = [
    "bÃ£i biá»ƒn Má»¹ KhÃª, ÄÃ  Náºµng, sÃ¡ng sá»›m, cÃ¡t sáº¡ch, biá»ƒn xanh vÃ  hÃ ng dá»«a xa phÃ­a sau",
    "bÃ£i biá»ƒn Nha Trang, KhÃ¡nh HÃ²a, golden hour, lá»‘i Ä‘i ven biá»ƒn vÃ  máº·t nÆ°á»›c sÃ¡ng",
    "bÃ£i biá»ƒn PhÃº Quá»‘c, KiÃªn Giang, hoÃ ng hÃ´n nháº¹, resort nhiá»‡t Ä‘á»›i á»Ÿ xa",
    "bÃ£i biá»ƒn BÃ£i Sau, VÅ©ng TÃ u, sÃ¡ng sá»›m, bá» biá»ƒn rá»™ng vÃ  Ã¡nh sÃ¡ng tá»± nhiÃªn",
    "lá»‘i Ä‘i resort ven biá»ƒn ÄÃ  Náºµng, cÃ¢y cá» vÃ  Ã¡nh sÃ¡ng ban ngÃ y",
    "ban cÃ´ng resort PhÃº Quá»‘c nhÃ¬n ra biá»ƒn, Ã¡nh sÃ¡ng vÃ ng cuá»‘i chiá»u",
]
BEACH_POSES = [
    "Ä‘i bá»™ cháº­m dá»c bá» biá»ƒn rá»“i nhÃ¬n sang mÃ¡y quay",
    "Ä‘á»©ng gÃ³c ba pháº§n tÆ° gáº§n hÃ ng dá»«a, tÃ³c chuyá»ƒn Ä‘á»™ng nháº¹ theo giÃ³",
    "tá»±a nháº¹ lan can lá»‘i Ä‘i ven biá»ƒn vÃ  nhÃ¬n ra máº·t nÆ°á»›c",
    "cáº§m chai nÆ°á»›c, bÆ°á»›c cháº­m trÃªn lá»‘i Ä‘i resort",
    "Ä‘á»©ng toÃ n thÃ¢n trÃªn cÃ¡t khÃ´, xoay nháº¹ vai vá» phÃ­a mÃ¡y quay",
]
BEACH_OUTFITS = [
    "Ã¡o tank top Ã´m dÃ¡ng mÃ u tráº¯ng + quáº§n short be sÃ¡ng, phong cÃ¡ch biá»ƒn nÄƒng Ä‘á»™ng vÃ  opaque",
    "Ã¡o crop top xanh baby + quáº§n short tráº¯ng, outfit hÃ¨ kÃ­n Ä‘Ã¡o vÃ  khÃ´ng xuyÃªn tháº¥u",
    "Ã¡o halter pastel + quáº§n short cáº¡p cao mÃ u kem, phong cÃ¡ch resort nÄƒng Ä‘á»™ng",
    "Ã¡o sÃ¡t nÃ¡ch coral + quáº§n short tráº¯ng, outfit mÃ¹a hÃ¨ nháº¹ vÃ  opaque",
]

def normalize_scene_mode(value: Any) -> str:
    raw = str(value or "GYM").strip().upper().replace("-", "_").replace(" ", "_")
    return SCENE_MODE_ALIASES.get(raw, "GYM")

def normalize_scene_mix(value: Any) -> list[str]:
    if isinstance(value, list):
        raw=value
    else:
        raw=re.split(r"[,;|]+", str(value or ""))
    out=[]
    for item in raw:
        mode=normalize_scene_mode(item)
        if mode in {"GYM","BEACH","VIETNAM","CUSTOM"} and mode not in out:
            out.append(mode)
    return out[:4]

def _scene_mode_cfg(profile: dict[str, Any]) -> tuple[str, list[str]]:
    cfg=dict(profile.get("scheduler_config") or {})
    mode=normalize_scene_mode(cfg.get("scene_mode") or "GYM")
    mix=normalize_scene_mix(cfg.get("scene_mix") or [])
    if mode=="MIX" and not mix:
        mix=["GYM","BEACH"]
    return mode,mix

def _scene_mode_label(mode: str, mix: list[str] | None=None) -> str:
    if mode=="MIX":
        return "MIX " + "+".join(mix or ["GYM","BEACH"])
    return mode

def _resolve_scene_pools(profile: dict[str, Any]) -> tuple[str,list[str],list[str],list[str],list[str],str]:
    mode,mix=_scene_mode_cfg(profile)
    custom_bgs=_clean_list(profile.get("backgrounds"), DEFAULT_BACKGROUNDS)
    custom_poses=_clean_list(profile.get("poses"), DEFAULT_POSES)
    profile_outfits=_clean_list(profile.get("outfit_prompts"), DEFAULT_OUTFITS)

    def pools(m: str):
        if m=="GYM":
            return GYM_BACKGROUNDS, GYM_POSES, GYM_OUTFITS, GYM_VIDEO_MOTIONS
        if m=="BEACH":
            return BEACH_BACKGROUNDS, BEACH_POSES, BEACH_OUTFITS, VIDEO_MOTIONS
        if m=="VIETNAM":
            return DEFAULT_BACKGROUNDS, DEFAULT_POSES, profile_outfits, VIDEO_MOTIONS
        if m=="CUSTOM":
            return custom_bgs, custom_poses, profile_outfits, VIDEO_MOTIONS
        return [],[],[],[]

    if mode=="RANDOM":
        chosen=random.choice(["GYM","BEACH","VIETNAM"])
        b,p,o,m=pools(chosen)
        return mode,list(b),list(p),list(o),list(m),chosen
    if mode=="MIX":
        modes=mix or ["GYM","BEACH"]
        bgs=[];poses=[];outfits=[];motions=[]
        for m in modes:
            b,p,o,mo=pools(m)
            bgs.extend(b);poses.extend(p);outfits.extend(o);motions.extend(mo)
        return mode,bgs or list(GYM_BACKGROUNDS),poses or list(GYM_POSES),outfits or list(GYM_OUTFITS),motions or list(GYM_VIDEO_MOTIONS),"+".join(modes)
    b,p,o,m=pools(mode)
    return mode,list(b),list(p),list(o),list(m),mode

def _scene_lock_clause(resolved_scene: str) -> str:
    if resolved_scene=="GYM":
        return (
            "STRICT ENVIRONMENT LOCK: every scene must remain INSIDE a modern premium gym/fitness facility. "
            "Show dumbbell racks, benches, mirrors, cable machines or fitness equipment. "
            "DO NOT move the subject to streets, bridges, cafes, rooftops, city landmarks, parks or beaches."
        )
    if resolved_scene=="BEACH":
        return (
            "STRICT ENVIRONMENT LOCK: every scene must remain at a beach/coastal resort setting. "
            "DO NOT move the subject into a gym, city street, bridge or indoor cafe."
        )
    if resolved_scene=="VIETNAM":
        return "Environment category: Vietnamese urban/lifestyle locations; use the named Vietnamese place for each scene."
    return f"Environment category: {resolved_scene}."

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
    try:
        d["persona_source_exists"] = bool(d.get("persona_path") and Path(str(d.get("persona_path"))).exists())
    except Exception:
        d["persona_source_exists"] = False
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
    if mode not in {"AUTO", "IMAGE_BEAT", "IMAGE_MIX", "IMAGE_TO_VIDEO"}:
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
            raise ValueError("KhÃ´ng tháº¥y Page Profile")
        d = dict(row)
        persona_path = str(d.get("persona_path") or d.get("persona_original_path") or d.get("persona_master_path") or "").strip()
        if not persona_path:
            raise ValueError("Page Profile chÆ°a cÃ³ áº£nh FRONT/original Ä‘á»ƒ rebuild")
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
        raise ValueError(f"GÃ³c persona khÃ´ng há»£p lá»‡: {angle}")
    return specs[angle]


def build_persona_angle_scene(profile: dict[str, Any], angle: str) -> dict[str, Any]:
    angle = str(angle or "").strip().lower()
    front = str(profile.get("persona_master_path") or profile.get("persona_path") or "").strip()
    if not front or not Path(front).exists():
        raise ValueError("Persona FRONT master chÆ°a sáºµn sÃ ng")
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
        raise ValueError(f"GÃ³c persona khÃ´ng há»£p lá»‡: {angle}")
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
        raise ValueError("GÃ³c khÃ´ng há»£p lá»‡")
    if find_active_persona_angle_job(profile_id, angle):
        raise RuntimeError(f"GÃ³c {angle} Ä‘ang generate; chá» job xong rá»“i xÃ³a")
    profile=get_page_profile(profile_id)
    if not profile:
        raise ValueError("KhÃ´ng tháº¥y Page Profile")
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
        raise ValueError("GÃ³c khÃ´ng há»£p lá»‡")
    profile=get_page_profile(profile_id)
    if not profile:
        raise ValueError("KhÃ´ng tháº¥y Page Profile")
    if enabled and not profile.get(f"persona_{angle}_master_path"):
        raise ValueError(f"GÃ³c {angle} chÆ°a cÃ³ áº£nh Ä‘á»ƒ dÃ¹ng")
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
    raw = {str((x or {}).get("id") or "").strip(): (x or {}) for x in rows}
    statuses = get_ai_model_status_map()
    out=[]
    for spec in ROUTER9_ALLOWED_MODELS:
        mid=spec["id"]
        item=raw.get(mid)
        if not item:
            continue
        st=statuses.get(mid) or {}
        if int(st.get("hard_disabled") or 0):
            continue
        out.append({
            "id":mid,"label":spec["label"],"family":spec["family"],
            "owned_by":str(item.get("owned_by") or ""),"status":"available",
            "disabled":False,"hard_disabled":False,"latency_ms":None,"error":None,
            "tested_at":None,"block_reason":None,
        })
    return out

def router9_model_policy_stats() -> dict[str, int]:
    raw = _router9_fetch_raw_models() if router9_enabled() else []
    github = sum(1 for x in raw if _is_github_model(str((x or {}).get("id") or ""), str((x or {}).get("owned_by") or "")))
    with conn() as c:
        hard = int(c.execute("SELECT COUNT(*) FROM ai_model_status WHERE COALESCE(hard_disabled,0)=1 AND block_reason<>'github_provider_blocked'").fetchone()[0])
        soft = int(c.execute("SELECT COUNT(*) FROM ai_model_status WHERE COALESCE(disabled,0)=1 AND COALESCE(hard_disabled,0)=0").fetchone()[0])
    return {"blocked_github": github, "hard_disabled": hard, "soft_disabled": soft}


def router9_usable_models() -> list[dict[str, Any]]:
    # V2.14.29: no manual model-test gate; GET /models availability is enough.
    return router9_models()

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
        raise ValueError("Thiáº¿u model_id")
    if _is_github_model(model_id):
        _hard_block_model(model_id, "github_provider_blocked")
        return {"ok": False, "model_id": model_id, "status": "blocked", "hard_disabled": True, "error": "GitHub provider bá»‹ cháº·n theo policy"}
    family = _model_family(model_id)
    existing = get_ai_model_status_map().get(model_id) or {}
    if int(existing.get("hard_disabled") or 0):
        return {"ok": False, "model_id": model_id, "status": "blocked", "hard_disabled": True, "error": existing.get("block_reason") or "Model bá»‹ permanent block"}
    # Retest thá»§ cÃ´ng chá»‰ má»Ÿ láº¡i SOFT-disabled model.
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
            raise RuntimeError(f"KhÃ´ng cÃ³ content sau khi parse JSON/SSE: {data}")
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


def router9_chat_json(*, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> dict[str, Any]:
    if not router9_enabled():
        raise RuntimeError("ROUTER9_API_KEY chÆ°a cáº¥u hÃ¬nh")
    model = str(model or "").strip()
    if model not in ROUTER9_ALLOWED_IDS:
        model = ""
    if not model:
        models = router9_usable_models()
        model = models[0]["id"] if models else ""
    if not model:
        raise RuntimeError("9router khÃ´ng tráº£ model nÃ o tá»« GET /v1/models")
    url = ROUTER9_BASE_URL + "/chat/completions"
    headers = {"Authorization": f"Bearer {ROUTER9_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    last_data: dict[str, Any] = {}
    for with_json_mode in (True, False):
        body = dict(payload)
        if with_json_mode:
            body["response_format"] = {"type": "json_object"}
        body["stream"] = False
        body.setdefault("max_tokens", 1400)
        r = requests.post(url, headers=headers, json=body, timeout=(20, ROUTER9_TIMEOUT))
        content, parsed = _router9_response_content(r)
        last_data = parsed if isinstance(parsed, dict) else {"data": parsed}
        if r.ok:
            if not content:
                raise RuntimeError(f"9router rá»—ng sau JSON/SSE parse: {last_data}")
            return json.loads(_extract_json_text(content))
        if r.status_code not in {400, 404, 422} or not with_json_mode:
            raise RuntimeError(f"9router HTTP {r.status_code}: {last_data}")
    raise RuntimeError(f"9router lá»—i: {last_data}")


def fallback_content_plan(profile: dict[str, Any], *, theme: str, body: str, sexy_level: int, outfit_pool: list[str], background_pool: list[str], pose_pool: list[str], final_index: int, mode: str) -> dict[str, Any]:
    outfit = random.choice(outfit_pool)
    bg = random.choice(background_pool)
    pose = random.choice(pose_pool)
    title_hint = str(profile.get("title_hint") or profile.get("theme") or profile.get("name") or "")
    sexy_word = "cuá»‘n hÃºt" if sexy_level >= 70 else "nÄƒng Ä‘á»™ng" if sexy_level >= 45 else "nháº¹ nhÃ ng"
    title = f"{title_hint or profile.get('name')}: {sexy_word} má»—i ngÃ y"[:98]
    hashtags = ["#reels", "#viral", "#fyp", "#vietnam", "#lifestyle", "#fashion"]
    caption = f"{sexy_word.title()} vibe hÃ´m nay âœ¨ {theme}. Outfit: {outfit}. {profile.get('name')} giá»¯ Ä‘Ãºng gÆ°Æ¡ng máº·t, Ä‘á»•i outfit vÃ  background Ä‘á»ƒ video Ä‘a dáº¡ng hÆ¡n. {' '.join(hashtags)}"
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
    color_direction = _choose_outfit_color(str(profile.get("id") or profile.get("name") or "default"))
    if outfit_pool == DEFAULT_OUTFITS and color_direction in OUTFIT_COLOR_PALETTES:
        fallback["outfit"] = DEFAULT_OUTFITS[OUTFIT_COLOR_PALETTES.index(color_direction)]
    fallback["color_direction"] = color_direction
    model = (profile.get("ai_model") or "").strip()
    if not router9_enabled():
        return fallback
    system_prompt = (
        "You plan short-form social content for adult Vietnamese glamour/lifestyle pages. "
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
        f"REQUIRED OUTFIT COLOR FAMILY FOR THIS VIDEO: {color_direction}. Follow this color direction; do not default to blue/teal unless blue/teal is explicitly the selected family.\n"
        "BACKGROUND RULE: prioritize a named, recognizable Vietnamese location from the candidate list. Avoid generic gym/fitness studios unless the Page explicitly asks for gym content. "
        "DESIGN A NEW OUTFIT for this video instead of merely selecting an existing line. Prioritize short bottoms such as short casual shorts, mini skorts, tailored shorts or sporty shorts, and breathable cool tops such as crop tops, sleeveless tops, halter tops, fitted camisole-style tops or square-neck tops. "
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
        # V2.13: built-in Vietnam preset must stay concrete. Do not let the planner
        # drift back to a generic gym/studio or the old teal outfit habit.
        if background_pool == DEFAULT_BACKGROUNDS and plan["background"] not in background_pool:
            plan["background"] = fallback["background"]
        if outfit_pool == DEFAULT_OUTFITS:
            lowered = plan["outfit"].lower()
            if "xanh ngá»c" in lowered or "teal" in lowered or "turquoise" in lowered:
                plan["outfit"] = fallback["outfit"]
        plan["color_direction"] = color_direction
        tags = plan.get("hashtags")
        if not isinstance(tags, list):
            tags = fallback["hashtags"]
        plan["hashtags"] = [str(x).strip() for x in tags if str(x).strip()][:8] or fallback["hashtags"]
        plan["ai_used"] = True
        plan["ai_model"] = model
        plan["color_direction"] = color_direction
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
        return "bold glamorous fitted styling, attractive and figure-flattering, fitted crop or sleeveless top with high-waisted shorts/skort, fully clothed, opaque, non-explicit"
    return "high-glamour figure-hugging adult fashion, clearly flattering bust, waist, hips and legs with fitted crop/halter/square-neck top and high-waisted mini shorts or skort, fully clothed, opaque, non-explicit, no nudity"


def _synth_hard_beat_wav(path: Path, *, bpm: int, seconds: int, seed: int) -> None:
    """Original hard fitness beat: kick, bass, clap/snare, hats and impacts."""
    sr=44100
    seconds=max(8,min(60,int(seconds)))
    total=sr*seconds
    beat=60.0/max(90,min(180,int(bpm)))
    rnd=random.Random(int(seed))
    pcm=array('h')
    bass_pattern=[55.0,55.0,65.406,73.416,49.0,55.0,82.407,73.416]
    two_pi=2.0*math.pi
    for i in range(total):
        t=i/sr
        beat_index=int(t/beat); beat_phase=t-beat_index*beat
        half=beat/2.0; half_index=int(t/half); half_phase=t-half_index*half
        kick=0.0
        if beat_phase<0.18:
            env=math.exp(-beat_phase*22.0); freq=92.0-48.0*min(1.0,beat_phase/0.12)
            kick=math.sin(two_pi*freq*beat_phase)*env*0.95
        snare=0.0
        if beat_index%4 in (1,3) and beat_phase<0.11:
            env=math.exp(-beat_phase*32.0)
            snare=(rnd.random()*2.0-1.0)*env*0.42 + math.sin(two_pi*185.0*beat_phase)*env*0.18
        hat=0.0
        if half_phase<0.035:
            env=math.exp(-half_phase*78.0); hat=(rnd.random()*2.0-1.0)*env*(0.13 if half_index%2 else 0.09)
        note=bass_pattern[(beat_index//2)%len(bass_pattern)]
        side=0.22+0.78*min(1.0,beat_phase/0.12)
        bass=math.sin(two_pi*note*t)*0.24*side + math.sin(two_pi*note*2.0*t)*0.055*side
        impact=0.0; bar_phase=t%(beat*4.0)
        if bar_phase<0.24:
            env=math.exp(-bar_phase*13.0); impact=math.sin(two_pi*38.0*bar_phase)*env*0.32
            if bar_phase<0.05: impact+=(rnd.random()*2.0-1.0)*math.exp(-bar_phase*55.0)*0.10
        sample=max(-1.0,min(1.0,(kick+snare+hat+bass+impact)*0.82))
        wobble=0.018*math.sin(two_pi*0.37*t)
        pcm.append(int(max(-1.0,min(1.0,sample*(1.0+wobble)))*32767))
        pcm.append(int(max(-1.0,min(1.0,sample*(1.0-wobble)))*32767))
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as wf:
        wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(pcm.tobytes())


def _generate_auto_music_track(profile_id: str, *, variant: int=0) -> str:
    dest=_profile_music_dir(profile_id)
    bpms=[136,144,152,160,148]; bpm=bpms[int(variant)%len(bpms)]
    mp3=dest/f"auto_hardbeat_{bpm}_{int(variant)+1}.mp3"
    if mp3.exists() and mp3.stat().st_size>4096:
        return str(mp3.resolve())
    wav=dest/f".auto_hardbeat_{bpm}_{int(variant)+1}.wav"
    seed=int(hashlib.sha256(f"{profile_id}:{variant}:{bpm}".encode()).hexdigest()[:12],16)
    _synth_hard_beat_wav(wav,bpm=bpm,seconds=AUTO_MUSIC_SECONDS,seed=seed)
    if not shutil.which('ffmpeg'):
        raise RuntimeError('AUTO MUSIC cáº§n ffmpeg Ä‘á»ƒ táº¡o MP3')
    _run_cmd(['ffmpeg','-y','-i',str(wav),'-c:a','libmp3lame','-b:a','192k','-af','loudnorm=I=-10:TP=-1.2:LRA=7',str(mp3)],timeout=120)
    wav.unlink(missing_ok=True)
    probe=_probe_audio_file(mp3); sha=hashlib.sha256(mp3.read_bytes()).hexdigest()
    _write_music_meta(mp3,{
        'source_url':'','source_host':'AUTO_HARD_BEAT','profile_id':profile_id,'sha256':sha,
        'size':mp3.stat().st_size,'imported_at':utcnow(),'auto_generated':True,'bpm':bpm,
        'style':'gym_hard_super_shake',**probe,
    })
    return str(mp3.resolve())


def _ensure_auto_music_library(profile_id: str) -> list[str]:
    profile=get_page_profile(profile_id)
    if not profile: return []
    existing=[p for p in _clean_list(profile.get('music_paths')) if Path(p).exists()]
    if existing: return existing
    if not AUTO_MUSIC_ENABLED: return []
    generated=[]; errors=[]
    for variant in range(AUTO_MUSIC_VARIANTS):
        try: generated.append(_generate_auto_music_track(profile_id,variant=variant))
        except Exception as exc: errors.append(str(exc))
    generated=[p for p in generated if Path(p).exists()]
    if generated:
        with conn() as c:
            c.execute('UPDATE page_profiles SET music_paths_json=?,updated_at=? WHERE id=?',(dumps(generated),utcnow(),profile_id))
        persist_event_log({'type':'AUTO_MUSIC_READY','profileId':profile_id,'message':f'AUTO MUSIC READY Â· tá»± táº¡o {len(generated)} HARD BEAT Â· khÃ´ng cáº§n upload MP3.'})
    elif errors:
        persist_event_log({'type':'AUTO_MUSIC_ERROR','profileId':profile_id,'message':'AUTO MUSIC lá»—i Â· '+' | '.join(errors)[:1500]})
    return generated


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
            "sceneVideoPolicy": "PER_SCENE_V2",
            "serverSchemaVersion": "2.14.29",
        }
    }


def build_factory_v2_job(profile: dict[str, Any], req: FactoryV2GenerateRequest, run_id: str, final_index: int, mode: str) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    persona = str(profile.get("persona_master_path") or profile.get("persona_path") or "").strip()
    if not persona:
        raise ValueError(f"Page '{profile['name']}' chÆ°a cÃ³ persona master")
    if not Path(persona).exists():
        raise ValueError(f"Persona master khÃ´ng tá»“n táº¡i: {persona}")

    body = BODY_PRESETS.get(profile.get("body_preset") or "curvy_fit", BODY_PRESETS["curvy_fit"])
    sexy = _sexiness_clause(int(profile.get("sexiness_level") or 60))
    scene_mode, backgrounds, poses, outfit_prompts, scene_motions, resolved_scene = _resolve_scene_pools(profile)
    scene_mix = _scene_mode_cfg(profile)[1]
    scene_lock = _scene_lock_clause(resolved_scene)
    music_candidates=_clean_list(profile.get("music_paths"))
    if not _pick_existing(music_candidates) and AUTO_MUSIC_ENABLED:
        music_candidates=_ensure_auto_music_library(str(profile["id"]))
        if music_candidates: profile["music_paths"]=music_candidates
    music_path=_pick_existing(music_candidates)
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
    motion_order = random.sample(scene_motions, min(len(scene_motions), count)) if len(scene_motions) >= count else [random.choice(scene_motions) for _ in range(count)]
    if mode == "IMAGE_TO_VIDEO":
        motion_scene_ids=set(range(count))
    elif mode == "IMAGE_MIX":
        # 15s/10 áº£nh máº·c Ä‘á»‹nh: khoáº£ng 3 cáº£nh Ä‘Æ°á»£c animate, cÃ²n láº¡i lÃ  áº£nh giáº­t máº¡nh.
        motion_scene_ids=set(random.sample(range(count), min(max(2,int(req.i2v_clip_count)),count)))
    else:
        motion_scene_ids=set()
    meta_base["motionSceneIds"]=[x+1 for x in sorted(motion_scene_ids)]

    for i in range(count):
        outfit_clause = (
            "Keep the clothing faithfully consistent with the uploaded outfit reference."
            if outfit_ref else f"Wardrobe: {selected_outfit}."
        )
        angle_clause = "Use the full multi-angle persona pack when available: front, bust, left, right and back references. Keep hair parting, hair volume, ear visibility, neck, shoulder line and back-of-head appearance consistent across all views."
        location_anchor = (
            f"LOCATION ANCHOR LOCK: make this place visually recognizable from this exact prompt: {bg_order[i]}. "
            "Show 2-4 concrete location markers such as skyline, landmark architecture, street signs, beach/resort features, lanterns, lake/river, bridge, cafe garden or gym equipment as applicable. "
            "Do not use a generic blank background. "
            if int(getattr(req, 'location_anchor_strength', 85) or 0) >= 50 else ""
        )
        image_prompt = (
            f"Photorealistic Vietnamese adult woman, age 21+, East Asian ethnicity, fair natural skin tone, same exact identity, face, hairstyle and recognizable facial features as the person reference. "
            f"{body}. Theme: {theme}. Styling: {sexy}. {outfit_clause} {angle_clause} "
            f"{scene_lock} Scene preset: {_scene_mode_label(scene_mode, scene_mix)}. "
            f"Scene {i+1}: {bg_order[i]}; pose: {pose_order[i]}. "
            f"{location_anchor}"
            "Natural realistic skin texture, attractive social-media photography, full body or three-quarter body, vertical 9:16 composition, "
            "realistic anatomy, no text, no watermark, no nudity, no transparent clothing."
        )
        video_prompt = ""
        if i in motion_scene_ids:
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
            "sceneMode": scene_mode,
            "sceneMix": scene_mix,
            "resolvedScene": resolved_scene,
            "adultOnly": True,
            # V2.14.29 schema: extension must decide VIDEO per scene, not per whole job.
            "makeVideo": bool(i in motion_scene_ids),
            "mixedMotion": bool(i in motion_scene_ids),
            "sceneVideoPolicy": "PER_SCENE_V2",
            "sceneMediaMode": ("IMAGE_VIDEO" if i in motion_scene_ids else "IMAGE_ONLY"),
        }
        scenes.append({
            "sceneId": i + 1,
            "imagePrompt": image_prompt,
            "videoPrompt": video_prompt,
            "inputImages": refs,
            "metadata": metadata,
        })

    if mode in {"IMAGE_TO_VIDEO","IMAGE_MIX"}:
        flow = default_flow_config(
            imageModel=profile.get("image_model") or "Nano Banana 2",
            videoModel=profile.get("video_model") or "Veo 3.1 - Fast",
            imageConcurrency=min(req.image_concurrency, 10),
            videoConcurrency=min(req.video_concurrency, 10),
            submitPolicy="VIDEO_LIGHT",
            # V2.14.29: child clips Æ°u tiÃªn signed-URL streaming; local only as fallback/final output.
            autoDownloadVideo=False,
            aspectRatio="9:16",
            imageOutputs="x1",
            videoDuration=req.i2v_clip_duration,
            videoOutputs="x1",
            videoExtendFactor="x1",
            maxSubmitsPerMinute=min(8, max(2, req.video_concurrency + 2)),
            submitGapMs=850,
            videoTimeoutSec=900,
        )
        kind = "factory_v2_mix" if mode=="IMAGE_MIX" else "factory_v2_i2v"
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


def _recent_factory_modes(profile_id: str, limit: int = 3) -> list[str]:
    """Read recent generated modes without depending on result/meta parsing."""
    if not profile_id:
        return []
    kind_to_mode={
        "factory_v2_beat":"IMAGE_BEAT",
        "factory_v2_mix":"IMAGE_MIX",
        "factory_v2_i2v":"IMAGE_TO_VIDEO",
    }
    with conn() as c:
        rows=c.execute(
            """SELECT f.kind
               FROM content_queue q
               JOIN flow_jobs f ON f.id=q.flow_job_id
               WHERE q.page_profile_id=?
                 AND f.kind IN ('factory_v2_beat','factory_v2_mix','factory_v2_i2v')
               ORDER BY q.created_at DESC
               LIMIT ?""",
            (profile_id,max(1,int(limit))),
        ).fetchall()
    return [kind_to_mode.get(str(r["kind"] or ""),"") for r in rows if kind_to_mode.get(str(r["kind"] or ""))]


def choose_factory_mode(profile: dict[str, Any], requested: str) -> str:
    """AUTO RANDOM with anti-repeat so reserve buffer is not all image-only."""
    requested=str(requested or "AUTO").upper()
    if requested in {"IMAGE_BEAT","IMAGE_MIX","IMAGE_TO_VIDEO"}:
        return requested
    default=str(profile.get("default_video_mode") or "AUTO").upper()
    if default in {"IMAGE_BEAT","IMAGE_MIX","IMAGE_TO_VIDEO"}:
        return default

    profile_id=str(profile.get("id") or "")
    recent=_recent_factory_modes(profile_id,3)

    if recent and recent[0]=="IMAGE_BEAT":
        selected=random.choice(["IMAGE_MIX","IMAGE_TO_VIDEO"])
    elif len(recent)>=2 and recent[0]==recent[1]:
        selected=random.choice([m for m in ("IMAGE_BEAT","IMAGE_MIX","IMAGE_TO_VIDEO") if m!=recent[0]])
    else:
        selected=random.choices(
            ["IMAGE_BEAT","IMAGE_MIX","IMAGE_TO_VIDEO"],
            weights=[25,40,35],
            k=1,
        )[0]

    persist_event_log({
        "type":"AUTO_MODE_SELECTED",
        "profileId":profile_id,
        "message":f"AUTO RANDOM â†’ {selected} Â· recent={recent[:3]} Â· anti-repeat=ON",
    })
    return selected


def create_factory_run(profile_id: str, req: FactoryV2GenerateRequest, jobs: list[str]) -> str:
    run_id = f"factory_{server_stamp()}_{uuid.uuid4().hex[:8]}"
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
    d["active"] = sum(1 for j in statuses if j["status"] in {"queued", "dispatching", "running", "flow_done", "downloading", "rendering", "qc"})
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
    qid = f"cq_{server_stamp()}_{uuid.uuid4().hex[:8]}"
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


REBUILDABLE_SCHEMA_MARKERS = (
    "thiáº¿u videoprompt",
    "missing videoprompt",
    "scene schema",
    "invalid scene schema",
    "imageprompt|videoprompt",
    "mixedmotion",
    "makevideo",
)


def _flow_failure_blob(flow_job_id: str, error: str = "") -> str:
    parts=[str(error or "")]
    job=get_flow_job(flow_job_id) or {}
    parts.append(str(job.get("error") or ""))
    try:
        parts.append(dumps(job.get("result") or {}))
    except Exception:
        pass
    return " ".join(parts).lower()


def _is_rebuildable_schema_failure(flow_job_id: str, error: str = "") -> bool:
    """True only for deterministic old/malformed IMAGE_MIX schema faults.

    Old V2.14.12/13 IMAGE_MIX jobs used a job-wide videoModel while some scenes
    intentionally had blank videoPrompt. v14.5.20 validated every scene as video.
    Those old failed jobs are safe to discard/rebuild after upgrading.
    """
    job=get_flow_job(flow_job_id) or {}
    kind=str(job.get("kind") or "")
    blob=_flow_failure_blob(flow_job_id,error)
    if any(marker in blob for marker in REBUILDABLE_SCHEMA_MARKERS):
        return True
    if kind=="factory_v2_mix":
        scenes=job.get("scenes") or []
        policy=""
        if scenes:
            meta=scenes[0].get("metadata") or {}
            policy=str(meta.get("sceneVideoPolicy") or "")
        # Any failed pre-PER_SCENE_V2 mix job belongs to the known old schema.
        if policy!="PER_SCENE_V2":
            return True
    return False


def _auto_discard_rebuildable_failed(profile_id: str) -> int:
    with conn() as c:
        rows=c.execute(
            "SELECT id,flow_job_id,error FROM content_queue WHERE page_profile_id=? AND status='failed'",
            (profile_id,),
        ).fetchall()
    discard=[]
    for row in rows:
        jid=str(row["flow_job_id"] or "")
        if jid and _is_rebuildable_schema_failure(jid,str(row["error"] or "")):
            discard.append((str(row["id"]),jid,str(row["error"] or "")))
    if not discard:
        return 0
    now=utcnow()
    with conn() as c:
        for qid,jid,err in discard:
            c.execute(
                "UPDATE content_queue SET status='discarded',error=?,updated_at=? WHERE id=? AND status='failed'",
                (f"AUTO REBUILD SCHEMA Â· {err}"[:2000],now,qid),
            )
    for qid,jid,err in discard:
        persist_event_log({
            "type":"SCHEDULER_AUTO_DISCARD_SCHEMA",
            "profileId":profile_id,"jobId":jid,
            "message":f"Tá»± bá» job schema cÅ© {qid} Â· scheduler sáº½ táº¡o bÃ¹ Â· {err}",
        })
    return len(discard)


def _is_flow_ui_breaker_failure(message: str) -> bool:
    text=str(message or '').lower()
    markers=(
        'flow_ui_',
        'asset picker chÆ°a Ä‘Ã³ng',
        'asset picker khÃ´ng má»Ÿ',
        'asset picker Ä‘Ã£ má»Ÿ nhÆ°ng khÃ´ng tháº¥y',
        'khÃ´ng tÃ¬m tháº¥y nÃºt add media',
        'getaddmediapoint lá»—i',
        'setassetsearch lá»—i',
        'trusted-click Ä‘Ãºng mediaid',
        'composer khÃ´ng tÄƒng',
        'asset attach verify',
        'debugger is not attached',
        'not attached to the tab',
    )
    return any(m in text for m in markers)


def _set_flow_ui_breaker(profile_id: str, message: str) -> str:
    profile=get_page_profile(profile_id) or {}
    cfg=dict(profile.get('scheduler_config') or {})
    until=datetime.now(timezone.utc)+timedelta(minutes=FLOW_UI_BREAKER_MINUTES)
    cfg['flow_ui_breaker_until']=until.isoformat(timespec='seconds')
    cfg['flow_ui_breaker_reason']=str(message or 'Flow UI control failure')[:1200]
    _save_scheduler_cfg(profile_id,cfg)
    persist_event_log({
        'type':'SCHEDULER_FLOW_UI_BREAKER',
        'profileId':profile_id,
        'message':f'Flow UI lá»—i láº·p/spam Ä‘Ã£ bá»‹ khÃ³a {FLOW_UI_BREAKER_MINUTES} phÃºt Â· scheduler váº«n ON nhÆ°ng KHÃ”NG táº¡o job má»›i trong thá»i gian nÃ y Â· {message}',
    })
    return cfg['flow_ui_breaker_until']


def _flow_ui_breaker_status(profile: dict[str, Any]) -> dict[str, Any]:
    cfg=dict(profile.get('scheduler_config') or {})
    raw=cfg.get('flow_ui_breaker_until')
    until=_parse_iso_utc(raw) if raw else None
    now=datetime.now(timezone.utc)
    reason=str(cfg.get('flow_ui_breaker_reason') or 'Flow UI control failure')
    # V2.14.19 false-positive: Flow had selectedCount=1 + "add to prompt",
    # but extension incorrectly required aria-selected=true and tripped the breaker.
    legacy_false_positive = (
        'FLOW_UI_ASSET_PICKER_STUCK' in reason
        and ('selectedCount' in reason or 'add to prompt' in reason.lower())
    )
    if until and now < until and not legacy_false_positive:
        return {
            'blocked':True,
            'until':until.isoformat(timespec='seconds'),
            'seconds':max(0,int((until-now).total_seconds())),
            'reason':reason,
        }
    if legacy_false_positive:
        cfg.pop('flow_ui_breaker_until',None); cfg.pop('flow_ui_breaker_reason',None)
        try:_save_scheduler_cfg(str(profile.get('id')),cfg)
        except Exception:pass
        persist_event_log({
            'type':'SCHEDULER_CLEAR_21419_FALSE_BREAKER',
            'profileId':str(profile.get('id') or ''),
            'message':'ÄÃ£ clear breaker sai cá»§a V2.14.19 (selectedCount=1 / Add to prompt).'
        })
        return {'blocked':False,'until':None,'seconds':0,'reason':None}
    if raw:
        cfg.pop('flow_ui_breaker_until',None); cfg.pop('flow_ui_breaker_reason',None)
        try:_save_scheduler_cfg(str(profile.get('id')),cfg)
        except Exception:pass
    return {'blocked':False,'until':None,'seconds':0,'reason':None}


RETRY_POLICIES: dict[str, tuple[int,float,float]] = {
    # max stage attempts, base seconds, max seconds
    "dispatch": (4, 5, 60),
    "restart": (3, 5, 60),
    "disconnect": (4, 8, 120),
    "ui": (3, 15, 180),
    "network": (4, 8, 180),
    "timeout": (4, 15, 300),
    "media": (4, 10, 180),
    "render": (3, 15, 300),
    "qc": (2, 30, 300),
    "rate_limit": (3, 300, 1800),
    "quota": (2, 900, 3600),
    "publish": (RETRY_PUBLISH_MAX, 60, 1800),
    "runtime": (3, 20, 300),
}

DETERMINISTIC_FAILURE_MARKERS=(
    "missing videoprompt","thiáº¿u videoprompt","invalid scene schema","scene schema",
    "persona pack chÆ°a Ä‘á»§","khÃ´ng tháº¥y ffmpeg","khÃ´ng tháº¥y ffprobe","model_not_supported",
    "khÃ´ng tÃ¬m tháº¥y facebook page/token","token invalid","invalid oauth","error validating access token",
)
NETWORK_FAILURE_MARKERS=(
    "connection reset","connection refused","networkerror","failed to fetch","temporarily unavailable",
    "remote end closed","cannot connect","name resolution","econnreset","econnrefused","socket",
)
TIMEOUT_FAILURE_MARKERS=("timeout","timed out","quÃ¡ thá»i gian","stale watchdog","khÃ´ng cÃ³ progress")
RATE_LIMIT_FAILURE_MARKERS=("rate limit","too many requests","429","resource_exhausted")
QUOTA_FAILURE_MARKERS=("quota","credit","credits","insufficient","limit exceeded")
MEDIA_FAILURE_MARKERS=("thiáº¿u áº£nh scene","thiáº¿u video scene","khÃ´ng Ä‘á»§ áº£nh","khÃ´ng Ä‘á»§ video","mediaid","local_path","stream/local","image recovery","video recovery")
UI_FAILURE_MARKERS=("flow_ui_","asset picker","debugger is not attached","not attached to the tab","composer","trusted-click")
AUTH_FAILURE_MARKERS=("invalid oauth","access token","token expired","token invalid","permission","(#200)","(#190)")


def _classify_failure(error: str, stage: str = "runtime") -> tuple[str,bool]:
    text=str(error or "").lower()
    stage=str(stage or "runtime").lower()
    if any(x in text for x in AUTH_FAILURE_MARKERS): return "auth",False
    if any(x in text for x in DETERMINISTIC_FAILURE_MARKERS): return "deterministic",False
    if any(x in text for x in RATE_LIMIT_FAILURE_MARKERS): return "rate_limit",True
    if any(x in text for x in QUOTA_FAILURE_MARKERS): return "quota",True
    if any(x in text for x in UI_FAILURE_MARKERS): return "ui",True
    if any(x in text for x in MEDIA_FAILURE_MARKERS) or stage=="media": return "media",True
    if any(x in text for x in NETWORK_FAILURE_MARKERS): return "network",True
    if any(x in text for x in TIMEOUT_FAILURE_MARKERS): return "timeout",True
    if stage in {"dispatch","restart","disconnect","render","qc","publish"}: return stage,True
    return "runtime",True


def _retry_backoff_seconds(stage: str, attempt: int) -> int:
    policy=RETRY_POLICIES.get(stage,RETRY_POLICIES["runtime"])
    _,base,cap=policy
    raw=min(float(cap),float(base)*(2**max(0,int(attempt)-1)))
    jitter=raw*RETRY_JITTER_RATIO*random.uniform(-1.0,1.0)
    return max(1,int(round(raw+jitter)))


def _retry_due(value: str | None) -> bool:
    dt=_parse_iso_utc(value)
    return not dt or dt<=datetime.now(timezone.utc)


def _schedule_factory_retry(flow_job_id: str, error: str, *, stage: str="runtime", force_delay: int | None=None) -> dict[str,Any]:
    job=get_flow_job(flow_job_id) or {}
    queue=get_content_queue_by_flow(flow_job_id)
    failure_class,retryable=_classify_failure(error,stage)
    policy_stage=failure_class if failure_class in RETRY_POLICIES else stage if stage in RETRY_POLICIES else "runtime"
    max_stage,_,_=RETRY_POLICIES.get(policy_stage,RETRY_POLICIES["runtime"])
    prev_total=int(job.get("retry_count") or 0)
    prev_stage=str(job.get("retry_stage") or "")
    prev_stage_count=int(job.get("retry_stage_count") or 0) if prev_stage==policy_stage else 0

    # Idempotent duplicate failure notification: do not consume retry budget twice.
    if str(job.get("status") or "")=="retry_wait" and not _retry_due(job.get("retry_after")):
        return {"scheduled":True,"duplicate":True,"attempt":prev_stage_count,"failure_class":failure_class,"retry_after":job.get("retry_after")}
    if not retryable or prev_total>=RETRY_TOTAL_MAX or prev_stage_count>=max_stage:
        return {"scheduled":False,"failure_class":failure_class,"attempt":prev_stage_count,"reason":"budget_exhausted" if retryable else "non_retryable"}

    stage_count=prev_stage_count+1; total=prev_total+1
    delay=int(force_delay) if force_delay is not None else _retry_backoff_seconds(policy_stage,stage_count)
    retry_at=(datetime.now(timezone.utc)+timedelta(seconds=max(1,delay))).isoformat(timespec="seconds")
    update_flow_job(flow_job_id,status="retry_wait",retry_count=total,retry_stage_count=stage_count,retry_stage=policy_stage,retry_after=retry_at,failure_class=failure_class,last_error_at=utcnow(),agent_id=None,error=str(error)[:4000])
    if queue:
        update_content_queue_by_flow(flow_job_id,status="retry_wait",retry_count=total,retry_after=retry_at,failure_class=failure_class,error=f"AUTO RETRY {policy_stage} {stage_count}/{max_stage} Â· {error}"[:2000])
    persist_event_log({"type":"AUTO_RETRY_SCHEDULED","profileId":queue.get("page_profile_id") if queue else None,"jobId":flow_job_id,"message":f"RETRY SAME JOB Â· stage={policy_stage} Â· {stage_count}/{max_stage} Â· sau {delay}s Â· {error}"})
    return {"scheduled":True,"attempt":stage_count,"max":max_stage,"delay":delay,"retry_after":retry_at,"failure_class":failure_class,"stage":policy_stage}


async def _requeue_same_job_immediate(flow_job_id: str, reason: str, *, stage: str="runtime") -> bool:
    job=get_flow_job(flow_job_id) or {}
    queue=get_content_queue_by_flow(flow_job_id)
    total=int(job.get("retry_count") or 0)
    if total>=RETRY_TOTAL_MAX:
        return False
    failure_class,_=_classify_failure(reason,stage)
    next_total=total+1
    update_flow_job(
        flow_job_id,status="queued",agent_id=None,error=None,retry_after=None,
        retry_count=next_total,retry_stage=stage,retry_stage_count=int(job.get("retry_stage_count") or 0)+1,
        failure_class=failure_class,last_error_at=utcnow(),
    )
    if queue:
        update_content_queue_by_flow(
            flow_job_id,status="generating",retry_count=next_total,retry_after=None,
            failure_class=failure_class,error=f"SAME JOB RETRY Â· {stage} Â· {reason}"[:2000],
        )
    persist_event_log({
        "type":"SAME_JOB_IMMEDIATE_RETRY","profileId":queue.get("page_profile_id") if queue else None,
        "jobId":flow_job_id,
        "message":f"KhÃ´ng táº¡o replacement Â· requeue SAME JOB ngay Â· stage={stage} Â· retry={next_total}/{RETRY_TOTAL_MAX} Â· {reason}",
    })
    await dispatch_jobs()
    return True


def _clear_retry_state(flow_job_id: str) -> None:
    update_flow_job(flow_job_id,retry_stage=None,retry_stage_count=0,retry_after=None,failure_class=None,error=None)
    if get_content_queue_by_flow(flow_job_id):
        update_content_queue_by_flow(flow_job_id,retry_after=None,failure_class=None,error=None)


def _disk_space_ok(required_gb: float | None=None) -> tuple[bool,float]:
    required=float(required_gb or RETRY_DISK_MIN_FREE_GB)
    try: free=shutil.disk_usage(ROOT).free/(1024**3)
    except Exception: return True,999.0
    return free>=required,free


async def _process_due_factory_retries(profile_id: str) -> dict[str,Any]:
    now=datetime.now(timezone.utc)
    with conn() as c:
        rows=c.execute("SELECT flow_job_id,retry_after FROM content_queue WHERE page_profile_id=? AND status='retry_wait' ORDER BY updated_at ASC",(profile_id,)).fetchall()
    due=[]
    for row in rows:
        if _retry_due(row["retry_after"]): due.append(str(row["flow_job_id"] or ""))
    if not due: return {"started":0,"jobs":[]}
    started=[]
    for jid in due:
        FACTORY_RESUME_RETRY_AFTER.pop(jid,None)
        job=get_flow_job(jid) or {}; stage=str(job.get("retry_stage") or "runtime"); result=job.get("result") or {}
        if result.get("ok") is True:
            # Media/render/QC retry: same successful Flow result; no generation.
            update_flow_job(jid,status="failed",agent_id=None,retry_after=None)
            update_content_queue_by_flow(jid,status="failed",retry_after=None)
            started.append(jid)
            continue
        # Generation/dispatch retry: same flow job ID, same scenes; queue it again.
        update_flow_job(jid,status="queued",agent_id=None,retry_after=None,error=None)
        update_content_queue_by_flow(jid,status="generating",retry_after=None,error=f"RETRY SAME JOB Â· stage={stage}")
        started.append(jid)
    if started:
        persist_event_log({"type":"AUTO_RETRY_DUE","profileId":profile_id,"message":f"Äáº¿n háº¡n retry {len(started)} SAME job: {', '.join(x[-8:] for x in started)}"})
        await dispatch_jobs()
    return {"started":len(started),"jobs":started}


def mark_content_queue_failed(flow_job_id: str, error: str, stage: str="runtime") -> None:
    item=get_content_queue_by_flow(flow_job_id)
    if not item: return
    message=str(error or "Pipeline failed")
    if _is_rebuildable_schema_failure(flow_job_id,message):
        update_flow_job(flow_job_id,status="failed",error=message,retry_after=None,failure_class="deterministic")
        update_content_queue_by_flow(flow_job_id,status="discarded",error=f"AUTO REBUILD SCHEMA Â· {message}"[:2000])
        persist_event_log({"type":"SCHEDULER_AUTO_DISCARD_SCHEMA","profileId":item.get("page_profile_id"),"jobId":flow_job_id,"message":f"Schema deterministic Â· khÃ´ng retry job cÅ© Â· {message}"})
        return

    retry=_schedule_factory_retry(flow_job_id,message,stage=stage)
    if retry.get("scheduled"):
        return

    profile_id=str(item.get("page_profile_id") or "")
    failure_class=str(retry.get("failure_class") or _classify_failure(message,stage)[0])
    if profile_id and failure_class=="ui":
        until=_set_flow_ui_breaker(profile_id,message)
        update_flow_job(flow_job_id,status="failed",error=message,retry_after=None,failure_class=failure_class)
        update_content_queue_by_flow(flow_job_id,status="discarded",error=f"RETRY EXHAUSTED UI Â· {message}"[:2000])
        persist_event_log({"type":"SCHEDULER_AUTO_QUARANTINE_UI","profileId":profile_id,"jobId":flow_job_id,"message":f"UI retry Ä‘Ã£ háº¿t budget Â· breaker tá»›i {until} Â· {message}"})
        return
    update_flow_job(flow_job_id,status="failed",error=message,retry_after=None,failure_class=failure_class)
    update_content_queue_by_flow(flow_job_id,status="discarded",error=f"RETRY EXHAUSTED {failure_class} Â· {message}"[:2000],failure_class=failure_class)
    persist_event_log({"type":"SCHEDULER_RETRY_EXHAUSTED","profileId":item.get("page_profile_id"),"jobId":flow_job_id,"message":f"Retry budget Ä‘Ã£ háº¿t/non-retryable Â· class={failure_class} Â· {message}"})


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


def _scheduler_quarantine_failed(profile_id: str) -> int:
    with conn() as c:
        rows=c.execute("SELECT id,flow_job_id,error FROM content_queue WHERE page_profile_id=? AND status='failed' ORDER BY updated_at ASC",(profile_id,)).fetchall()
    if not rows: return 0
    now=utcnow()
    with conn() as c:
        for row in rows:
            msg=str(row['error'] or 'Pipeline failed')
            c.execute("UPDATE content_queue SET status='discarded',error=?,updated_at=? WHERE id=?",(f'AUTO QUARANTINE Â· {msg}'[:2000],now,row['id']))
    for row in rows:
        persist_event_log({'type':'SCHEDULER_AUTO_QUARANTINE_FAILED','profileId':profile_id,'jobId':row['flow_job_id'],'message':f"Tá»± cÃ¡ch ly job lá»—i {row['id']} Â· scheduler váº«n ON Â· sáº½ táº¡o bÃ¹ cÃ³ giá»›i háº¡n."})
    return len(rows)


def _scheduler_recovery_circuit(profile_id: str) -> dict[str, Any]:
    now=datetime.now(timezone.utc)
    since=(now-timedelta(minutes=SCHEDULER_AUTO_RECOVERY_WINDOW_MINUTES)).isoformat(timespec='seconds')
    with conn() as c:
        rows=c.execute("SELECT updated_at FROM content_queue WHERE page_profile_id=? AND status='discarded' AND updated_at>=? AND error LIKE 'AUTO QUARANTINE%' ORDER BY updated_at DESC",(profile_id,since)).fetchall()
    count=len(rows); last_dt=None
    if rows:
        try:
            last_dt=datetime.fromisoformat(str(rows[0]['updated_at']))
            last_dt=last_dt.replace(tzinfo=timezone.utc) if last_dt.tzinfo is None else last_dt.astimezone(timezone.utc)
        except Exception: last_dt=now
    cooldown_until=(last_dt+timedelta(minutes=SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES)) if last_dt else None
    blocked=bool(count>=SCHEDULER_AUTO_RECOVERY_MAX_FAILURES and cooldown_until and now<cooldown_until)
    return {'count':count,'blocked':blocked,'cooldown_until':cooldown_until.isoformat(timespec='seconds') if cooldown_until else None,'cooldown_seconds':max(0,int((cooldown_until-now).total_seconds())) if blocked and cooldown_until else 0}


def scheduler_status(profile_id: str) -> dict[str, Any]:
    profile = get_page_profile(profile_id)
    if not profile:
        raise ValueError("KhÃ´ng tháº¥y Page Profile")
    with conn() as c:
        rows = c.execute(
            "SELECT status,COUNT(*) n FROM content_queue WHERE page_profile_id=? GROUP BY status",
            (profile_id,),
        ).fetchall()
    counts = {str(r["status"]): int(r["n"]) for r in rows}
    active = counts.get("generating", 0) + counts.get("ready", 0) + counts.get("publishing", 0)
    cfg = _scheduler_cfg(profile)
    circuit=_scheduler_recovery_circuit(profile_id)
    ui_breaker=_flow_ui_breaker_status(profile)
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
        "retry_wait": counts.get("retry_wait", 0),
        "publishing": counts.get("publishing", 0),
        "published": counts.get("published", 0),
        "failed": counts.get("failed", 0) + counts.get("publish_failed", 0),
        "recent_auto_failures": int(circuit.get("count") or 0),
        "generation_blocked": bool(ui_breaker.get('blocked') or circuit.get("blocked")),
        "generation_block_reason": (
            f"FLOW UI BREAKER {int(ui_breaker.get('seconds') or 0)}s Â· khÃ´ng spam thao tÃ¡c browser Â· tá»± thá»­ láº¡i sau cooldown."
            if ui_breaker.get('blocked') else
            (f"AUTO RECOVERY cooldown {int(circuit.get('cooldown_seconds') or 0)}s rá»“i tá»± thá»­ láº¡i." if circuit.get("blocked") else None)
        ),
        "flow_ui_breaker_until": ui_breaker.get('until'),
        "flow_ui_breaker_seconds": int(ui_breaker.get('seconds') or 0),
        "flow_ui_breaker_reason": ui_breaker.get('reason'),
        "auto_recovery_cooldown_until": circuit.get("cooldown_until"),
        "auto_recovery_cooldown_seconds": int(circuit.get("cooldown_seconds") or 0),
        "buffer_active": active,
        "buffer_deficit": max(0,int(profile.get("buffer_target") or 2)-active),
        "buffer_invariant_ok": active >= int(profile.get("buffer_target") or 2),
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
        beat_image_count=int(cfg.get("beat_image_count") or 10),
        beat_duration_sec=float(cfg.get("beat_duration_sec") or 10),
        beat_motion_preset=normalize_transition_preset(cfg.get("beat_motion_preset") or "chaos_mix"),
        i2v_clip_count=int(cfg.get("i2v_clip_count") or 3),
        i2v_clip_duration=str(cfg.get("i2v_clip_duration") or "4s"),
        image_concurrency=int(cfg.get("image_concurrency") or 9),
        video_concurrency=int(cfg.get("video_concurrency") or 4),
        auto_publish=False,
        facebook_dry_run=True,
    )



def _factory_has_final_video(job_id: str) -> bool:
    with conn() as c:
        row=c.execute(
            "SELECT local_path FROM assets WHERE job_id=? AND kind='final_video' ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    if not row:
        return False
    lp=str(row["local_path"] or "").strip()
    return bool(lp and Path(lp).exists())


def _factory_expected_scene_sets(job: dict[str, Any]) -> tuple[set[int],set[int]]:
    meta=get_factory_meta(job); mode=str(meta.get("mode") or "IMAGE_BEAT")
    scenes=job.get("scenes") or []
    image_expected:set[int]=set(); video_expected:set[int]=set()
    for i,scene in enumerate(scenes):
        sid=int(scene.get("sceneId") or i+1); smeta=scene.get("metadata") or {}
        if isinstance(smeta.get("makeVideo"),bool): make_video=bool(smeta.get("makeVideo"))
        elif isinstance(smeta.get("mixedMotion"),bool): make_video=bool(smeta.get("mixedMotion"))
        else: make_video=(mode=="IMAGE_TO_VIDEO")
        if mode in {"IMAGE_BEAT","IMAGE_MIX"}: image_expected.add(sid)
        if mode=="IMAGE_TO_VIDEO" or (mode=="IMAGE_MIX" and make_video): video_expected.add(sid)
    return image_expected,video_expected


def _factory_result_media_scene_sets(job: dict[str, Any]) -> tuple[set[int],set[int]]:
    result=job.get("result") or {}; rows=result.get("results") or []
    image:set[int]=set(); video:set[int]=set()
    for r in rows:
        sid=int(r.get("sceneId") or (int(r.get("index") or 0)+1)); im=r.get("image") or {}
        if im.get("mediaId") or im.get("url"): image.add(sid)
        if any(r.get("videoMediaIds") or []): video.add(sid)
        if any((d or {}).get("mediaId") for d in (r.get("downloads") or [])): video.add(sid)
    return image,video


def _recoverable_counts_from_result(job: dict[str, Any]) -> tuple[int,int]:
    result=job.get("result") or {}
    rows=result.get("results") or []
    image_ids=set()
    video_ids=set()
    for r in rows:
        image=r.get("image") or {}
        mid=str(image.get("mediaId") or "").strip()
        if mid:
            image_ids.add(mid)
        for mid in r.get("videoMediaIds") or []:
            if mid:
                video_ids.add(str(mid))
        for d in r.get("downloads") or []:
            mid=str((d or {}).get("mediaId") or "").strip()
            if mid:
                video_ids.add(mid)
    return len(image_ids),len(video_ids)


def _resumable_factory_job_for_profile(profile_id: str) -> dict[str, Any] | None:
    """Find a completed Flow generation whose final render was lost/failed.

    This is deliberately conservative: result.ok must be true and recoverable
    mediaIds must already exist. It never resumes a partial/failed generation.
    """
    with conn() as c:
        rows=c.execute(
            """SELECT q.flow_job_id,q.status queue_status,q.error queue_error,
                      f.status flow_status,f.updated_at
               FROM content_queue q
               JOIN flow_jobs f ON f.id=q.flow_job_id
               WHERE q.page_profile_id=?
                 AND f.kind LIKE 'factory_v2_%'
                 AND f.result_json IS NOT NULL
                 AND q.status IN ('discarded','failed','generating')
               ORDER BY f.updated_at DESC
               LIMIT 20""",
            (profile_id,),
        ).fetchall()

    for row in rows:
        jid=str(row["flow_job_id"] or "")
        if not jid or jid in FACTORY_RESUME_IN_FLIGHT or _factory_has_final_video(jid):
            continue
        job=get_flow_job(jid) or {}
        result=job.get("result") or {}
        if result.get("ok") is not True:
            continue
        results=result.get("results") or []
        if not results:
            continue
        meta=get_factory_meta(job)
        mode=str(meta.get("mode") or "IMAGE_BEAT")
        image_count,video_count=_recoverable_counts_from_result(job)
        expected_images,expected_videos=_factory_expected_scene_sets(job)
        result_images,result_videos=_factory_result_media_scene_sets(job)
        local_image_scenes={int(x.get("scene_id") or 0) for x in _available_images_for_job(jid)}
        local_video_scenes={int(x.get("scene_id") or 0) for x in _available_videos_for_job(jid)}
        recoverable_images=result_images|local_image_scenes
        recoverable_videos=result_videos|local_video_scenes
        recoverable=bool((not expected_images or expected_images.issubset(recoverable_images)) and (not expected_videos or expected_videos.issubset(recoverable_videos)))

        if recoverable:
            return {
                "job_id":jid,
                "job":job,
                "results":results,
                "mode":mode,
                "image_media":image_count,
                "video_media":video_count,
                "local_images":len(local_image_scenes),
                "local_videos":len(local_video_scenes),
                "expected_images":len(expected_images),
                "expected_videos":len(expected_videos),
                "queue_status":str(row["queue_status"] or ""),
                "queue_error":str(row["queue_error"] or ""),
            }
    return None


async def _resume_existing_factory_task(profile_id: str, candidate: dict[str, Any], agent: "AgentRuntime") -> None:
    jid=str(candidate["job_id"])
    FACTORY_RESUME_IN_FLIGHT.add(jid); FACTORY_FINALIZE_IN_FLIGHT.add(jid)
    try:
        agent.busy=True; agent.job_id=jid; _touch_agent(agent,"finalizing",f"resume-run:{jid}")
        update_content_queue_by_flow(jid,status="generating",error="RESUME EXISTING MEDIA Â· khÃ´ng táº¡o láº¡i áº£nh/video")
        update_flow_job(jid,status="flow_done",error=None)
        persist_event_log({"type":"FACTORY_RESUME_EXISTING_MEDIA","profileId":profile_id,"jobId":jid,"message":f"RESUME job cÅ© {jid} Â· mode={candidate['mode']} Â· imageMedia={candidate['image_media']}/{candidate.get('expected_images',0)} Â· videoMedia={candidate['video_media']}/{candidate.get('expected_videos',0)} Â· KHÃ”NG GENERATE Láº I."})
        await ui_broadcast({"type":"FACTORY_RESUME_EXISTING_MEDIA","profileId":profile_id,"jobId":jid,"mode":candidate["mode"],"noRegeneration":True})
        await recover_then_render_factory_v2(jid,candidate["results"],agent)
        if str((get_flow_job(jid) or {}).get("status") or "")=="failed":
            FACTORY_RESUME_RETRY_AFTER[jid]=time.monotonic()+SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES*60
        else:
            FACTORY_RESUME_RETRY_AFTER.pop(jid,None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        err=f"Resume media cÅ© lá»—i: {exc}"; update_flow_job(jid,status="failed",error=err)
        FACTORY_RESUME_RETRY_AFTER[jid]=time.monotonic()+SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES*60
        if get_content_queue_by_flow(jid): mark_content_queue_failed(jid,err,stage="runtime")
        persist_event_log({"type":"FACTORY_RESUME_EXISTING_FAILED","profileId":profile_id,"jobId":jid,"message":err})
    finally:
        FACTORY_RESUME_IN_FLIGHT.discard(jid); FACTORY_FINALIZE_IN_FLIGHT.discard(jid)
        if agent.job_id==jid:
            agent.busy=False; agent.job_id=None; _touch_agent(agent,"idle",f"resume-done:{jid}")
        await dispatch_jobs(); spawn(scheduler_fill_profile(profile_id))


def _reconcile_completed_factory_queue(profile_id: str | None = None) -> int:
    params:list[Any]=[]
    where="q.status IN ('generating','failed','discarded')"
    if profile_id:
        where+=" AND q.page_profile_id=?"; params.append(profile_id)
    with conn() as c:
        rows=c.execute(f"""SELECT q.flow_job_id,q.page_profile_id FROM content_queue q JOIN flow_jobs f ON f.id=q.flow_job_id WHERE {where} AND f.kind LIKE 'factory_v2_%'""",params).fetchall()
    repaired=0
    for row in rows:
        jid=str(row["flow_job_id"] or "")
        with conn() as c:
            asset=c.execute("SELECT local_path FROM assets WHERE job_id=? AND kind='final_video' ORDER BY created_at DESC LIMIT 1",(jid,)).fetchone()
        path=str(asset["local_path"] or "").strip() if asset else ""
        if not path or not _valid_local_video_file(path):
            continue
        qc=latest_qc(jid)
        if not qc or not qc.get("passed"):
            continue
        job=get_flow_job(jid) or {}; meta=get_factory_meta(job)
        caption=str(meta.get("caption") or ""); tags=meta.get("hashtags") or []
        if tags and not any(str(t) in caption for t in tags):
            caption=(caption+"\n\n"+" ".join(str(t) for t in tags)).strip()
        update_content_queue_by_flow(jid,status="ready",video_path=path,error=None,title=str(meta.get("title") or meta.get("profileName") or ""),description=caption)
        update_flow_job(jid,status="done",error=None)
        FACTORY_RESUME_RETRY_AFTER.pop(jid,None)
        repaired+=1
        persist_event_log({"type":"SCHEDULER_RESTORE_READY_FINAL","profileId":row["page_profile_id"],"jobId":jid,"message":"Final MP4 + QC PASS Ä‘Ã£ tá»“n táº¡i Â· queue=READY Â· khÃ´ng generate/render láº¡i."})
    return repaired


async def _try_resume_existing_factory(profile_id: str) -> dict[str, Any] | None:
    candidate=_resumable_factory_job_for_profile(profile_id)
    if not candidate:
        # If a resume task is already active for this profile, keep generation blocked.
        with conn() as c:
            rows=c.execute(
                "SELECT flow_job_id FROM content_queue WHERE page_profile_id=? AND status='generating'",
                (profile_id,),
            ).fetchall()
        active_resume=[str(r["flow_job_id"]) for r in rows if str(r["flow_job_id"]) in FACTORY_RESUME_IN_FLIGHT]
        if active_resume:
            return {"status":"in_progress","job_id":active_resume[0]}
        return None

    jid=str(candidate["job_id"])
    if jid in FACTORY_RESUME_IN_FLIGHT:
        return {"status":"in_progress","job_id":jid}
    retry_at=float(FACTORY_RESUME_RETRY_AFTER.get(jid) or 0.0)
    if retry_at>time.monotonic():
        return {"status":"cooldown","job_id":jid,"cooldown_seconds":max(1,int(retry_at-time.monotonic()))}

    idle=[a for a in compatible_agents() if not a.busy and a.phase=="idle"]
    if not idle:
        return {"status":"wait_agent","job_id":jid}

    agent=idle[0]
    # Atomic claim BEFORE spawn so dispatch cannot steal this worker.
    FACTORY_RESUME_IN_FLIGHT.add(jid)
    FACTORY_FINALIZE_IN_FLIGHT.add(jid)
    agent.busy=True; agent.job_id=jid; _touch_agent(agent,"finalizing",f"resume:{jid}")
    update_content_queue_by_flow(
        jid,
        status="generating",
        error="RESUME EXISTING MEDIA Â· chá» recover/render",
    )
    persist_event_log({
        "type":"SCHEDULER_RESUME_OLD_JOB",
        "profileId":profile_id,"jobId":jid,
        "message":f"Æ¯u tiÃªn cá»©u job cÅ© {jid}; KHÃ”NG táº¡o batch má»›i.",
    })
    # task helper also add()s idempotently.
    spawn(_resume_existing_factory_task(profile_id,candidate,agent))
    return {"status":"started","job_id":jid,"mode":candidate["mode"]}


async def _scheduler_fill_profile_unlocked(profile_id: str, force_count: int | None = None) -> dict[str, Any]:
    profile=get_page_profile(profile_id)
    if not profile: raise ValueError('KhÃ´ng tháº¥y Page Profile')
    cfg=profile.get('scheduler_config') or {}
    target=int(profile.get('buffer_target') or 2)
    if not compatible_agents():
        seen=[
            f"{a.version or '?'}{'/NOT_READY' if not getattr(a,'ready',False) else ''}"
            for a in AGENTS.values()
        ]
        msg=(f"Äang chá» Flow Agent READY: {', '.join(seen)}." if seen else f"Äang chá» Flow Agent >= {MIN_EXTENSION_VERSION}.")
        _scheduler_state_event(profile_id,'WAIT_AGENT',msg+' READY xong sáº½ tá»± táº¡o buffer.')
        return {'created':0,'target':target,'jobs':[],'blocked':True,'reason':'extension_not_ready','message':msg}

    # Retry due SAME jobs before considering any replacement content.
    due_retry=await _process_due_factory_retries(profile_id)
    if due_retry.get("started"):
        _scheduler_state_event(profile_id,"RETRYING",f"Äang retry {due_retry['started']} job cÅ© Â· Æ°u tiÃªn SAME job trÆ°á»›c.")

    _reconcile_completed_factory_queue(profile_id)
    # Never create a replacement while a completed old Flow job is recoverable.
    resume=await _try_resume_existing_factory(profile_id)
    if resume:
        jid=resume.get('job_id')
        state=resume.get('status')
        _scheduler_state_event(
            profile_id,'RESUME_EXISTING',
            f"Äang cá»©u job cÅ© {jid} Â· {state} Â· khÃ´ng generate áº£nh/video láº¡i."
        )
        return {
            'created':0,'target':target,'jobs':[],'blocked':True,
            'reason':'resume_existing_media','resume':resume,
            'message':f"Resume job cÅ© {jid}; khÃ´ng táº¡o batch má»›i."
        }

    ui_breaker=_flow_ui_breaker_status(profile)
    if ui_breaker.get('blocked'):
        sec=int(ui_breaker.get('seconds') or 0)
        _scheduler_state_event(profile_id,'FLOW_UI_COOLDOWN',f'Flow UI breaker Â· táº¡m khÃ³a táº¡o job má»›i {sec}s Ä‘á»ƒ extension khÃ´ng spam browser; tá»± má»Ÿ láº¡i sau cooldown.')
        return {'created':0,'target':target,'jobs':[],'blocked':True,'reason':'flow_ui_breaker','cooldown_seconds':sec,'message':f'Flow UI breaker {sec}s Â· khÃ´ng táº¡o job má»›i.'}

    old_schema=_auto_discard_rebuildable_failed(profile_id)
    if old_schema:
        persist_event_log({'type':'SCHEDULER_SCHEMA_REBUILD_READY','profileId':profile_id,'message':f'ÄÃ£ dá»n {old_schema} job schema cÅ© Â· tiáº¿p tá»¥c AUTO.'})
    quarantined=_scheduler_quarantine_failed(profile_id)
    if quarantined:
        persist_event_log({'type':'SCHEDULER_AUTO_RECOVERY','profileId':profile_id,'message':f'ÄÃ£ cÃ¡ch ly {quarantined} job lá»—i Â· lá»‹ch váº«n ON.'})

    with conn() as c:
        row=c.execute(
            """SELECT
                 SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) ready,
                 SUM(CASE WHEN status='generating' THEN 1 ELSE 0 END) generating,
                 SUM(CASE WHEN status='publishing' THEN 1 ELSE 0 END) publishing,
                 SUM(CASE WHEN status='retry_wait' THEN 1 ELSE 0 END) retry_wait
               FROM content_queue WHERE page_profile_id=?""",
            (profile_id,),
        ).fetchone()
    ready=int((row["ready"] if row else 0) or 0)
    generating=int((row["generating"] if row else 0) or 0)
    publishing=int((row["publishing"] if row else 0) or 0)
    retry_wait=int((row["retry_wait"] if row else 0) or 0)
    active=ready+generating+publishing
    circuit=_scheduler_recovery_circuit(profile_id)
    if circuit.get('blocked'):
        sec=int(circuit.get('cooldown_seconds') or 0)
        _scheduler_state_event(profile_id,'AUTO_COOLDOWN',f"AUTO váº«n báº­t Â· táº¡m nghá»‰ {sec}s sau {circuit.get('count')} lá»—i gáº§n Ä‘Ã¢y; háº¿t cooldown tá»± thá»­ láº¡i.")
        return {'created':0,'active_before':active,'target':target,'jobs':[],'blocked':True,'reason':'auto_recovery_cooldown','cooldown_seconds':sec,'recent_failures':circuit.get('count',0),'message':f'AUTO cooldown {sec}s rá»“i tá»± cháº¡y láº¡i.'}

    _scheduler_state_event(
        profile_id,'ACTIVE',
        f'BUFFER CTRL Â· READY={ready} GEN={generating} POST={publishing} RETRY={retry_wait} Â· target={target}.'
    )
    missing=max(0,int(force_count) if force_count is not None else target-active)
    # Half-open circuit: after a failure storm, probe with only ONE new content job.
    if int(circuit.get("count") or 0)>=SCHEDULER_AUTO_RECOVERY_MAX_FAILURES and not circuit.get("blocked") and missing>1:
        missing=1
        persist_event_log({"type":"SCHEDULER_HALF_OPEN_PROBE","profileId":profile_id,"message":"Cooldown háº¿t Â· chá»‰ táº¡o 1 probe job trÆ°á»›c khi má»Ÿ full buffer."})
    if missing<=0:
        _scheduler_state_event(profile_id,'BUFFER_OK',f'Buffer Ä‘á»§ {active}/{target} Â· READY={ready} GEN={generating}.')
        return {'created':0,'active_before':active,'target':target,'jobs':[],'ready':ready,'generating':generating,'retry_wait':retry_wait}
    req=_scheduler_factory_request(profile_id,cfg,missing)
    provisional=f"sched_{server_stamp()}_{uuid.uuid4().hex[:6]}"
    created=[]
    for i in range(missing):
        mode=choose_factory_mode(profile,req.mode)
        scenes,flow,kind=build_factory_v2_job(profile,req,provisional,i+1,mode)
        jid=create_flow_job(kind,scenes,flow); qid=create_content_queue_item(profile_id,jid)
        created.append({'job_id':jid,'queue_id':qid,'mode':mode})
    run_id=create_factory_run(profile_id,req,[x['job_id'] for x in created])
    with conn() as c:
        c.execute('UPDATE factory_runs SET id=id,config_json=? WHERE id=?',(dumps({**req.model_dump(),'scheduler':True}),run_id))
    persist_event_log({
        'type':'SCHEDULER_PREFILL_NOW','profileId':profile_id,
        'message':f'PREFILL NGAY {missing} video Â· READY={ready} GEN={generating} RETRY={retry_wait} Â· buffer {active}/{target} â†’ {target}/{target}.'
    })
    _scheduler_state_event(profile_id,'GENERATING',f'PREFILL Â· Ä‘ang táº¡o {missing} video Â· buffer {active}/{target}.')
    await dispatch_jobs()
    return {'created':len(created),'active_before':active,'target':target,'jobs':created}


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
        persist_event_log({"type": "SCHEDULER_BLOCKED", "profileId": profile_id, "message": "ChÆ°a map Facebook Page/token"})
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
    pub_id=None
    existing_pub_id=str(q.get("publish_job_id") or "").strip()
    if existing_pub_id:
        with conn() as c: existing_pub=c.execute("SELECT * FROM publish_jobs WHERE id=?",(existing_pub_id,)).fetchone()
        if existing_pub and str(existing_pub["status"] or "")=="retry_wait":
            due=_parse_iso_utc(existing_pub["retry_after"])
            if due and now<due:
                _scheduler_set_next(profile_id,due)
                return None
            pub_id=existing_pub_id
    if not pub_id:
        pub_id=create_publish_job(req,bool(profile.get("scheduler_dry_run",True)))
    with conn() as c:
        c.execute(
            "UPDATE content_queue SET status='publishing',publish_job_id=?,scheduled_for=?,updated_at=? WHERE id=?",
            (pub_id,next_at.isoformat(timespec="seconds"),utcnow(),q["id"]),
        )
    persist_event_log({"type": "SCHEDULED_PUBLISH_START", "profileId": profile_id, "jobId": q.get("flow_job_id"), "message": f"Äáº¿n giá» Ä‘Äƒng Â· queue={q['id']} Â· dry={bool(profile.get('scheduler_dry_run', True))}"})
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
            msg = f"ÄÄƒng xong Â· slot káº¿ tiáº¿p {next_local.strftime('%d/%m %H:%M')}"
        else:
            interval = int(profile.get("publish_interval_minutes") or 180)
            next_due = now + timedelta(minutes=interval)
            _scheduler_set_next(profile_id, next_due, last=now, warmup=False)
            msg = f"ÄÄƒng xong Â· bÃ i káº¿ tiáº¿p sau {interval} phÃºt"
        persist_event_log({"type": "SCHEDULED_PUBLISH_DONE", "profileId": profile_id, "message": msg})
        return {"ok": True, "queue_id": q["id"], "publish_job_id": pub_id, "status": pub_status}
    pubd=dict(pub) if pub else {}
    failure_class=str(pubd.get("failure_class") or "publish")
    retry_after=_parse_iso_utc(pubd.get("retry_after"))
    if pub_status=="retry_wait" and retry_after:
        with conn() as c: c.execute("UPDATE content_queue SET status='ready',error=?,retry_after=?,failure_class=?,updated_at=? WHERE id=?",(f"publish retry Â· {failure_class}",retry_after.isoformat(timespec="seconds"),failure_class,utcnow(),q["id"]))
        _scheduler_set_next(profile_id,retry_after)
        persist_event_log({"type":"SCHEDULED_PUBLISH_RETRY","profileId":profile_id,"message":f"Publish transient {failure_class} Â· retry {retry_after.astimezone(SERVER_TZ).strftime('%H:%M:%S')}"})
        return {"ok":False,"status":pub_status,"retry_after":retry_after.isoformat(timespec="seconds")}
    with conn() as c: c.execute("UPDATE content_queue SET status='ready',error=?,failure_class=?,updated_at=? WHERE id=?",(f"publish {pub_status}",failure_class,utcnow(),q["id"]))
    retry=now+timedelta(minutes=60 if failure_class=="auth" else 15); _scheduler_set_next(profile_id,retry)
    persist_event_log({"type":"SCHEDULED_PUBLISH_FAILED","profileId":profile_id,"message":f"Publish lá»—i terminal {failure_class} Â· {'cáº§n sá»­a token/quyá»n' if failure_class=='auth' else 'retry lá»‹ch sau'}"})
    return {"ok":False,"status":pub_status,"failure_class":failure_class}


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


def _cleanup_stale_retry_wait() -> int:
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(timespec="seconds")
    with conn() as c:
        rows=c.execute("SELECT id,flow_job_id FROM content_queue WHERE status='retry_wait' AND updated_at<?",(cutoff,)).fetchall()
        for row in rows:
            c.execute("UPDATE content_queue SET status='discarded',error='Retry stale >24h Â· auto cleanup',updated_at=? WHERE id=?",(utcnow(),row["id"]))
            c.execute("UPDATE flow_jobs SET status='failed',error='Retry stale >24h Â· auto cleanup',updated_at=? WHERE id=?",(utcnow(),row["flow_job_id"]))
    return len(rows)


async def reconcile_scheduler_on_startup() -> None:
    stale=_cleanup_stale_retry_wait()
    if stale: persist_event_log({"type":"RETRY_STALE_CLEANUP","message":f"Dá»n {stale} retry_wait cÅ© >24h."})
    for profile in list_page_profiles():
        if not profile.get("scheduler_enabled") or not profile.get("enabled"):
            continue
        profile_id = str(profile["id"])
        try:
            restored_ready=_reconcile_completed_factory_queue(profile_id)
            if restored_ready:
                persist_event_log({"type":"SCHEDULER_STARTUP_READY_REPAIR","profileId":profile_id,"message":f"Startup: phá»¥c há»“i {restored_ready} final MP4/QC PASS vá» READY."})
            repaired=_auto_discard_rebuildable_failed(profile_id)
            if repaired:
                persist_event_log({
                    "type":"SCHEDULER_STARTUP_SCHEMA_REPAIR","profileId":profile_id,
                    "message":f"Startup: tá»± bá» {repaired} job IMAGE_MIX/schema cÅ© Ä‘á»ƒ táº¡o bÃ¹.",
                })
            cfg = _scheduler_cfg(profile)
            if cfg.get("scheduler_mode") == "DAILY_SLOTS":
                cfg, _, target = _next_daily_entry(profile_id, profile, cfg, _scheduler_local_now(), startup_reconcile=True)
                _scheduler_set_next(profile_id, target)
            else:
                _reconcile_interval_next(profile_id, profile, cfg, startup=True)
            persist_event_log({
                "type":"SCHEDULER_RESTORED","profileId":profile_id,
                "message":f"KhÃ´i phá»¥c scheduler {cfg.get('scheduler_mode')} Â· Æ°u tiÃªn RESUME media/job cÅ© trÆ°á»›c, chá»‰ táº¡o má»›i náº¿u khÃ´ng cÃ²n gÃ¬ cá»©u Ä‘Æ°á»£c."
            })
            if compatible_agents():
                await scheduler_fill_profile(profile_id)
            else:
                _scheduler_state_event(profile_id,'WAIT_AGENT',f'Scheduler restored Â· chá» Flow Agent >= {MIN_EXTENSION_VERSION}; káº¿t ná»‘i xong tá»± táº¡o buffer.')
        except Exception as exc:
            persist_event_log({"type": "SCHEDULER_RESTORE_ERROR", "profileId": profile_id, "message": str(exc)})


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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            persist_event_log({"type": "SCHEDULER_LOOP_ERROR", "message": str(exc)})
        await asyncio.sleep(BUFFER_MAINTAIN_INTERVAL_SEC)


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



def _valid_local_image_file(path: str | None) -> bool:
    raw=str(path or "").strip()
    if not raw: return False
    p=Path(raw)
    try:
        if not p.exists() or not p.is_file() or p.stat().st_size<1024: return False
        with Image.open(p) as im: im.verify()
        return True
    except Exception: return False


def _valid_local_video_file(path: str | None) -> bool:
    raw=str(path or "").strip()
    if not raw: return False
    p=Path(raw)
    try:
        if not p.exists() or not p.is_file() or p.stat().st_size<4096: return False
        if shutil.which("ffprobe"):
            info=ffprobe_info(p)
            return float(info.get("duration") or 0)>0 and int(info.get("width") or 0)>0 and int(info.get("height") or 0)>0
        return True
    except Exception: return False


def _merge_metadata_json(existing: str | None, patch: dict[str, Any]) -> str:
    base=loads(existing,{}) if existing else {}
    if not isinstance(base,dict): base={}
    base.update(patch or {})
    return dumps(base)


def _safe_remote_media_url(value: Any) -> str | None:
    raw=str(value or "").strip()
    if not raw:
        return None
    try:
        u=urlparse(raw)
    except Exception:
        return None
    return raw if u.scheme.lower()=="https" and bool(u.netloc) else None


def _asset_stream_source(row: dict[str, Any]) -> str | None:
    # Recovered local files are stable; signed Flow URLs can expire after wait/restart.
    lp=str(row.get("local_path") or "").strip()
    kind=str(row.get("kind") or "")
    if lp:
        if kind=="image" and _valid_local_image_file(lp): return lp
        if kind=="video" and _valid_local_video_file(lp): return lp
        if kind not in {"image","video"} and Path(lp).exists(): return lp
    return _safe_remote_media_url(row.get("url"))


def _available_images_for_job(job_id: str) -> list[dict[str, Any]]:
    with conn() as c:
        rows=c.execute("SELECT * FROM assets WHERE job_id=? AND kind='image' ORDER BY scene_id ASC,created_at ASC",(job_id,)).fetchall()
    by_scene={}
    for r in rows:
        d=dict(r);sid=int(d.get("scene_id") or 0)
        if sid and sid not in by_scene and _asset_stream_source(d):
            by_scene[sid]=d
    return [by_scene[k] for k in sorted(by_scene)]


def _available_videos_for_job(job_id: str) -> list[dict[str, Any]]:
    with conn() as c:
        rows=c.execute("SELECT * FROM assets WHERE job_id=? AND kind='video' AND scene_id>0 ORDER BY scene_id ASC,created_at ASC",(job_id,)).fetchall()
    by_scene={}
    for r in rows:
        d=dict(r);sid=int(d.get("scene_id") or 0)
        if sid and sid not in by_scene and _asset_stream_source(d):
            by_scene[sid]=d
    return [by_scene[k] for k in sorted(by_scene)]


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


def _video_media_map_from_job(job: dict[str, Any] | None) -> dict[int, list[str]]:
    if not job:
        return {}
    payload = job.get("result") or {}
    results = payload.get("results") or [] if isinstance(payload, dict) else []
    out: dict[int, list[str]] = {}
    for r in results:
        sid = int(r.get("sceneId") or (int(r.get("index") or 0) + 1))
        ids = [str(x) for x in (r.get("videoMediaIds") or []) if x]
        if ids:
            out[sid] = list(dict.fromkeys(ids))
    return out


def _latest_job_with_video_media() -> dict[str, Any] | None:
    for job in list_flow_jobs(120):
        if _video_media_map_from_job(job):
            return job
    return None


async def _watch_download_only_test(job_id: str, expected_scenes: int, timeout_sec: int = 180) -> None:
    """Watch direct re-download into the ORIGINAL Flow job.

    Success salvages the existing generated media. It never creates a replacement
    generation job. Factory jobs are rendered from the recovered local clips.
    """
    deadline = time.monotonic() + max(30, int(timeout_sec))
    while time.monotonic() < deadline:
        rows = _downloaded_videos_for_job(job_id)
        ready = len({int(x.get("scene_id") or 0) for x in rows})
        if ready >= expected_scenes:
            job = get_flow_job(job_id) or {}
            persist_event_log({"type":"DOWNLOAD_ONLY_TEST_OK","jobId":job_id,"message":f"TEST DOWNLOAD OK Â· {ready}/{expected_scenes} clip"})
            await ui_broadcast({"type":"DOWNLOAD_ONLY_TEST_OK","jobId":job_id,"ready":ready,"expected":expected_scenes})
            if str(job.get("kind") or "").startswith("factory_v2_"):
                if get_content_queue_by_flow(job_id):
                    update_content_queue_by_flow(job_id, status="generating", error=None)
                update_flow_job(job_id, status="rendering", error=None)
                await render_factory_v2(job_id)
            else:
                update_flow_job(job_id, status="done", error=None)
            return
        await asyncio.sleep(1.0)
    ready = len({int(x.get("scene_id") or 0) for x in _downloaded_videos_for_job(job_id)})
    err = f"TEST DOWNLOAD chÆ°a Ä‘á»§: {ready}/{expected_scenes} clip sau {timeout_sec}s"
    job=get_flow_job(job_id) or {}
    if str(job.get("kind") or "").startswith("factory_v2_"):
        meta=get_factory_meta(job); mode=str(meta.get("mode") or "")
        expected_images,expected_videos=_factory_expected_scene_sets(job)
        got={int(x.get("scene_id") or 0) for x in _available_videos_for_job(job_id)}
        missing=sorted(expected_videos-got)
        if mode=="IMAGE_MIX":
            persist_event_log({"type":"DOWNLOAD_ONLY_DEGRADED_RENDER","jobId":job_id,"message":f"{err} -> IMAGE_MIX dÃ¹ng still fallback, tiáº¿p tá»¥c render."})
            update_flow_job(job_id,status="rendering",error=None)
            await render_factory_v2(job_id)
            return
        if missing:
            await asyncio.to_thread(_materialize_missing_video_fallbacks,job_id,missing,meta)
            got={int(x.get("scene_id") or 0) for x in _available_videos_for_job(job_id)}
            if expected_videos.issubset(got):
                persist_event_log({"type":"DOWNLOAD_ONLY_LOCAL_FALLBACK","jobId":job_id,"message":f"{err} -> local fallback Ä‘á»§ scene, tiáº¿p tá»¥c render."})
                update_flow_job(job_id,status="rendering",error=None)
                await render_factory_v2(job_id)
                return
    # Non-factory/manual test or impossible fallback: same-job retry, not terminal history failure.
    if await _requeue_same_job_immediate(job_id,err,stage="media"):
        persist_event_log({"type":"DOWNLOAD_ONLY_RETRY_SAME_JOB","jobId":job_id,"message":err+" -> SAME JOB retry"})
        return
    update_flow_job(job_id,status="failed",error=err)
    if get_content_queue_by_flow(job_id): mark_content_queue_failed(job_id,err,stage="media")
    persist_event_log({"type":"DOWNLOAD_ONLY_TEST_FAILED","jobId":job_id,"message":err})
    await ui_broadcast({"type":"DOWNLOAD_ONLY_TEST_FAILED","jobId":job_id,"ready":ready,"expected":expected_scenes,"error":err})



async def recover_missing_image_downloads(job_id: str, results: list[dict[str, Any]], agent: AgentRuntime, timeout_sec: int=150) -> dict[str,Any]:
    expected:dict[int,list[str]]={}
    for r in results or []:
        sid=int(r.get("sceneId") or (int(r.get("index") or 0)+1)); image=r.get("image") or {}; mid=str(image.get("mediaId") or "").strip()
        if mid: expected[sid]=[mid]
    if not expected: return {"expected":0,"ready":len(_available_images_for_job(job_id)),"requested":0}
    async with _agent_recovery_lock(agent):
        requested=0
        for round_no in range(1,RETRY_MEDIA_ROUNDS+1):
            ready_scenes={int(x.get("scene_id") or 0) for x in _available_images_for_job(job_id)}
            missing={sid:ids for sid,ids in expected.items() if sid not in ready_scenes}
            if not missing: return {"expected":len(expected),"ready":len(expected),"requested":requested,"rounds":round_no-1}
            if not _agent_connected_ready(agent): return {"expected":len(expected),"ready":len(ready_scenes),"requested":requested,"agent_lost":True,"rounds":round_no-1}
            ok,free=_disk_space_ok()
            if not ok: raise RuntimeError(f"Disk trá»‘ng chá»‰ {free:.2f}GB < {RETRY_DISK_MIN_FREE_GB:.1f}GB")
            _touch_agent(agent,"recovering_images",f"img-round:{job_id}:{round_no}:{len(missing)}")
            update_flow_job(job_id,status="downloading_images",error=None)
            persist_event_log({"type":"IMAGE_RECOVERY_ROUND","jobId":job_id,"message":f"IMAGE recovery vÃ²ng {round_no}/{RETRY_MEDIA_ROUNDS} Â· thiáº¿u {sorted(missing)}"})
            for sid,ids in missing.items():
                try: await agent.ws.send_text(dumps({"type":"DOWNLOAD_IMAGE_MEDIA_FILES","jobId":job_id,"sceneId":sid,"mediaIds":ids})); requested+=1
                except Exception: break
            deadline=time.monotonic()+max(20,min(int(timeout_sec),len(missing)*RECOVERY_IMAGE_TIMEOUT_PER_SCENE))
            last=-1
            while time.monotonic()<deadline and _agent_connected_ready(agent):
                scenes={int(x.get("scene_id") or 0) for x in _available_images_for_job(job_id)}; ready=len([sid for sid in expected if sid in scenes])
                if ready!=last: last=ready; _touch_agent(agent,"recovering_images",f"img-ready:{job_id}:{ready}"); persist_event_log({"type":"IMAGE_DOWNLOAD_RECOVERY_PROGRESS","jobId":job_id,"message":f"IMAGE RECOVERY {ready}/{len(expected)} Â· round {round_no}"})
                if ready==len(expected): return {"expected":len(expected),"ready":ready,"requested":requested,"rounds":round_no}
                await asyncio.sleep(0.5)
            if round_no<RETRY_MEDIA_ROUNDS: await asyncio.sleep(_retry_backoff_seconds("media",round_no))
        scenes={int(x.get("scene_id") or 0) for x in _available_images_for_job(job_id)}; ready=len([sid for sid in expected if sid in scenes])
        return {"expected":len(expected),"ready":ready,"requested":requested,"rounds":RETRY_MEDIA_ROUNDS,"agent_lost":not _agent_connected_ready(agent)}


async def recover_missing_video_downloads(job_id: str, results: list[dict[str, Any]], agent: AgentRuntime, timeout_sec: int=240) -> dict[str,Any]:
    expected:dict[int,list[str]]={}
    for r in results or []:
        sid=int(r.get("sceneId") or (int(r.get("index") or 0)+1)); ids=[str(x) for x in (r.get("videoMediaIds") or []) if x]
        if ids: expected[sid]=list(dict.fromkeys(ids))
    if not expected: return {"expected":0,"ready":len(_available_videos_for_job(job_id)),"requested":0}
    async with _agent_recovery_lock(agent):
        requested=0
        for round_no in range(1,RETRY_VIDEO_MEDIA_ROUNDS+1):
            ready_scenes={int(x.get("scene_id") or 0) for x in _available_videos_for_job(job_id)}
            missing={sid:ids for sid,ids in expected.items() if sid not in ready_scenes}
            if not missing: return {"expected":len(expected),"ready":len(expected),"requested":requested,"rounds":round_no-1}
            if not _agent_connected_ready(agent): return {"expected":len(expected),"ready":len(ready_scenes),"requested":requested,"agent_lost":True,"rounds":round_no-1}
            ok,free=_disk_space_ok()
            if not ok: raise RuntimeError(f"Disk trá»‘ng chá»‰ {free:.2f}GB < {RETRY_DISK_MIN_FREE_GB:.1f}GB")
            _touch_agent(agent,"recovering_videos",f"vid-round:{job_id}:{round_no}:{len(missing)}")
            update_flow_job(job_id,status="downloading",error=None)
            persist_event_log({"type":"VIDEO_RECOVERY_ROUND","jobId":job_id,"message":f"VIDEO recovery vÃ²ng {round_no}/{RETRY_VIDEO_MEDIA_ROUNDS} Â· thiáº¿u {sorted(missing)}"})
            for sid,ids in missing.items():
                try: await agent.ws.send_text(dumps({"type":"DOWNLOAD_MEDIA_FILES","jobId":job_id,"sceneId":sid,"mediaIds":ids})); requested+=1
                except Exception: break
            deadline=time.monotonic()+max(30,min(int(timeout_sec),len(missing)*RECOVERY_VIDEO_TIMEOUT_PER_SCENE))
            last=-1
            while time.monotonic()<deadline and _agent_connected_ready(agent):
                scenes={int(x.get("scene_id") or 0) for x in _available_videos_for_job(job_id)}; ready=len([sid for sid in expected if sid in scenes])
                if ready!=last: last=ready; _touch_agent(agent,"recovering_videos",f"vid-ready:{job_id}:{ready}"); persist_event_log({"type":"VIDEO_DOWNLOAD_RECOVERY_PROGRESS","jobId":job_id,"message":f"VIDEO RECOVERY {ready}/{len(expected)} Â· round {round_no}"})
                if ready==len(expected): return {"expected":len(expected),"ready":ready,"requested":requested,"rounds":round_no}
                await asyncio.sleep(0.75)
            if round_no<RETRY_VIDEO_MEDIA_ROUNDS: await asyncio.sleep(_retry_backoff_seconds("media",round_no))
        scenes={int(x.get("scene_id") or 0) for x in _available_videos_for_job(job_id)}; ready=len([sid for sid in expected if sid in scenes])
        return {"expected":len(expected),"ready":ready,"requested":requested,"rounds":RETRY_VIDEO_MEDIA_ROUNDS,"agent_lost":not _agent_connected_ready(agent)}


def _materialize_missing_video_fallbacks(job_id: str, missing_sids: list[int], meta: dict[str, Any]) -> dict[str,Any]:
    """Create local fallback clips without calling Flow Create.

    Preferred fallback is the exact scene's generated image (Ken Burns/beat motion).
    If no image survived, normalize the nearest available video clip as last resort.
    """
    missing=[int(x) for x in missing_sids if int(x)>0]
    if not missing:
        return {"created":[],"failed":[]}
    images={int(x.get("scene_id") or 0):x for x in _available_images_for_job(job_id)}
    videos={int(x.get("scene_id") or 0):x for x in _available_videos_for_job(job_id)}
    expected=max(1,int(meta.get("expectedCount") or max(missing)))
    total=float(meta.get("beatDurationSec") or 15.0)
    per=max(0.7,total/expected)
    preset=normalize_transition_preset(meta.get("motionPreset") or "chaos_mix")
    work=OUTPUT_DIR/"factory_v2"/job_id/"fallback"
    work.mkdir(parents=True,exist_ok=True)
    created=[];failed=[]
    for sid in missing:
        dst=work/f"fallback_{sid:03d}.mp4"
        try:
            source_kind=None
            if sid in images:
                _normalize_image_segment(images[sid],dst,per,sid-1,preset)
                source_kind="scene_image"
            elif videos:
                nearest=min(videos,key=lambda k:abs(int(k)-sid))
                _normalize_video_segment(videos[nearest],dst,per)
                source_kind=f"nearest_video_scene_{nearest}"
            else:
                raise RuntimeError("khÃ´ng cÃ³ image/video source Ä‘á»ƒ fallback")
            if not _valid_local_video_file(str(dst)):
                raise RuntimeError("fallback mp4 validation fail")
            add_asset(
                job_id,sid,"video",local_path=str(dst.resolve()),media_id=None,
                metadata={"source":"LOCAL_VIDEO_FALLBACK","fallback":True,"fallbackSource":source_kind},
            )
            created.append(sid)
            persist_event_log({
                "type":"VIDEO_LOCAL_FALLBACK","jobId":job_id,"sceneId":sid,
                "message":f"Scene {sid}: thiáº¿u Flow video -> local fallback tá»« {source_kind}; KHÃ”NG generate láº¡i.",
            })
        except Exception as exc:
            failed.append({"sceneId":sid,"error":str(exc)})
    return {"created":created,"failed":failed}


async def recover_then_render_factory_v2(job_id: str, results: list[dict[str, Any]], agent: AgentRuntime) -> None:
    job = get_flow_job(job_id) or {}
    scenes = job.get("scenes") or []
    if any(((scene.get("metadata") or {}).get("miniAttachTest")) for scene in scenes):
        rows = ((job.get("result") or {}).get("results") or results or [])
        image_ok = bool(rows) and all(str((row or {}).get("imageState") or "") == "SUCCESS" for row in rows)
        scene_errors = [str((row or {}).get("error") or "").strip() for row in rows if (row or {}).get("error")]
        if image_ok and not scene_errors:
            update_flow_job(job_id, status="done", error=None)
            await ui_broadcast({"type":"MINI_ATTACH_IMAGE_OK","jobId":job_id})
            return
    meta = get_factory_meta(job)
    mode = str(meta.get("mode") or "")

    expected_images,expected_videos=_factory_expected_scene_sets(job)
    if mode in {"IMAGE_BEAT","IMAGE_MIX"}:
        await recover_missing_image_downloads(job_id,results,agent)
        ready_image_scenes={int(x.get("scene_id") or 0) for x in _available_images_for_job(job_id)}; missing_images=sorted(expected_images-ready_image_scenes)
        if missing_images:
            err=f"Thiáº¿u áº£nh scene {missing_images}; cÃ³ {len(ready_image_scenes)}/{len(expected_images)} source há»£p lá»‡"; update_flow_job(job_id,status="failed",error=err)
            if get_content_queue_by_flow(job_id): mark_content_queue_failed(job_id,err,stage="media")
            await ui_broadcast({"type":"FACTORY_RENDER_FAILED","jobId":job_id,"error":err}); return
    if mode in {"IMAGE_TO_VIDEO","IMAGE_MIX"}:
        await recover_missing_video_downloads(job_id,results,agent)
        ready_video_scenes={int(x.get("scene_id") or 0) for x in _available_videos_for_job(job_id)}
        missing_videos=sorted(expected_videos-ready_video_scenes)
        if missing_videos and mode=="IMAGE_MIX":
            # IMAGE_MIX already has a still for every scene. Missing motion clip simply
            # falls back to that still in _render_mixed_media.
            persist_event_log({
                "type":"IMAGE_MIX_VIDEO_DEGRADED","jobId":job_id,
                "message":f"Thiáº¿u motion clip {missing_videos} sau recovery -> dÃ¹ng still scene tÆ°Æ¡ng á»©ng, váº«n render.",
            })
        elif missing_videos:
            fb=await asyncio.to_thread(_materialize_missing_video_fallbacks,job_id,missing_videos,meta)
            ready_video_scenes={int(x.get("scene_id") or 0) for x in _available_videos_for_job(job_id)}
            still_missing=sorted(expected_videos-ready_video_scenes)
            if still_missing:
                err=f"Thiáº¿u video scene {still_missing} sau recovery + local fallback"; update_flow_job(job_id,status="failed",error=err)
                if get_content_queue_by_flow(job_id): mark_content_queue_failed(job_id,err,stage="media")
                await ui_broadcast({"type":"FACTORY_RENDER_FAILED","jobId":job_id,"error":err}); return
    _touch_agent(agent,"rendering_local",f"render:{job_id}")
    await render_factory_v2(job_id)


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
    if mode=="IMAGE_BEAT":
        source_rows=_available_images_for_job(job_id)
    elif mode=="IMAGE_MIX":
        im=_available_images_for_job(job_id);vi=_available_videos_for_job(job_id)
        scene_ids={int(x.get("scene_id") or 0) for x in im+vi if int(x.get("scene_id") or 0)>0}
        source_rows=[{"scene_id":sid} for sid in sorted(scene_ids)]
    else:
        source_rows=_available_videos_for_job(job_id)

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
        hashes = [_make_ahash(Path(r["local_path"])) for r in source_rows if r.get("local_path") and Path(r["local_path"]).exists()]
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
    min_sources = 1 if expected <= 1 else max(2, min(expected, 2))
    passed = score >= 70 and good_duration and len(source_rows) >= min_sources
    result = {"score": score, "passed": passed, "checks": checks, "mode": mode, "preflight": info}
    with conn() as c:
        c.execute("INSERT INTO qc_results(id,job_id,score,passed,details_json,created_at) VALUES(?,?,?,?,?,?)",
                  (f"qc_{uuid.uuid4().hex}", job_id, score, 1 if passed else 0, dumps(result), utcnow()))
    return result


def _normalize_video_segment(row: dict[str, Any], dst: Path, duration: float) -> None:
    src=_asset_stream_source(row)
    if not src:
        raise RuntimeError("Video child khÃ´ng cÃ³ URL/local source")
    cmd=["ffmpeg","-y","-i",src,"-t",f"{duration:.3f}","-an","-vf",
         "scale=1120:1992:force_original_aspect_ratio=increase,crop=1080:1920:x='20+18*sin(t*11)':y='36+15*cos(t*13)',fps=30,setsar=1,eq=contrast=1.025:saturation=1.04,format=yuv420p",
         "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p",str(dst)]
    try:
        _run_cmd(cmd,timeout=240)
        return
    except Exception:
        remote=_safe_remote_media_url(row.get("url"))
        if not remote:
            raise
        # Only fallback-download when direct stream fails and final MP4 cannot otherwise be built.
        lp,size,ctype=download_signed_video_sync(remote,str(row.get("job_id") or "stream_fallback"),int(row.get("scene_id") or 0),str(row.get("media_id") or uuid.uuid4().hex[:8]))
        row["local_path"]=lp
        _run_cmd(["ffmpeg","-y","-i",lp,"-t",f"{duration:.3f}","-an","-vf",
                  "scale=1120:1992:force_original_aspect_ratio=increase,crop=1080:1920:x='20+18*sin(t*11)':y='36+15*cos(t*13)',fps=30,setsar=1,eq=contrast=1.025:saturation=1.04,format=yuv420p",
                  "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p",str(dst)],timeout=240)


def _concat_video_clips(job_id: str, rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("KhÃ´ng tháº¥y ffmpeg trong PATH")
    out_dir=OUTPUT_DIR/"factory_v2"/job_id
    work=out_dir/"work";work.mkdir(parents=True,exist_ok=True)
    total=float(meta.get("beatDurationSec") or 15.0)
    per=max(0.7,total/max(1,len(rows)))
    normalized=[]
    for idx,row in enumerate(rows):
        dst=work/f"clip_{idx+1:03d}.mp4"
        _normalize_video_segment(row,dst,per)
        normalized.append(dst)
    concat_file=work/"concat.txt"
    concat_file.write_text("\n".join(f"file '{x.as_posix()}'" for x in normalized)+"\n",encoding="utf-8")
    raw=work/"video_no_audio.mp4"
    _run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_file),"-t",f"{total:.3f}","-c","copy",str(raw)],timeout=240)
    return _attach_music_or_silence(raw,out_dir/"final.mp4",str(meta.get("musicPath") or ""),target_duration=total)



def _normalize_image_segment(row: dict[str, Any], seg: Path, per: float, idx: int, preset: str) -> None:
    src=_asset_stream_source(row)
    if not src:
        raise RuntimeError("áº¢nh child khÃ´ng cÃ³ URL/local source")
    style,z,x,y,extra=strong_motion_filter_params(preset,idx,per,30)
    vf=(f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s=1080x1920:fps=30,"
        f"eq=contrast=1.035:saturation=1.045{extra},format=yuv420p")
    cmd=["ffmpeg","-y","-loop","1","-framerate","30","-i",src,"-t",f"{per:.3f}","-vf",vf,
         "-an","-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-r","30",str(seg)]
    try:
        _run_cmd(cmd,timeout=240)
        return
    except Exception:
        remote=_safe_remote_media_url(row.get("url"))
        if not remote:
            raise
        lp=cache_image_sync(remote,str(row.get("job_id") or "stream_fallback"),int(row.get("scene_id") or 0),row.get("media_id"))
        if not lp:
            raise
        row["local_path"]=lp
        _run_cmd(["ffmpeg","-y","-loop","1","-framerate","30","-i",lp,"-t",f"{per:.3f}","-vf",vf,
                  "-an","-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-r","30",str(seg)],timeout=240)


def _render_image_beat(job_id: str, rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("KhÃ´ng tháº¥y ffmpeg trong PATH")
    total=float(meta.get("beatDurationSec") or 15.0)
    preset=normalize_transition_preset(meta.get("motionPreset") or "chaos_mix")
    out_dir=OUTPUT_DIR/"factory_v2"/job_id
    work=out_dir/"work";work.mkdir(parents=True,exist_ok=True)
    per=max(0.5,total/max(1,len(rows)))
    segs=[]
    for idx,row in enumerate(rows):
        seg=work/f"seg_{idx+1:03d}.mp4"
        _normalize_image_segment(row,seg,per,idx,preset)
        segs.append(seg)
    concat_file=work/"concat.txt";concat_file.write_text("\n".join(f"file '{x.as_posix()}'" for x in segs)+"\n",encoding="utf-8")
    raw=work/"video_no_audio.mp4"
    _run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_file),"-t",f"{total:.3f}","-c","copy",str(raw)],timeout=240)
    return _attach_music_or_silence(raw,out_dir/"final.mp4",str(meta.get("musicPath") or ""),target_duration=total)


def _render_mixed_media(job_id: str, images: list[dict[str, Any]], videos: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    """IMAGE_MIX = still shots + short natural-motion clips in one ~15s timeline."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("KhÃ´ng tháº¥y ffmpeg trong PATH")
    total=float(meta.get("beatDurationSec") or 15.0)
    expected=max(1,int(meta.get("expectedCount") or len(images) or 10))
    preset=normalize_transition_preset(meta.get("motionPreset") or "chaos_mix")
    image_map={int(x.get("scene_id") or 0):x for x in images}
    video_map={int(x.get("scene_id") or 0):x for x in videos}
    out_dir=OUTPUT_DIR/"factory_v2"/job_id
    work=out_dir/"work";work.mkdir(parents=True,exist_ok=True)
    per=max(0.55,total/expected)
    segs=[]
    used=0
    for sid in range(1,expected+1):
        row=video_map.get(sid) or image_map.get(sid)
        if not row:
            continue
        seg=work/f"mix_{sid:03d}.mp4"
        if sid in video_map:
            _normalize_video_segment(row,seg,per)
        else:
            _normalize_image_segment(row,seg,per,sid-1,preset)
        segs.append(seg);used+=1
    if used<2:
        raise RuntimeError(f"IMAGE_MIX chá»‰ cÃ³ {used} scene usable")
    concat_file=work/"concat_mix.txt";concat_file.write_text("\n".join(f"file '{x.as_posix()}'" for x in segs)+"\n",encoding="utf-8")
    raw=work/"video_no_audio.mp4"
    _run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_file),"-t",f"{total:.3f}","-c","copy",str(raw)],timeout=240)
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
        await ui_broadcast({"type":"AUTO_PUBLISH_SKIPPED","jobId":job_id,"error":"Page Profile chÆ°a map Facebook Page/token"})
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
    job=get_flow_job(job_id) or {}; meta=get_factory_meta(job); mode=str(meta.get("mode") or "IMAGE_BEAT")
    expected_images,expected_videos=_factory_expected_scene_sets(job)
    last_exc:Exception|None=None
    for render_attempt in range(1,RETRY_RENDER_ATTEMPTS+1):
        try:
            ok_disk,free=_disk_space_ok()
            if not ok_disk: raise RuntimeError(f"Disk trá»‘ng chá»‰ {free:.2f}GB < {RETRY_DISK_MIN_FREE_GB:.1f}GB")
            update_flow_job(job_id,status="rendering",error=None)
            await ui_broadcast({"type":"FACTORY_RENDER_STARTED","jobId":job_id,"attempt":render_attempt})
            if render_attempt>1:
                persist_event_log({"type":"FACTORY_RENDER_RETRY","jobId":job_id,"message":f"Render retry {render_attempt}/{RETRY_RENDER_ATTEMPTS} Â· SAME media"})
                # Delete only derived work files, never source assets.
                work=OUTPUT_DIR/"factory_v2"/job_id/"work"
                if work.exists(): await asyncio.to_thread(shutil.rmtree,work,True)
            if mode=="IMAGE_TO_VIDEO":
                rows=_available_videos_for_job(job_id); got={int(x.get("scene_id") or 0) for x in rows}; missing=sorted(expected_videos-got)
                if missing: raise RuntimeError(f"IMAGE_TO_VIDEO thiáº¿u clip scene {missing}")
                final=await asyncio.to_thread(_concat_video_clips,job_id,rows,meta)
            elif mode=="IMAGE_MIX":
                images=_available_images_for_job(job_id); videos=_available_videos_for_job(job_id)
                got_i={int(x.get("scene_id") or 0) for x in images}; got_v={int(x.get("scene_id") or 0) for x in videos}
                mi=sorted(expected_images-got_i); mv=sorted(expected_videos-got_v)
                if mi: raise RuntimeError(f"IMAGE_MIX thiáº¿u image={mi}")
                if mv:
                    persist_event_log({"type":"IMAGE_MIX_RENDER_STILL_FALLBACK","jobId":job_id,"message":f"Render: motion scene {mv} dÃ¹ng still fallback."})
                final=await asyncio.to_thread(_render_mixed_media,job_id,images,videos,meta)
            else:
                rows=_available_images_for_job(job_id); got={int(x.get("scene_id") or 0) for x in rows}; missing=sorted(expected_images-got)
                if missing: raise RuntimeError(f"IMAGE_BEAT thiáº¿u áº£nh scene {missing}")
                final=await asyncio.to_thread(_render_image_beat,job_id,rows,meta)
            if not await asyncio.to_thread(_valid_local_video_file,final): raise RuntimeError("Final MP4 render xong nhÆ°ng ffprobe/file validation fail")
            asset_id=add_asset(job_id,0,"final_video",local_path=final,title=f"{meta.get('profileName','Factory')} Â· {mode}",metadata={"source":"FACTORY_V2","mode":mode,"renderAttempt":render_attempt})
            update_flow_job(job_id,status="qc",error=None)
            qc=await asyncio.to_thread(qc_video_sync,job_id,final)
            technical=not bool((qc.get("checks") or {}).get("resolution",{}).get("ok")) or not bool((qc.get("checks") or {}).get("duration",{}).get("ok")) or not bool((qc.get("checks") or {}).get("file",{}).get("ok"))
            if not qc.get("passed") and technical and render_attempt<RETRY_RENDER_ATTEMPTS:
                last_exc=RuntimeError(f"QC technical fail score={qc.get('score')}")
                persist_event_log({"type":"QC_TECHNICAL_RETRY","jobId":job_id,"message":f"QC technical fail Â· tá»± render láº¡i {render_attempt+1}/{RETRY_RENDER_ATTEMPTS}"})
                continue
            update_flow_job(job_id,status="done" if qc.get("passed") else "qc_failed",error=None if qc.get("passed") else f"QC score {qc.get('score')}")
            await ui_broadcast({"type":"FACTORY_VIDEO_READY","jobId":job_id,"assetId":asset_id,"localPath":final,"qc":qc})
            queue_item=get_content_queue_by_flow(job_id)
            if queue_item:
                if qc.get("passed"):
                    caption=str(meta.get("caption") or ""); tags=meta.get("hashtags") or []
                    if tags and not any(str(t) in caption for t in tags): caption=(caption+"\n\n"+" ".join(str(t) for t in tags)).strip()
                    update_content_queue_by_flow(job_id,status="ready",video_path=final,title=str(meta.get("title") or meta.get("profileName") or ""),description=caption,error=None,retry_after=None,failure_class=None)
                    _clear_retry_state(job_id)
                    await ui_broadcast({"type":"SCHEDULER_VIDEO_READY","jobId":job_id,"profileId":queue_item.get("page_profile_id"),"localPath":final})
                else:
                    mark_content_queue_failed(job_id,f"QC score {qc.get('score')}",stage="qc")
            else:
                await maybe_auto_publish_factory(job_id,final,qc)
            return
        except Exception as exc:
            last_exc=exc
            if render_attempt<RETRY_RENDER_ATTEMPTS:
                await asyncio.sleep(_retry_backoff_seconds("render",render_attempt))
                continue
            err=f"Factory render lá»—i sau {RETRY_RENDER_ATTEMPTS} attempt: {exc}"
            update_flow_job(job_id,status="failed",error=err)
            if get_content_queue_by_flow(job_id): mark_content_queue_failed(job_id,err,stage="render")
            await ui_broadcast({"type":"FACTORY_RENDER_FAILED","jobId":job_id,"error":str(exc)})
            return


async def _finalize_factory_owned(job_id: str, results: list[dict[str, Any]], agent: AgentRuntime) -> None:
    FACTORY_FINALIZE_IN_FLIGHT.add(job_id)
    try:
        agent.busy=True; agent.job_id=job_id; _touch_agent(agent,"finalizing",f"finalize:{job_id}")
        await recover_then_render_factory_v2(job_id,results,agent)
        if str((get_flow_job(job_id) or {}).get("status") or "")=="failed":
            FACTORY_RESUME_RETRY_AFTER[job_id]=time.monotonic()+SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES*60
        else:
            FACTORY_RESUME_RETRY_AFTER.pop(job_id,None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        err=f"Factory finalize lá»—i: {exc}"; update_flow_job(job_id,status="failed",error=err)
        FACTORY_RESUME_RETRY_AFTER[job_id]=time.monotonic()+SCHEDULER_AUTO_RECOVERY_COOLDOWN_MINUTES*60
        if get_content_queue_by_flow(job_id): mark_content_queue_failed(job_id,err,stage="runtime")
        persist_event_log({"type":"FACTORY_FINALIZE_FAILED","jobId":job_id,"message":err})
    finally:
        FACTORY_FINALIZE_IN_FLIGHT.discard(job_id)
        if agent.job_id==job_id:
            agent.busy=False; agent.job_id=None; _touch_agent(agent,"idle",f"finalized:{job_id}")
        await dispatch_jobs()
        item=get_content_queue_by_flow(job_id)
        if item and item.get("page_profile_id"):
            spawn(scheduler_fill_profile(str(item["page_profile_id"])))


async def _finalize_video_test_owned(job_id: str, results: list[dict[str, Any]], agent: AgentRuntime) -> None:
    FACTORY_FINALIZE_IN_FLIGHT.add(job_id)
    try:
        agent.busy=True; agent.job_id=job_id; _touch_agent(agent,"finalizing",f"video-test:{job_id}")
        await recover_missing_image_downloads(job_id,results,agent)
        await render_video_test(job_id)
    finally:
        FACTORY_FINALIZE_IN_FLIGHT.discard(job_id)
        if agent.job_id==job_id:
            agent.busy=False; agent.job_id=None; _touch_agent(agent,"idle",f"video-test-done:{job_id}")
        await dispatch_jobs()


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
            inputs.append({"path": str(p), "name": p.stem or role, "role": role, "videoReference": False})
    return inputs


def build_factory_scenes(req: FactoryBatchRequest) -> list[dict[str, Any]]:
    # Manual batch test uses the same Vietnam lifestyle defaults as Auto Factory.
    poses = list(DEFAULT_POSES)
    scenes_bg = list(DEFAULT_BACKGROUNDS)
    outfit_texts = list(DEFAULT_OUTFITS)
    refs = normalize_input_images(req.persona_path, req.outfit_path)
    scenes: list[dict[str, Any]] = []
    for i in range(req.count):
        prompt = (
            f"{req.base_prompt}. Adult model, age 21+. Theme: {req.theme}. "
            f"Scene: {random.choice(scenes_bg)}. Pose: {random.choice(poses)}. "
            f"Wardrobe: {random.choice(outfit_texts)}. "
            "Natural body proportions, realistic skin texture, Vietnamese lifestyle setting, no generic gym unless explicitly requested, no text, no watermark."
        )
        scenes.append({
            "sceneId": i + 1, "imagePrompt": prompt, "videoPrompt": "", "inputImages": refs,
            "metadata": {"pageProfile": req.page_profile,"theme": req.theme,"variation": i + 1,"adultOnly": True},
        })
    return scenes


def build_video_test_scenes(req: VideoTestRequest) -> list[dict[str, Any]]:
    refs = normalize_input_images(req.person_path, req.outfit_path)
    poses = list(DEFAULT_POSES)
    backgrounds = list(DEFAULT_BACKGROUNDS)
    outfits = list(DEFAULT_OUTFITS)
    motion = normalize_transition_preset(req.motion_preset)
    settings = {
        "durationSec": float(req.duration_sec), "motionPreset": motion,
        "musicPath": (req.music_path or "").strip() or None, "width": 1080, "height": 1920, "fps": 30,
    }
    scenes: list[dict[str, Any]] = []
    for i in range(req.image_count):
        outfit_clause = "Keep the outfit/reference garment faithful to the uploaded outfit image." if req.outfit_path else f"Wardrobe: {outfits[i % len(outfits)]}."
        identity_clause = "Keep exactly the same adult woman's identity, face, hair and body proportions as the person reference." if req.person_path else "Use one consistent adult woman identity across this batch."
        prompt = (
            f"{req.prompt}. {identity_clause} Adult model, age 21+. "
            f"Scene variation {i+1}: {backgrounds[i % len(backgrounds)]}; pose: {poses[i % len(poses)]}. "
            f"{outfit_clause} Photorealistic, realistic skin texture, natural smartphone photography, "
            "vertical 9:16 composition, full body or three-quarter body, Vietnamese lifestyle setting, tasteful social media content, no text, no watermark."
        )
        scenes.append({
            "sceneId": i + 1,"imagePrompt": prompt,"videoPrompt": "","inputImages": refs,
            "metadata": {"mode":"video_test","variation":i+1,"adultOnly":True,"videoTest":settings},
        })
    return scenes


def _run_cmd(cmd: list[str], timeout: int = 180) -> None:
    cp = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "")[-5000:]
        raise RuntimeError(f"Command lá»—i ({cp.returncode}): {' '.join(cmd[:8])} ...\n{tail}")


def _video_test_image_rows(job_id: str) -> list[dict[str, Any]]:
    return _available_images_for_job(job_id)


def render_video_test_sync(job_id: str) -> str:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("KhÃ´ng tháº¥y ffmpeg trong PATH")
    job = get_flow_job(job_id)
    if not job:
        raise RuntimeError("KhÃ´ng tháº¥y video test job")
    scenes = job.get("scenes") or []
    meta = ((scenes[0].get("metadata") or {}).get("videoTest") or {}) if scenes else {}
    total = float(meta.get("durationSec") or 10.0)
    preset = normalize_transition_preset(meta.get("motionPreset") or "chaos_mix")
    width, height, fps = int(meta.get("width") or 1080), int(meta.get("height") or 1920), int(meta.get("fps") or 30)
    music_path = str(meta.get("musicPath") or "").strip()

    images = _video_test_image_rows(job_id)
    if len(images) < 2:
        raise RuntimeError(f"Chá»‰ cache Ä‘Æ°á»£c {len(images)} áº£nh; cáº§n Ã­t nháº¥t 2 áº£nh Ä‘á»ƒ render video")

    out_dir = OUTPUT_DIR / "video_tests" / job_id
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    per = max(0.45, total / len(images))
    segments: list[Path] = []

    for idx, row in enumerate(images):
        seg = work / f"seg_{idx+1:03d}.mp4"
        _normalize_image_segment(row,seg,per,idx,preset)
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
        update_flow_job(job_id, status="failed", error=f"Render video lá»—i: {exc}")
        await ui_broadcast({"type": "VIDEO_RENDER_FAILED", "jobId": job_id, "error": str(exc)})


async def _dispatch_ack_watchdog(agent_id: str, job_id: str) -> None:
    await asyncio.sleep(DISPATCH_ACK_TIMEOUT_SEC)
    agent=AGENTS.get(agent_id); job=get_flow_job(job_id) or {}
    if not agent or agent.job_id!=job_id or str(job.get("status") or "")!="dispatching": return
    persist_event_log({"type":"DISPATCH_ACK_TIMEOUT","agentId":agent_id,"jobId":job_id,"message":f"Không có FLOW_JOB_ACCEPTED sau {DISPATCH_ACK_TIMEOUT_SEC}s · requeue SAME job."})
    _schedule_factory_retry(job_id,f"Dispatch ACK timeout {DISPATCH_ACK_TIMEOUT_SEC}s",stage="dispatch",force_delay=3)
    agent.busy=False; agent.job_id=None; agent.ready=False; _touch_agent(agent,"idle",f"ack-timeout:{job_id}")


async def dispatch_jobs() -> None:
    async with DISPATCH_LOCK:
        idle = [
            a for a in AGENTS.values()
            if not a.busy and a.ready and a.phase=="idle" and extension_version_compatible(a.version)
        ]
        if not idle:
            return
        with conn() as c:
            queued = c.execute(
                "SELECT id,flow_json,scenes_json FROM flow_jobs WHERE status='queued' ORDER BY created_at ASC LIMIT ?",
                (len(idle),),
            ).fetchall()
        for agent, row in zip(idle, queued):
            job_id = row["id"]
            payload = {
                "type": "RUN_FLOW_JOB",
                "jobId": job_id,
                "flow": loads(row["flow_json"], {}),
                "scenes": loads(row["scenes_json"], []),
            }
            try:
                agent.busy=True; agent.job_id=job_id; _touch_agent(agent,"dispatching",f"dispatch:{job_id}")
                update_flow_job(job_id,status="dispatching",agent_id=agent.id)
                await agent.ws.send_text(dumps(payload))
                await ui_broadcast({"type":"JOB_DISPATCHED","jobId":job_id,"agentId":agent.id})
                spawn(_dispatch_ack_watchdog(agent.id,job_id))
            except Exception as exc:
                agent.busy=False; agent.job_id=None; agent.ready=False; _touch_agent(agent,"idle",f"dispatch-error:{job_id}")
                update_flow_job(job_id,status="queued",agent_id=None,error=f"Dispatch lá»—i: {exc}")


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



def download_signed_video_sync(url: str, job_id: str, scene_id: int, media_id: str) -> tuple[str, int, str]:
    """Stream a short-lived signed Flow CDN URL directly into local storage."""
    from urllib.parse import urlparse
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"https", "http"}:
        raise RuntimeError("Signed video URL khÃ´ng pháº£i HTTP/HTTPS.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Tá»« chá»‘i signed video URL HTTP khÃ´ng-local.")

    safe_job = re.sub(r"[^A-Za-z0-9._-]+", "_", str(job_id))[:100]
    safe_mid = re.sub(r"[^A-Za-z0-9._-]+", "_", str(media_id))[:80]
    out_dir = OUTPUT_DIR / "flow_downloads" / safe_job
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"scene_{int(scene_id):03d}_{safe_mid}.server.part"
    final = out_dir / f"scene_{int(scene_id):03d}_{safe_mid}.mp4"
    tmp.unlink(missing_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0 FlowContentFactory/2.14.29",
        "Accept": "video/mp4,video/*;q=0.9,application/octet-stream;q=0.8,*/*;q=0.5",
    }
    total = 0
    content_type = ""
    try:
        with requests.get(str(url), stream=True, allow_redirects=True, timeout=(15, 180), headers=headers) as r:
            r.raise_for_status()
            content_type = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type.startswith("text/") or "json" in content_type or "html" in content_type:
                raise RuntimeError(f"Signed URL tráº£ content-type khÃ´ng pháº£i video: {content_type or 'unknown'}")
            expected = int(r.headers.get("content-length") or 0)
            with tmp.open("wb") as f:
                for chunk in r.iter_content(512 * 1024):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
            if total <= 4096:
                raise RuntimeError(f"Video táº£i vá» quÃ¡ nhá»: {total} bytes")
            if expected > 0 and total != expected:
                raise RuntimeError(f"Video táº£i chÆ°a Ä‘á»§: {total}/{expected} bytes")
        final.unlink(missing_ok=True)
        tmp.replace(final)
        return str(final.resolve()), total, content_type or "video/mp4"
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

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
    asset_id = f"asset_{uuid.uuid4().hex}"
    with conn() as c:
        c.execute(
            "INSERT INTO assets(id,job_id,scene_id,kind,url,local_path,media_id,title,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (asset_id, job_id, scene_id, kind, url, local_path, media_id, title, dumps(metadata or {}), utcnow()),
        )
    return asset_id


async def process_flow_result(job_id: str, message: dict[str, Any], agent: AgentRuntime) -> None:
    replayed=bool(message.get("replayed"))
    if agent.job_id and agent.job_id!=job_id and not replayed:
        persist_event_log({"type":"STALE_FLOW_RESULT_IGNORED","agentId":agent.id,"jobId":job_id,"message":f"Agent Ä‘ang sá»Ÿ há»¯u {agent.job_id}; bá» result stale cá»§a {job_id}."}); return
    existing=get_flow_job(job_id)
    if not existing:
        persist_event_log({"type":"UNKNOWN_FLOW_RESULT_IGNORED","agentId":agent.id,"jobId":job_id,"message":"KhÃ´ng tháº¥y flow_job trong DB."}); return
    if replayed and (existing.get("result") or {}).get("ok") is True and str(existing.get("status") or "") not in {"interrupted","failed","partial_failed"}:
        persist_event_log({"type":"REPLAY_FLOW_RESULT_DUPLICATE","agentId":agent.id,"jobId":job_id,"message":"Result replay Ä‘Ã£ cÃ³; bá» duplicate."}); return
    _touch_agent(agent,"finalizing" if str(existing.get("kind") or "").startswith("factory_v2_") else "running",f"result:{job_id}")
    ok = bool(message.get("ok"))
    results = message.get("results") or []
    result_payload = {"ok": ok, "results": results, "failures": message.get("failures") or []}
    job = get_flow_job(job_id) or {}
    is_video_test = job.get("kind") == "video_test"
    is_factory_v2 = str(job.get("kind") or "").startswith("factory_v2_")
    is_persona_angle_pack = job.get("kind") in {"persona_angle_pack", "persona_angle"}
    needs_local_render = is_video_test or is_factory_v2
    update_flow_job(
        job_id,
        status=("flow_done" if ok and needs_local_render else "done" if ok else "partial_failed"),
        result_json=dumps(result_payload),
        error=message.get("error"),
    )
    if not ok and get_content_queue_by_flow(job_id):
        err_text=str(message.get("error") or "Flow job partial_failed")
        low=err_text.lower()
        busy_collision=(
            "Ä‘ang cháº¡y má»™t batch khÃ¡c" in low
            or "running another batch" in low
            or "extension busy" in low
            or "batch already running" in low
        )
        if busy_collision:
            # This is orchestration contention, not a content failure.
            update_flow_job(job_id,status="interrupted",error=err_text,agent_id=None)
            spawn(_requeue_same_job_immediate(job_id,err_text,stage="dispatch"))
        else:
            mark_content_queue_failed(job_id,err_text,stage="generation")
    for r in results:
        scene_id = int(r.get("sceneId") or (int(r.get("index") or 0) + 1))
        input_refs = r.get("inputRefs") or []
        if input_refs:
            verified = bool(r.get("refVerified")) and all(bool(x.get("verified")) for x in input_refs)
            persist_event_log({
                "type": "REF_VERIFY_OK" if verified else "REF_VERIFY_MISSING",
                "jobId": job_id, "sceneId": scene_id,
                "message": f"áº¢nh máº«u Flow {'Ä‘Ã£ xÃ¡c nháº­n' if verified else 'CHÆ¯A xÃ¡c nháº­n'} Â· {len(input_refs)} ref Â· " + ", ".join(str(x.get('role') or 'reference') for x in input_refs),
            })
        image = r.get("image") or {}
        if image.get("mediaId") or image.get("url"):
            # Factory/video-test previews + FFmpeg use remote image URL first.
            # Persona angles must remain local because they become long-lived identity references.
            cache_required = is_persona_angle_pack or not (is_factory_v2 or is_video_test)
            local_path = None
            if cache_required:
                local_path = await asyncio.to_thread(
                    cache_image_sync,
                    str(image.get("url") or ""),
                    job_id,
                    scene_id,
                    image.get("mediaId"),
                )
            asset_id = add_asset(
                job_id,
                scene_id,
                "image",
                url=image.get("url"),
                local_path=local_path,
                media_id=image.get("mediaId"),
                title=image.get("title"),
                metadata={"imageState": r.get("imageState"), "source": "FLOW_JOB_RESULT"},
            )
            await ui_broadcast({
                "type":"IMAGE_READY","jobId":job_id,"sceneId":scene_id,"assetId":asset_id,
                "mediaId":image.get("mediaId"),"url":image.get("url"),"localPath":local_path,
                "title":image.get("title"),
            })
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
                add_asset(
                    job_id,
                    scene_id,
                    "video",
                    local_path=dl_path,
                    media_id=dl_media,
                    metadata={"source": "FLOW_JOB_RESULT"},
                )
    await ui_broadcast({"type": "FLOW_JOB_RESULT", "jobId": job_id, "ok": ok})
    if is_persona_angle_pack:
        scenes = job.get("scenes") or []
        profile_id = str(((scenes[0].get("metadata") or {}) if scenes else {}).get("profileId") or "")
        if profile_id:
            profile_after = get_page_profile(profile_id) or {}
            await ui_broadcast({"type":"PERSONA_PACK_READY" if profile_after.get("persona_pack_ready") else "PERSONA_PACK_PARTIAL","jobId":job_id,"profileId":profile_id,"angleCount":profile_after.get("persona_angle_count",0),"ok":ok})
    if ok and is_factory_v2:
        if job_id not in FACTORY_FINALIZE_IN_FLIGHT:
            FACTORY_FINALIZE_IN_FLIGHT.add(job_id); spawn(_finalize_factory_owned(job_id,results,agent))
        return
    if ok and is_video_test:
        if job_id not in FACTORY_FINALIZE_IN_FLIGHT:
            FACTORY_FINALIZE_IN_FLIGHT.add(job_id); spawn(_finalize_video_test_owned(job_id,results,agent))
        return
    if agent.job_id==job_id:
        agent.busy=False; agent.job_id=None; _touch_agent(agent,"idle",f"flow-result-release:{job_id}")
    await dispatch_jobs()


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
        # TrÃ¡nh Page Profile giá»¯ mapping tá»›i Page Ä‘Ã£ xÃ³a/token khÃ´ng cÃ²n.
        c.execute("UPDATE page_profiles SET facebook_page_id=NULL,updated_at=? WHERE facebook_page_id=?", (utcnow(), page_id))
    return {"ok": True, "page_id": page_id, "ignored": bool(ignore)}


def keep_only_fb_page(page_id: str) -> dict[str, Any]:
    if not get_fb_page_secret(page_id):
        raise ValueError("KhÃ´ng tÃ¬m tháº¥y Page cáº§n giá»¯")
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
        return {"ffprobe": False, "warning": "ffprobe khÃ´ng cÃ³ trong PATH; bá» qua kiá»ƒm tra video."}
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
        warnings.append("Facebook Reel API yÃªu cáº§u tá»‘i thiá»ƒu 540x960.")
    if ratio and abs(ratio - 9 / 16) > 0.03:
        warnings.append(f"Tá»‰ lá»‡ hiá»‡n táº¡i {width}x{height} khÃ´ng gáº§n 9:16.")
    if duration and not (4 <= duration <= 60):
        warnings.append(f"Duration {duration:.2f}s náº±m ngoÃ i khoáº£ng 4-60s cá»§a Reels Publishing API.")
    return {
        "ffprobe": True,
        "width": width,
        "height": height,
        "duration": duration,
        "size": int(fmt.get("size") or video_path.stat().st_size),
        "warnings": warnings,
    }


def create_publish_job(req: FacebookPublishRequest, dry_run: bool) -> str:
    pid = f"fb_{server_stamp()}_{uuid.uuid4().hex[:8]}"
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


def _classify_publish_failure(error: str) -> tuple[str,bool]:
    text=str(error or "").lower()
    if any(x in text for x in AUTH_FAILURE_MARKERS): return "auth",False
    if any(x in text for x in RATE_LIMIT_FAILURE_MARKERS): return "rate_limit",True
    if any(x in text for x in NETWORK_FAILURE_MARKERS) or any(x in text for x in TIMEOUT_FAILURE_MARKERS): return "network",True
    return "publish",True


def _publish_retry_delay(attempt: int, failure_class: str) -> int:
    if failure_class=="rate_limit": return min(3600,300*(2**max(0,attempt-1)))
    if failure_class=="network": return min(900,30*(2**max(0,attempt-1)))
    return min(1800,60*(2**max(0,attempt-1)))


def run_fb_publish(job_id: str) -> None:
    with conn() as c:
        job = c.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return
    job = dict(job)
    page = get_fb_page_secret(job["page_id"])
    if not page:
        update_publish_job(job_id, status="failed", error="KhÃ´ng tÃ¬m tháº¥y Facebook Page/token")
        return
    path = Path(job["video_path"])
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        update_publish_job(job_id, status="failed", error=f"KhÃ´ng tháº¥y video: {path}")
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
            raise RuntimeError(f"Facebook khÃ´ng tráº£ video_id/upload_url: {start}")

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
        err=str(exc); failure_class,retryable=_classify_publish_failure(err); attempt=int(job.get("retry_count") or 0)+1
        retry_after=None
        if retryable and attempt<=RETRY_PUBLISH_MAX:
            delay=_publish_retry_delay(attempt,failure_class); retry_after=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(timespec="seconds")
            update_publish_job(job_id,status="retry_wait",error=err,retry_count=attempt,retry_after=retry_after,failure_class=failure_class)
        else:
            update_publish_job(job_id,status="failed",error=err,retry_count=attempt,failure_class=failure_class)


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


_STOP_ALL_ALREADY_SENT = False
_STOP_ALL_LAST_COUNT = 0


async def _send_stop_all_to_agents(reason: str = "server_shutdown") -> int:
    """Best-effort final command before the server closes its websocket side."""
    global _STOP_ALL_ALREADY_SENT, _STOP_ALL_LAST_COUNT
    if _STOP_ALL_ALREADY_SENT:
        return _STOP_ALL_LAST_COUNT
    agents = list(AGENTS.values())
    if not agents:
        return 0
    payload = dumps({"type":"STOP_ALL","reason":reason,"policy":"SERVER_OFF_FAILSAFE","clearQueue":True,"detachDebugger":True,"stopBrowserActions":True,"ts":utcnow()})
    sent = 0
    for agent in agents:
        try:
            await asyncio.wait_for(agent.ws.send_text(payload), timeout=1.0)
            sent += 1
        except Exception:
            pass
    # Give MV3 service worker one short turn to process STOP_ALL and reply ACK.
    if sent:
        _STOP_ALL_ALREADY_SENT = True
        _STOP_ALL_LAST_COUNT = sent
        await asyncio.sleep(0.25)
    return sent


def _clear_startup_logs() -> None:
    # Cosmetic cleanup must not be allowed to block ASGI startup.
    try:
        with conn(timeout=2.0) as c:
            c.execute("DELETE FROM event_logs")
    except Exception as exc:
        print(f"[STARTUP] Bá» qua xÃ³a log cÅ©: {exc}", flush=True)


def _assert_ws_route_integrity(app: FastAPI) -> dict[str, Any]:
    ws_routes=[]
    for route in getattr(app,"routes",[]):
        path=str(getattr(route,"path","") or "")
        endpoint=getattr(route,"endpoint",None)
        if path=="/ws":
            ws_routes.append({
                "path":path,
                "endpoint":getattr(endpoint,"__name__",str(endpoint)),
                "routeClass":route.__class__.__name__,
            })
    if len(ws_routes)!=1:
        raise RuntimeError(f"WS ROUTE INVALID: cáº§n Ä‘Ãºng 1 /ws route, hiá»‡n cÃ³ {ws_routes}")
    row=ws_routes[0]
    if row["endpoint"]!="extension_ws":
        raise RuntimeError(
            f"WS ROUTE INVALID: /ws Ä‘ang trá» tá»›i {row['endpoint']} thay vÃ¬ extension_ws. "
            "KhÃ´ng khá»Ÿi Ä‘á»™ng server Ä‘á»ƒ trÃ¡nh extension disconnect 1006."
        )
    return row


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_task = None
    try:
        print("[STARTUP 1/5] Kiá»ƒm tra / nÃ¢ng cáº¥p SQLite...", flush=True)
        try:
            # Run synchronous SQLite setup off the event loop and fail clearly instead
            # of sitting forever at 'Waiting for application startup'.
            await asyncio.wait_for(asyncio.to_thread(init_db), timeout=12.0)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("SQLite startup quÃ¡ 12 giÃ¢y. CÃ³ thá»ƒ cÃ²n server cÅ© Ä‘ang giá»¯ DB. HÃ£y Ä‘Ã³ng process Python cÅ© rá»“i cháº¡y láº¡i.") from exc
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise RuntimeError("SQLite Ä‘ang bá»‹ khÃ³a bá»Ÿi process server khÃ¡c. ÄÃ³ng server/Python cÅ© rá»“i cháº¡y láº¡i.") from exc
            raise
        print("[STARTUP 2/5] SQLite OK", flush=True)

        ws_integrity=_assert_ws_route_integrity(app)
        print(f"[STARTUP 3/5] WS ROUTE OK Â· /ws -> {ws_integrity['endpoint']}", flush=True)

        # Do not block startup on log cleanup or scheduler reconciliation.
        await asyncio.to_thread(_clear_startup_logs)
        print("[STARTUP 4/5] Khá»Ÿi táº¡o scheduler ná»n...", flush=True)
        scheduler_task = asyncio.create_task(scheduler_loop(), name="publish-scheduler")
        spawn(asyncio.to_thread(persist_event_log, {
            "type":"SERVER_STARTED",
            "message":f"Log má»›i Â· timezone {SERVER_TZ_NAME} (+07:00)",
            "serverVersion":"2.14.29",
            "timezone":SERVER_TZ_NAME,
            "utcOffset":"+07:00",
        }))
        persist_event_log({
            "type":"WS_ROUTE_SELFTEST_OK",
            "message":"/ws -> extension_ws Â· bridge route integrity PASS",
            "serverVersion":"2.14.29",
        })
        print(f"[STARTUP 5/5] READY Â· WS=/ws->extension_ws Â· timezone={SERVER_TZ_NAME} (+07:00) Â· now={server_now_iso()}", flush=True)
        yield
    finally:
        print("[SHUTDOWN] Gá»­i STOP_ALL tá»›i extension...", flush=True)
        try:
            sent = await _send_stop_all_to_agents("server_shutdown")
            print(f"[SHUTDOWN] STOP_ALL sent={sent}", flush=True)
        except Exception as exc:
            print(f"[SHUTDOWN] STOP_ALL lá»—i: {exc}", flush=True)

        # Stop scheduler and every background coroutine owned by this process.
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        for task in list(BACKGROUND_TASKS):
            task.cancel()
        if BACKGROUND_TASKS:
            await asyncio.gather(*list(BACKGROUND_TASKS), return_exceptions=True)
        print("[SHUTDOWN] HoÃ n táº¥t.", flush=True)


app = FastAPI(title=APP_NAME, version="2.14.29", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")


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
                "soft_disabled_count": 0,
                "strict_mode": False,
                "compact_mode": True,
                "allowed_ids": [x["id"] for x in ROUTER9_ALLOWED_MODELS]}
    except Exception as exc:
        return {"ok": False, "enabled": router9_enabled(), "base_url": ROUTER9_BASE_URL, "models": [], "error": str(exc)}


@app.post("/api/ai/models/test")
def ai_model_test(req: AiModelTestRequest):
    if not router9_enabled():
        raise HTTPException(400, "ROUTER9_API_KEY chÆ°a cáº¥u hÃ¬nh")
    return test_router9_model_sync(req.model_id)


@app.post("/api/ai/models/test-all")
def ai_models_test_all(background_tasks: BackgroundTasks):
    if not router9_enabled():
        raise HTTPException(400, "ROUTER9_API_KEY chÆ°a cáº¥u hÃ¬nh")
    rows = router9_models()
    ids = [m["id"] for m in rows if m["family"] in {"gpt","gemini"}]
    if not ids:
        raise HTTPException(400, "9router khÃ´ng tráº£ GPT/Gemini model nÃ o")
    for mid in ids:
        upsert_ai_model_status(mid, _model_family(mid), "testing", latency_ms=None, error=None)
    background_tasks.add_task(test_router9_models_background, ids)
    return {"ok": True, "testing": len(ids), "models": ids}




@app.post("/api/ai/models/clear-errors")
def ai_models_clear_errors():
    d = disable_failed_router9_models()
    return {"ok": True, **d, "disabled": d["soft_disabled"] + d["hard_disabled"],
            "message": f"ÄÃ£ clear {d['soft_disabled']} lá»—i táº¡m vÃ  permanent-block {d['hard_disabled']} model khÃ´ng há»— trá»£. GitHub luÃ´n bá»‹ cháº·n."}


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
        "time": server_now_iso(),
        "timezone": SERVER_TZ_NAME,
        "utc_offset": "+07:00",
        "ws_route": "/ws",
        "ws_endpoint": "extension_ws",
        "agent_bridge_port": AGENT_PORT,
        "web_port": WEB_PORT,
        "agents_connected": len(AGENTS),
        "agents_idle": sum(1 for a in AGENTS.values() if not a.busy),
        "page_profiles": profile_count,
        "server_version": "2.14.29",
        "required_extension_version": MIN_EXTENSION_VERSION,
        "compatible_agents": len(compatible_agents()),
        "graph_version": FB_GRAPH_VERSION,
        "web_port": WEB_PORT,
        "agent_port": AGENT_PORT,
        "extension_ws": f"ws://{HOST}:{AGENT_PORT}/ws",
    }


@app.get("/api/dashboard/summary")
def dashboard_summary():
    """One cheap query for dashboard counters; replaces 4 large polling calls."""
    active_states = ("queued", "dispatching", "running", "flow_done", "downloading", "rendering", "qc")
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
        "server_version": "2.14.29",
        "agents_connected": len(AGENTS),
        "agents_idle": sum(1 for a in AGENTS.values() if not a.busy),
        "active_jobs": active_jobs,
        "page_profiles": profiles,
        "videos_completed": videos,
        "agents": [a.public() for a in AGENTS.values()],
        "time": utcnow(),
    }


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
    dest = UPLOAD_DIR / f"{server_stamp()}_{uuid.uuid4().hex[:6]}_{safe_name}"
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    size = dest.stat().st_size
    if size <= 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Tá»‡p upload rá»—ng.")

    suffix = dest.suffix.lower()
    is_image = str(file.content_type or "").lower().startswith("image/") or suffix in {".jpg",".jpeg",".png",".webp",".bmp"}
    image_meta: dict[str, Any] = {}
    if is_image:
        try:
            with Image.open(dest) as im:
                im.verify()
            with Image.open(dest) as im:
                width, height = im.size
                fmt = str(im.format or suffix.lstrip('.')).upper()
            if width < 256 or height < 256:
                raise ValueError(f"áº¢nh quÃ¡ nhá»: {width}x{height}; cáº§n tá»‘i thiá»ƒu 256x256")
            image_meta = {"image_valid": True, "width": int(width), "height": int(height), "format": fmt}
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"áº¢nh upload khÃ´ng há»£p lá»‡ hoáº·c khÃ´ng Ä‘á»c Ä‘Æ°á»£c: {exc}")

    sha256 = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {"ok": True, "path": str(dest.resolve()), "name": safe_name, "size": size, "sha256": sha256, **image_meta}


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


@app.get("/api/download-test/status")
def download_test_status() -> dict[str, Any]:
    source = _latest_job_with_video_media()
    if not source:
        return {"ok": True, "available": False, "reason": "ChÆ°a cÃ³ videoMediaId nÃ o tá»« Flow."}
    media_map = _video_media_map_from_job(source)
    expected = len(media_map)
    local_ready = len(_downloaded_videos_for_job(source["id"]))
    return {
        "ok": True,
        "available": bool(media_map),
        "source_job_id": source["id"],
        "source_status": source.get("status"),
        "expected": expected,
        "local_ready": local_ready,
        "agent_id": source.get("agent_id"),
        "agent_connected": bool(AGENTS),
        "agent_compatible": bool(compatible_agents()),
        "required_extension_version": MIN_EXTENSION_VERSION,
        "agent_versions": [str(a.version or "?") for a in AGENTS.values()],
    }


@app.post("/api/download-test/latest")
async def download_test_latest(source_job_id: str | None = None, timeout_sec: int = 180) -> dict[str, Any]:
    source = get_flow_job(source_job_id) if source_job_id else _latest_job_with_video_media()
    if not source:
        raise HTTPException(404, "ChÆ°a tÃ¬m tháº¥y job Flow nÃ o cÃ³ videoMediaId Ä‘á»ƒ test táº£i.")
    media_map = _video_media_map_from_job(source)
    if not media_map:
        raise HTTPException(400, "Job Ä‘Æ°á»£c chá»n khÃ´ng cÃ³ videoMediaId.")
    agent = AGENTS.get(str(source.get("agent_id") or ""))
    if not agent or not extension_version_compatible(agent.version):
        agent = require_compatible_agent()

    # IMPORTANT: download directly into the ORIGINAL job. No download_test Flow job
    # is created, so this button can never trigger/retrigger image/video generation.
    target_job_id = str(source["id"])
    update_flow_job(target_job_id, status="downloading", error=None)
    if get_content_queue_by_flow(target_job_id):
        update_content_queue_by_flow(target_job_id, status="generating", error=None)
    requested = 0
    for sid, ids in media_map.items():
        await agent.ws.send_text(dumps({"type":"DOWNLOAD_MEDIA_FILES","jobId":target_job_id,"sceneId":sid,"mediaIds":ids,"directTransfer":True}))
        requested += 1
    persist_event_log({"type":"DOWNLOAD_ONLY_TEST_START","jobId":target_job_id,"message":f"TEST DOWNLOAD ONLY Â· dÃ¹ng láº¡i video Ä‘Ã£ táº¡o Â· {len(media_map)} scene Â· KHÃ”NG GEN Láº I"})
    await ui_broadcast({"type":"DOWNLOAD_ONLY_TEST_START","jobId":target_job_id,"sourceJobId":target_job_id,"expected":len(media_map)})
    spawn(_watch_download_only_test(target_job_id, len(media_map), timeout_sec))
    return {"ok":True,"test_job_id":target_job_id,"source_job_id":target_job_id,"expected":len(media_map),"requested":requested,"agent_id":agent.id,"reused_existing_job":True}



@app.get("/api/flow/jobs/{job_id}")
def flow_job_detail(job_id: str):
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "KhÃ´ng tháº¥y job")
    j["assets"] = get_assets(200, job_id)
    return j


@app.post("/api/flow/jobs/{job_id}/retry")
async def retry_flow_job(job_id: str):
    require_compatible_agent()
    j = get_flow_job(job_id)
    if not j:
        raise HTTPException(404, "KhÃ´ng tháº¥y job")
    update_flow_job(job_id, status="queued", error=None, agent_id=None)
    await dispatch_jobs()
    return {"ok": True, "job_id": job_id}


@app.get("/api/assets")
def assets(limit: int = 200, job_id: str | None = None):
    return get_assets(min(max(limit, 1), 1000), job_id)


@app.get("/favicon.ico", include_in_schema=False)
def favicon_no_content():
    # Avoid noisy browser 404s when no favicon is configured.
    return Response(status_code=204)



def _profiles_for_page(page_id: str) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM page_profiles WHERE facebook_page_id=? ORDER BY enabled DESC,updated_at DESC,name ASC",
            (str(page_id).strip(),),
        ).fetchall()
    return [_profile_from_row(r) for r in rows]


def _unique_profile_identity(page_name: str, requested_name: str | None = None) -> tuple[str, str]:
    base = str(requested_name or "").strip() or f"{str(page_name or 'Page').strip()} Â· Há»“ sÆ¡"
    with conn() as c:
        ids={str(r[0]) for r in c.execute("SELECT id FROM page_profiles").fetchall()}
        names={str(r[0]).strip().lower() for r in c.execute("SELECT name FROM page_profiles").fetchall()}
    for n in range(1, 10000):
        name = base if n == 1 else f"{base} {n}"
        pid = _slug(name)
        if pid and pid not in ids and name.lower() not in names:
            return name, pid
    raise RuntimeError("KhÃ´ng táº¡o Ä‘Æ°á»£c ID há»“ sÆ¡ duy nháº¥t")


@app.post("/api/page-profiles/{profile_id}/clone")
def page_profile_clone(profile_id: str, payload: dict[str, Any] | None = None):
    source=get_page_profile(profile_id)
    if not source:
        raise HTTPException(404,"KhÃ´ng tháº¥y Page Profile")
    page_id=str(source.get("facebook_page_id") or "").strip() or None
    page=get_fb_page_secret(page_id) if page_id else None
    requested=(payload or {}).get("name") if isinstance(payload,dict) else None
    name,new_id=_unique_profile_identity(str((page or {}).get("name") or source.get("name") or "Page"), requested or f"{source.get('name') or 'Há»“ sÆ¡'} Â· Copy")
    # Báº®T BUá»˜C clear má»i áº£nh/reference áº£nh: FRONT, cÃ¡c gÃ³c, Persona derivatives, outfit image refs.
    req=PageProfileSave(
        id=new_id,
        name=name,
        theme=str(source.get("theme") or "adult glamour lifestyle in Vietnam"),
        persona_path=None,
        persona_left_path=None,
        persona_right_path=None,
        persona_back_path=None,
        body_preset=str(source.get("body_preset") or "curvy_fit"),
        sexiness_level=int(source.get("sexiness_level") or 60),
        outfit_prompts=source.get("outfit_prompts") or [],
        outfit_paths=[],
        backgrounds=source.get("backgrounds") or [],
        poses=source.get("poses") or [],
        music_paths=source.get("music_paths") or [],
        default_video_mode=str(source.get("default_video_mode") or "AUTO"),
        image_to_video_ratio=0,  # legacy column only; AUTO 2.14.29 ignores it.
        image_model=str(source.get("image_model") or "Nano Banana 2"),
        video_model=str(source.get("video_model") or "Veo 3.1 - Fast"),
        facebook_page_id=page_id,
        title_hint=str(source.get("title_hint") or ""),
        caption_style=str(source.get("caption_style") or "engaging_short"),
        ai_model=str(source.get("ai_model") or ""),
        ai_provider=str(source.get("ai_provider") or "router9"),
        enabled=True,
    )
    cloned=save_page_profile(req)
    # Copy production/schedule preferences, but NEVER clone runtime ON state/queue.
    cfg=dict(source.get("scheduler_config") or {})
    cfg["mode"]=str(source.get("default_video_mode") or cfg.get("mode") or "AUTO")
    cfg.pop("image_to_video_ratio",None)
    with conn() as c:
        c.execute(
            "UPDATE page_profiles SET scheduler_enabled=0,scheduler_warmup=1,next_publish_at=NULL,last_publish_at=NULL,"
            "scheduler_config_json=?,updated_at=? WHERE id=?",
            (dumps(cfg),utcnow(),new_id),
        )
    cloned=get_page_profile(new_id) or cloned
    persist_event_log({
        "type":"PROFILE_CLONED_CLEARED",
        "profileId":new_id,
        "pageId":page_id,
        "message":f"NhÃ¢n há»“ sÆ¡ {profile_id} â†’ {new_id} Â· Ä‘Ã£ CLEAR FRONT/gÃ³c/outfit image refs Â· Facebook Page giá»¯ nguyÃªn.",
    })
    return {"ok":True,"source_profile_id":profile_id,"profile":cloned,"images_cleared":True,"facebook_page_kept":True}


def _simple_profile_for_page(page_id: str) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM page_profiles WHERE facebook_page_id=? ORDER BY enabled DESC,updated_at DESC LIMIT 1",
            (str(page_id).strip(),),
        ).fetchone()
    return _profile_from_row(row) if row else None


def _simple_transition(profile: dict[str, Any] | None) -> str:
    cfg = (profile or {}).get("scheduler_config") or loads((profile or {}).get("scheduler_config_json"), {})
    return normalize_transition_preset((cfg or {}).get("beat_motion_preset") or "chaos_mix")


def _simple_scheduler_request(profile: dict[str, Any], *, live: bool = True) -> SchedulerConfigRequest:
    cfg = dict(profile.get("scheduler_config") or {})
    mode = str(cfg.get("mode") or profile.get("default_video_mode") or "AUTO").upper()
    if mode not in {"AUTO", "IMAGE_BEAT", "IMAGE_MIX", "IMAGE_TO_VIDEO"}:
        mode = "AUTO"
    transition = _simple_transition(profile)
    return SchedulerConfigRequest(
        enabled=True,
        scheduler_mode=str(cfg.get("scheduler_mode") or "DAILY_SLOTS").upper(),
        publish_interval_minutes=int(profile.get("publish_interval_minutes") or 180),
        buffer_target=int(profile.get("buffer_target") or 2),
        facebook_dry_run=not bool(live),
        first_publish_delay_minutes=int(cfg.get("first_publish_delay_minutes") or 0),
        daily_slots=cfg.get("daily_slots") or ["08:00", "14:00", "21:00"],
        daily_random_minutes=int(30 if cfg.get("daily_random_minutes") is None else cfg.get("daily_random_minutes")),
        resume_random_minutes=int(30 if cfg.get("resume_random_minutes") is None else cfg.get("resume_random_minutes")),
        mode=mode,
        beat_image_count=int(cfg.get("beat_image_count") or 10),
        beat_duration_sec=float(cfg.get("beat_duration_sec") or 15.0),
        beat_motion_preset=transition,
        i2v_clip_count=int(cfg.get("i2v_clip_count") or 3),
        i2v_clip_duration=str(cfg.get("i2v_clip_duration") or "8s"),
        image_concurrency=int(cfg.get("image_concurrency") or 9),
        video_concurrency=int(cfg.get("video_concurrency") or 4),
    )



MUSIC_ALLOWED_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com",
    "vt.tiktok.com", "www.tiktokmusic.com", "tiktokmusic.com",
    "capcut.com", "www.capcut.com", "m.capcut.com",
}


def _music_host_allowed(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False, ""
    if parsed.scheme.lower() != "https":
        return False, ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False, ""
    allowed = (
        host in MUSIC_ALLOWED_HOSTS
        or host.endswith(".tiktok.com")
        or host.endswith(".capcut.com")
    )
    return bool(allowed), host


def _profile_music_dir(profile_id: str) -> Path:
    p = (UPLOAD_DIR / "music" / _slug(profile_id)).resolve()
    p.mkdir(parents=True, exist_ok=True)
    root = (UPLOAD_DIR / "music").resolve()
    if root != p and root not in p.parents:
        raise RuntimeError("Music path vÆ°á»£t khá»i uploads/music")
    return p


def _safe_music_meta_path(mp3: Path) -> Path:
    return mp3.with_suffix(mp3.suffix + ".meta.json")


def _probe_audio_file(path: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        raise RuntimeError("KhÃ´ng tháº¥y ffprobe trong PATH")
    cp = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,format_name",
            "-show_entries", "stream=codec_type,codec_name,channels,sample_rate",
            "-of", "json", str(path),
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if cp.returncode != 0:
        raise RuntimeError("ffprobe khÃ´ng Ä‘á»c Ä‘Æ°á»£c audio: " + (cp.stderr or cp.stdout or "")[-1200:])
    data = json.loads(cp.stdout or "{}")
    streams = data.get("streams") or []
    audio_streams = [s for s in streams if str(s.get("codec_type") or "") == "audio"]
    if not audio_streams:
        raise RuntimeError("File táº£i vá» khÃ´ng cÃ³ audio stream")
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except Exception:
        duration = 0.0
    if duration <= 0 or duration > 600:
        raise RuntimeError(f"Audio duration khÃ´ng há»£p lá»‡: {duration:.1f}s (giá»›i háº¡n 600s)")
    return {
        "duration": round(duration, 3),
        "format": str((data.get("format") or {}).get("format_name") or ""),
        "codec": str(audio_streams[0].get("codec_name") or ""),
        "channels": int(audio_streams[0].get("channels") or 0),
        "sample_rate": str(audio_streams[0].get("sample_rate") or ""),
    }


def _write_music_meta(path: Path, meta: dict[str, Any]) -> None:
    _safe_music_meta_path(path).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_music_meta(path: Path) -> dict[str, Any]:
    p = _safe_music_meta_path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _append_profile_music_path(profile_id: str, music_path: str) -> dict[str, Any]:
    profile = get_page_profile(profile_id)
    if not profile:
        raise KeyError(profile_id)
    paths = _clean_list(profile.get("music_paths"))
    resolved = str(Path(music_path).resolve())
    if resolved not in paths:
        paths.append(resolved)
    with conn() as c:
        c.execute(
            "UPDATE page_profiles SET music_paths_json=?,updated_at=? WHERE id=?",
            (dumps(paths), utcnow(), profile_id),
        )
    return get_page_profile(profile_id) or profile


def _remove_profile_music_path(profile_id: str, music_path: str) -> dict[str, Any]:
    profile = get_page_profile(profile_id)
    if not profile:
        raise KeyError(profile_id)
    target = str(Path(music_path).resolve())
    paths = [str(Path(p).resolve()) for p in _clean_list(profile.get("music_paths")) if str(Path(p).resolve()) != target]
    with conn() as c:
        c.execute(
            "UPDATE page_profiles SET music_paths_json=?,updated_at=? WHERE id=?",
            (dumps(paths), utcnow(), profile_id),
        )
    return get_page_profile(profile_id) or profile


def _music_library_rows(profile_id: str) -> list[dict[str, Any]]:
    profile = get_page_profile(profile_id)
    if not profile:
        raise KeyError(profile_id)
    rows=[]
    for raw in _clean_list(profile.get("music_paths")):
        p=Path(raw)
        meta=_read_music_meta(p) if p.exists() else {}
        rows.append({
            "path": str(p),
            "name": p.name,
            "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else 0,
            "sha256": meta.get("sha256") or "",
            "source_url": meta.get("source_url") or "",
            "source_host": meta.get("source_host") or "",
            "duration": meta.get("duration"),
            "codec": meta.get("codec") or "",
            "imported_at": meta.get("imported_at") or "",
        })
    return rows


def _download_public_music_url(profile_id: str, url: str) -> dict[str, Any]:
    profile = get_page_profile(profile_id)
    if not profile:
        raise KeyError(profile_id)
    ok, host = _music_host_allowed(url)
    if not ok:
        raise ValueError("Chá»‰ nháº­n HTTPS URL cÃ´ng khai tá»« TikTok hoáº·c CapCut")

    # Use the same Python/venv, not an arbitrary PATH executable.
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        raise RuntimeError("Thiáº¿u yt-dlp. Cháº¡y láº¡i run.bat Ä‘á»ƒ cÃ i requirements.txt.")

    dest_dir = _profile_music_dir(profile_id)
    before = {p.resolve() for p in dest_dir.glob("*.mp3")}
    env = dict(os.environ)
    # Official yt-dlp docs warn plugins are imported automatically and unchecked.
    # Disable every plugin for this server integration.
    env["YTDLP_NO_PLUGINS"] = "1"
    template = str(dest_dir / "%(extractor)s_%(id)s_%(epoch)s.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config",
        "--no-config-locations",
        "--no-playlist",
        "--max-downloads", "1",
        "--max-filesize", "40M",
        "--match-filters", "duration <=? 600 & !is_live",
        "--socket-timeout", "20",
        "--retries", "2",
        "--fragment-retries", "2",
        "--restrict-filenames",
        "--no-write-thumbnail",
        "--no-write-info-json",
        "--no-write-comments",
        "--no-write-playlist-metafiles",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "2",
        "-o", template,
        "--print", "after_move:filepath",
        str(url).strip(),
    ]
    cp = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=180, env=env
    )
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "")[-2500:]
        raise RuntimeError("KhÃ´ng import Ä‘Æ°á»£c URL nÃ y báº±ng extractor cÃ´ng khai: " + tail)

    candidates = [p.resolve() for p in dest_dir.glob("*.mp3") if p.resolve() not in before]
    if not candidates:
        # If same source was imported before and yt-dlp reused/overwrote a filename,
        # accept the newest mp3 that stays inside this Page's directory.
        candidates = sorted(
            [p.resolve() for p in dest_dir.glob("*.mp3")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:1]
    if not candidates:
        raise RuntimeError("yt-dlp bÃ¡o xong nhÆ°ng khÃ´ng tÃ¬m tháº¥y MP3 Ä‘áº§u ra")

    path = candidates[0]
    allowed_root = dest_dir.resolve()
    if allowed_root != path.parent and allowed_root not in path.parents:
        raise RuntimeError("Output path khÃ´ng an toÃ n")
    size = path.stat().st_size
    if size <= 0 or size > 40 * 1024 * 1024:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"MP3 vÆ°á»£t giá»›i háº¡n hoáº·c rá»—ng: {size} bytes")

    probe = _probe_audio_file(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        "source_url": str(url).strip(),
        "source_host": host,
        "profile_id": profile_id,
        "sha256": sha,
        "size": size,
        "imported_at": utcnow(),
        **probe,
    }
    _write_music_meta(path, meta)
    updated = _append_profile_music_path(profile_id, str(path))
    persist_event_log({
        "type": "MUSIC_URL_IMPORTED",
        "profileId": profile_id,
        "message": f"Music imported Â· {host} Â· {path.name} Â· {probe['duration']:.1f}s",
    })
    return {"ok": True, "music": {"path": str(path), "name": path.name, **meta}, "profile": updated}


@app.get("/api/page-profiles/{profile_id}/music")
def page_profile_music_list(profile_id: str):
    try:
        rows=_music_library_rows(profile_id)
    except KeyError:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    return {"ok": True, "profile_id": profile_id, "count": len(rows), "items": rows}


@app.post("/api/page-profiles/{profile_id}/music/import-url")
def page_profile_music_import_url(profile_id: str, req: MusicUrlImportRequest):
    try:
        return _download_public_music_url(profile_id, req.url)
    except KeyError:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Import nháº¡c quÃ¡ 180 giÃ¢y nÃªn Ä‘Ã£ dá»«ng")
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/page-profiles/{profile_id}/music/remove")
def page_profile_music_remove(profile_id: str, req: MusicRemoveRequest):
    try:
        updated=_remove_profile_music_path(profile_id, req.path)
    except KeyError:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    # Only remove from Page library; do not delete the physical file automatically.
    return {"ok": True, "profile": updated, "items": _music_library_rows(profile_id)}


@app.get("/api/page-profiles")
def page_profiles():
    return list_page_profiles()


@app.get("/api/page-profiles/{profile_id}")
def page_profile_detail(profile_id: str):
    p = get_page_profile(profile_id)
    if not p:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    return p


@app.post("/api/page-profiles")
def page_profile_save(req: PageProfileSave):
    try:
        return {"ok": True, "profile": save_page_profile(req)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/page-profiles/{profile_id}/apply-vietnam-preset")
def page_profile_apply_vietnam_preset(profile_id: str):
    try:
        return {"ok": True, "profile": apply_vietnam_lifestyle_preset(profile_id)}
    except KeyError:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/page-profiles/{profile_id}/prepare-persona")
def page_profile_prepare_persona(profile_id: str):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    source = str(profile.get("persona_path") or "").strip()
    if not source:
        raise HTTPException(409, "Profile chÆ°a cÃ³ áº£nh FRONT gá»‘c. HÃ£y táº£i áº£nh FRONT rá»“i LÆ¯U Há»’ SÆ  trÆ°á»›c.")
    if not Path(source).exists():
        raise HTTPException(409, "áº¢nh FRONT gá»‘c khÃ´ng cÃ²n tá»“n táº¡i trÃªn server. HÃ£y táº£i láº¡i áº£nh FRONT rá»“i LÆ¯U Há»’ SÆ .")
    try:
        return {"ok": True, "profile": prepare_profile_persona(profile_id)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/page-profiles/{profile_id}/angles/{angle}/generate")
async def page_profile_generate_one_angle(profile_id: str, angle: str, force: bool = False):
    angle=str(angle or "").strip().lower()
    if angle not in {"left","right","back"}:
        raise HTTPException(400,"GÃ³c pháº£i lÃ  left/right/back")
    profile=get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404,"KhÃ´ng tháº¥y Page Profile")
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
        raise HTTPException(404,"KhÃ´ng tháº¥y Page Profile")
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
        return {"ok":False,"blocked_bulk_force":True,"message":"REGEN 3 GÃ“C Ä‘Ã£ táº¯t Ä‘á»ƒ trÃ¡nh spam. HÃ£y GEN Láº I tá»«ng gÃ³c.","profile":get_page_profile(profile_id)}
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
        raise HTTPException(404,"KhÃ´ng tháº¥y Page Profile")
    return {"ok":True,"profile":profile,"active_jobs":list_active_persona_angle_jobs(profile_id),"server_version":"2.14.29"}


@app.delete("/api/page-profiles/{profile_id}")
def page_profile_delete(profile_id: str):
    profile=get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404,"KhÃ´ng tháº¥y Page Profile")
    page_id=str(profile.get("facebook_page_id") or "").strip() or None
    SIMPLE_START_CANCELLED.add(profile_id)
    task=SIMPLE_START_TASKS.pop(profile_id,None)
    if task and not task.done():
        task.cancel()
    # XÃ³a há»“ sÆ¡/rÃ ng buá»™c runtime cá»§a há»“ sÆ¡; tuyá»‡t Ä‘á»‘i khÃ´ng xÃ³a fb_pages/fb_page_secrets.
    with conn() as c:
        c.execute("DELETE FROM content_queue WHERE page_profile_id=?", (profile_id,))
        c.execute("DELETE FROM factory_runs WHERE page_profile_id=?", (profile_id,))
        c.execute("DELETE FROM page_profiles WHERE id=?", (profile_id,))
    try:
        persona_dir=(OUTPUT_DIR/"personas"/_slug(profile_id)).resolve()
        if persona_dir.exists() and (OUTPUT_DIR/"personas").resolve() in persona_dir.parents:
            shutil.rmtree(persona_dir,ignore_errors=True)
    except Exception:
        pass
    persist_event_log({
        "type":"PROFILE_DELETED_PAGE_KEPT","profileId":profile_id,"pageId":page_id,
        "message":f"ÄÃ£ xÃ³a há»“ sÆ¡ {profile_id}; Facebook Page/token KHÃ”NG bá»‹ xÃ³a."
    })
    return {"ok":True,"deleted_profile_id":profile_id,"facebook_page_id":page_id,"facebook_page_kept":True}


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
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    if not profile.get("facebook_page_id"):
        raise HTTPException(400, "Page Profile chÆ°a map Facebook Page")
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
    # Idempotency guard: clicking START twice with the same settings must not refill or create duplicate work.
    same_cfg = (
        bool(profile.get("scheduler_enabled"))
        and str(old_cfg.get("scheduler_mode") or "INTERVAL").upper() == scheduler_mode
        and int(profile.get("publish_interval_minutes") or 180) == int(req.publish_interval_minutes)
        and int(profile.get("buffer_target") or 2) == int(req.buffer_target)
        and bool(profile.get("scheduler_dry_run")) == bool(req.facebook_dry_run)
        and list(old_cfg.get("daily_slots") or []) == list(slots)
        and int(old_cfg.get("daily_random_minutes") or 0) == int(req.daily_random_minutes)
        and int(old_cfg.get("resume_random_minutes") or 0) == int(req.resume_random_minutes)
        and str(old_cfg.get("mode") or "AUTO").upper() == str(req.mode or "AUTO").upper()
        and int(old_cfg.get("beat_image_count") or 7) == int(req.beat_image_count)
        and float(old_cfg.get("beat_duration_sec") or 10) == float(req.beat_duration_sec)
        and str(old_cfg.get("beat_motion_preset") or "capcut_beat") == str(req.beat_motion_preset)
        and int(old_cfg.get("i2v_clip_count") or 3) == int(req.i2v_clip_count)
        and str(old_cfg.get("i2v_clip_duration") or "4s") == str(req.i2v_clip_duration)
        and int(old_cfg.get("image_concurrency") or 9) == int(req.image_concurrency)
        and int(old_cfg.get("video_concurrency") or 4) == int(req.video_concurrency)
    )
    if same_cfg:
        fill=await scheduler_fill_profile(profile_id)
        status=scheduler_status(profile_id)
        persist_event_log({
            "type":"SCHEDULER_START_BUFFER_ENSURE","profileId":profile_id,
            "message":f"START láº·p Â· giá»¯ lá»‹ch/config Â· ensure buffer ngay {status.get('buffer_active',0)}/{status.get('buffer_target',2)}."
        })
        return {"ok":True,"already_enabled":True,"fill":fill,"status":status}

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
    msg = (f"Scheduler ON Â· slots {','.join(slots)} Â· random Â±{req.daily_random_minutes}m Â· buffer {req.buffer_target}" if scheduler_mode == "DAILY_SLOTS" else f"Scheduler ON Â· má»—i {req.publish_interval_minutes} phÃºt Â· buffer {req.buffer_target}")
    persist_event_log({"type":"SCHEDULER_STARTED","profileId":profile_id,"message":msg + (" Â· DRY RUN" if req.facebook_dry_run else " Â· PUBLISH THáº¬T")})
    return {"ok": True, "fill": fill, "status": scheduler_status(profile_id)}


@app.post("/api/scheduler/{profile_id}/stop")
def scheduler_stop(profile_id: str):
    if not get_page_profile(profile_id):
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    with conn() as c:
        c.execute("UPDATE page_profiles SET scheduler_enabled=0,updated_at=? WHERE id=?", (utcnow(), profile_id))
    persist_event_log({"type":"SCHEDULER_STOPPED","profileId":profile_id,"message":"Scheduler OFF"})
    return {"ok": True, "status": scheduler_status(profile_id)}


@app.post("/api/scheduler/{profile_id}/fill-now")
async def scheduler_fill_now(profile_id: str):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    if not profile.get("scheduler_config"):
        raise HTTPException(400, "ChÆ°a START Scheduler Ä‘á»ƒ lÆ°u cáº¥u hÃ¬nh generate")
    try:
        result = await scheduler_fill_profile(profile_id)
        return {"ok": True, "fill": result, "status": scheduler_status(profile_id)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/scheduler/{profile_id}/discard-failed")
async def scheduler_discard_failed(profile_id: str):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    with conn() as c:
        rows = c.execute(
            "SELECT id,flow_job_id FROM content_queue WHERE page_profile_id=? AND status='failed'",
            (profile_id,),
        ).fetchall()
        c.execute(
            "UPDATE content_queue SET status='discarded',updated_at=? WHERE page_profile_id=? AND status='failed'",
            (utcnow(), profile_id),
        )
    count = len(rows)
    persist_event_log({
        "type": "SCHEDULER_FAILED_DISCARDED",
        "profileId": profile_id,
        "message": f"ÄÃ£ bá» qua {count} job lá»—i Â· scheduler cÃ³ thá»ƒ táº¡o bÃ¹ trá»Ÿ láº¡i.",
    })
    fill = await scheduler_fill_profile(profile_id) if profile.get("scheduler_enabled") else {"created": 0}
    return {"ok": True, "discarded": count, "fill": fill, "status": scheduler_status(profile_id)}


@app.post("/api/scheduler/{profile_id}/publish-now")
async def scheduler_publish_now(profile_id: str):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    with conn() as c:
        c.execute("UPDATE page_profiles SET scheduler_warmup=0,next_publish_at=?,updated_at=? WHERE id=?", (utcnow(), utcnow(), profile_id))
    profile = get_page_profile(profile_id) or profile
    result = await scheduler_publish_due(profile)
    if result and result.get("ok"):
        await scheduler_fill_profile(profile_id)
    return {"ok": True, "publish": result, "status": scheduler_status(profile_id)}


@app.post("/api/factory/v2/generate")
async def factory_v2_generate(req: FactoryV2GenerateRequest):
    require_compatible_agent()
    profile = get_page_profile(req.page_profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    if not profile.get("enabled"):
        raise HTTPException(400, "Page Profile Ä‘ang disabled")
    # Build first using a provisional run id, then persist final run + jobs.
    provisional = f"factory_{server_stamp()}_{uuid.uuid4().hex[:8]}"
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
            update_flow_job(item["job_id"], status="failed", error=f"Factory setup lá»—i: {exc}")
        raise HTTPException(400, str(exc))


@app.get("/api/factory/v2/runs")
def factory_v2_runs(limit: int = 50):
    return list_factory_runs(min(max(limit, 1), 200))


@app.get("/api/qc/{job_id}")
def qc_job(job_id: str):
    q = latest_qc(job_id)
    if not q:
        raise HTTPException(404, "Job chÆ°a cÃ³ QC")
    return q



def _flow_scene_profile_id(scene: dict[str, Any]) -> str:
    meta = scene.get("metadata") or {}
    if not isinstance(meta, dict):
        return ""
    direct = str(meta.get("profileId") or "").strip()
    if direct:
        return direct
    factory = meta.get("factoryV2") or {}
    if isinstance(factory, dict):
        return str(factory.get("profileId") or "").strip()
    return ""


def _profile_workspace_job_ids(profile_id: str, *, limit: int = 500) -> tuple[set[str], set[str]]:
    """Return (all profile jobs, factory child jobs) without mixing other Pages."""
    all_ids: set[str] = set()
    factory_ids: set[str] = set()
    with conn() as c:
        runs = c.execute(
            "SELECT job_ids_json FROM factory_runs WHERE page_profile_id=? ORDER BY created_at DESC LIMIT 200",
            (profile_id,),
        ).fetchall()
        for row in runs:
            for jid in loads(row["job_ids_json"], []):
                if jid:
                    all_ids.add(str(jid))
                    factory_ids.add(str(jid))

        for row in c.execute(
            "SELECT flow_job_id FROM content_queue WHERE page_profile_id=? AND flow_job_id IS NOT NULL ORDER BY created_at DESC LIMIT 300",
            (profile_id,),
        ).fetchall():
            jid = str(row["flow_job_id"] or "")
            if jid:
                all_ids.add(jid)
                factory_ids.add(jid)

        # Persona/manual jobs are identified by scene metadata profileId.
        recent = c.execute(
            "SELECT id,scenes_json FROM flow_jobs ORDER BY created_at DESC LIMIT ?",
            (min(max(limit, 50), 1000),),
        ).fetchall()
        for row in recent:
            scenes = loads(row["scenes_json"], [])
            if any(_flow_scene_profile_id(s) == profile_id for s in scenes if isinstance(s, dict)):
                all_ids.add(str(row["id"]))
    return all_ids, factory_ids


def _profile_workspace_activity(profile_id: str) -> list[dict[str, Any]]:
    all_ids, factory_ids = _profile_workspace_job_ids(profile_id)
    out: list[dict[str, Any]] = []
    run_child_ids: set[str] = set()

    with conn() as c:
        rows = c.execute(
            "SELECT * FROM factory_runs WHERE page_profile_id=? ORDER BY created_at DESC LIMIT 80",
            (profile_id,),
        ).fetchall()

    for row in rows:
        run = _factory_run_row(row)
        child_ids = [str(x) for x in run.get("job_ids", [])]
        run_child_ids.update(child_ids)
        qc_rows = [latest_qc(jid) for jid in child_ids]
        qc_rows = [q for q in qc_rows if q]
        qc_pass_n = sum(1 for q in qc_rows if bool(q.get("passed")))
        qc_text = ""
        if qc_rows:
            scores = [int(q.get("score") or 0) for q in qc_rows]
            qc_text = f"QC {qc_pass_n}/{len(qc_rows)} Ä‘áº¡t Â· Ä‘iá»ƒm " + "/".join(str(x) for x in scores)
        details = f"{run.get('done',0)}/{run.get('requested_count',0)} hoÃ n táº¥t Â· {run.get('failed',0)} lá»—i Â· {run.get('active',0)} Ä‘ang cháº¡y"
        err = "; ".join(str(j.get("error") or "") for j in run.get("jobs", []) if j.get("error"))[:800]
        out.append({
            "id": run["id"],
            "type": "run",
            "label": f"Sáº£n xuáº¥t Â· {run.get('requested_mode') or 'AUTO'}",
            "status": run.get("status") or "queued",
            "detail": details,
            "qc_text": qc_text,
            "qc_pass": bool(qc_rows and qc_pass_n == len(qc_rows)),
            "error": err,
            "created_at": run.get("created_at"),
            "retry_job_id": None,
        })

    # Only non-factory standalone jobs appear separately; child jobs are already represented by their run.
    standalone = sorted(all_ids - run_child_ids)
    if standalone:
        placeholders = ",".join("?" for _ in standalone)
        with conn() as c:
            rows = c.execute(
                f"SELECT id,kind,status,error,created_at,updated_at,scenes_json FROM flow_jobs WHERE id IN ({placeholders}) ORDER BY created_at DESC",
                standalone,
            ).fetchall()
        for row in rows:
            d = dict(row)
            kind = str(d.get("kind") or "")
            # Old download tests are diagnostics and don't belong in normal Page history.
            if kind == "download_test":
                continue
            q = latest_qc(str(d["id"]))
            out.append({
                "id": d["id"],
                "type": "job",
                "label": "Persona" if kind.startswith("persona_angle") else kind,
                "kind": kind,
                "status": d.get("status"),
                "detail": f"{len(loads(d.get('scenes_json'), []))} cáº£nh",
                "qc_text": (f"QC {int(q.get('score') or 0)} Â· {'Äáº T' if q.get('passed') else 'KHÃ”NG Äáº T'}" if q else ""),
                "qc_pass": bool(q and q.get("passed")),
                "error": d.get("error") or "",
                "created_at": d.get("created_at"),
                "retry_job_id": d["id"] if d.get("status") in {"failed","partial_failed","interrupted","qc_failed"} else None,
            })

    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out[:120]


def _profile_workspace_results(profile_id: str) -> list[dict[str, Any]]:
    _,factory_ids=_profile_workspace_job_ids(profile_id)
    if not factory_ids:
        return []
    ids=sorted(factory_ids);placeholders=",".join("?" for _ in ids)
    with conn() as c:
        rows=c.execute(f"SELECT * FROM assets WHERE job_id IN ({placeholders}) ORDER BY created_at DESC LIMIT 800",ids).fetchall()
    out=[];seen=set()
    for row in rows:
        a=dict(row);a["metadata"]=loads(a.pop("metadata_json"),{})
        a["local_url"]=path_to_local_url(a.get("local_path"))
        kind=str(a.get("kind") or "");sid=int(a.get("scene_id") or 0)
        if kind not in {"image","video","final_video"}:
            continue
        is_final=kind=="final_video" or (kind=="video" and sid==0)
        # CHILD: prefer remote URL streaming. FINAL: local /outputs path is authoritative.
        remote=_safe_remote_media_url(a.get("url"))
        a["is_final"]=is_final
        a["stream_url"]=(a.get("local_url") if is_final else (remote or a.get("local_url")))
        a["download_url"]=(a.get("local_url") if is_final else None)
        if not a.get("stream_url"):
            continue
        key=str(a.get("media_id") or a.get("local_path") or a.get("url") or a.get("id"))
        if key in seen:continue
        seen.add(key)
        q=latest_qc(str(a.get("job_id") or ""))
        a["qc_text"]=f"QC {int(q.get('score') or 0)} Â· {'Äáº T' if q.get('passed') else 'KHÃ”NG Äáº T'}" if q and is_final else ""
        out.append(a)
    return out



@app.get("/api/auto/profiles/{profile_id}/workspace")
def auto_profile_workspace(profile_id: str):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y Page Profile")
    return {
        "ok": True,
        "profile_id": profile_id,
        "activity": _profile_workspace_activity(profile_id),
        "results": _profile_workspace_results(profile_id),
        "scheduler": scheduler_status(profile_id),
    }


def _simple_norm_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", str(value or "")).encode("ascii","ignore").decode("ascii").lower())


def _simple_profile_score_raw(profile: dict[str, Any]) -> int:
    score = 0
    if profile.get("persona_master_path"): score += 100
    if profile.get("persona_path"): score += 60
    if profile.get("persona_left_master_path"): score += 10
    if profile.get("persona_right_master_path"): score += 10
    if profile.get("persona_back_master_path"): score += 10
    if profile.get("scheduler_config_json"): score += 8
    if profile.get("default_video_mode"): score += 4
    if profile.get("theme"): score += 2
    return score


def _simple_is_numeric_shadow(profile: dict[str, Any], page_id: str) -> bool:
    name = str(profile.get("name") or "").strip()
    pid = str(profile.get("id") or "").strip()
    return (
        name == str(page_id)
        or pid == str(page_id)
        or (bool(re.fullmatch(r"\d{10,}", name)) and not _simple_norm_name(name) != _simple_norm_name(page_id))
    )


def _simple_relink_profiles_to_pages(profiles: list[dict[str, Any]], pages: list[dict[str, Any]]) -> set[str]:
    """Backward repair only. V2.14.29 allows MANY profiles mapped to one Facebook Page.

    We only auto-link one unique exact-name profile when that Page currently has
    no mapped profile at all. Existing mappings are never cleared/stolen.
    """
    linked:set[str]=set()
    with conn() as c:
        raw=[dict(r) for r in c.execute("SELECT * FROM page_profiles ORDER BY updated_at DESC").fetchall()]
        for page in pages:
            page_id=str(page.get("id") or "").strip()
            norm=_simple_norm_name(page.get("name"))
            if not page_id or not norm:
                continue
            if any(str(p.get("facebook_page_id") or "").strip()==page_id for p in raw):
                continue
            matches=[p for p in raw if not str(p.get("facebook_page_id") or "").strip() and _simple_norm_name(p.get("name"))==norm]
            if len(matches)!=1:
                continue
            target=matches[0]
            c.execute("UPDATE page_profiles SET facebook_page_id=?,updated_at=? WHERE id=?",(page_id,utcnow(),target["id"]))
            target["facebook_page_id"]=page_id
            linked.add(str(target["id"]))
            persist_event_log({
                "type":"SIMPLE_PROFILE_AUTO_LINKED","profileId":target["id"],"pageId":page_id,
                "message":f"Tá»± Ä‘á»“ng bá»™ Page â†” profile {target.get('name')} Â· khÃ´ng Ä‘á»¥ng cÃ¡c profile khÃ¡c.",
            })
    if linked:
        refreshed={str(p["id"]):p for p in list_page_profiles()}
        for i,p in enumerate(profiles):
            if str(p.get("id")) in refreshed:
                profiles[i]=refreshed[str(p.get("id"))]
    return linked


def _simple_reconcile_all() -> set[str]:
    profiles = list_page_profiles()
    pages = list_fb_pages()
    return _simple_relink_profiles_to_pages(profiles, pages)


@app.get("/api/simple/pages")
def simple_pages():
    profiles=list_page_profiles()
    pages=list_fb_pages()
    auto_linked=_simple_relink_profiles_to_pages(profiles,pages)
    # refresh after possible legacy repair
    profiles=list_page_profiles()
    out=[]
    for page in pages:
        page_id=str(page.get("id") or "")
        mapped=[p for p in profiles if str(p.get("facebook_page_id") or "").strip()==page_id]
        p_rows=[]
        for profile in mapped:
            cfg=dict(profile.get("scheduler_config") or {})
            has_face=bool(profile.get("persona_ready") or profile.get("persona_path") or profile.get("persona_source_exists") or profile.get("persona_master_path"))
            config_saved=bool(profile.get("default_video_mode") or cfg.get("mode") or cfg.get("beat_motion_preset") or profile.get("persona_path"))
            p_rows.append({
                "profile_id":profile.get("id"),
                "profile_name":profile.get("name"),
                "config_saved":config_saved,
                "has_face":has_face,
                "persona_ready":bool(profile.get("persona_ready")),
                "video_mode":profile.get("default_video_mode") or cfg.get("mode") or "AUTO",
                "transition_preset":_simple_transition(profile),
                "scene_mode":normalize_scene_mode(cfg.get("scene_mode") or "GYM"),
                "scene_mix":normalize_scene_mix(cfg.get("scene_mix") or []),
                "scheduler_enabled":bool(profile.get("scheduler_enabled")),
                "scheduler_dry_run":bool(profile.get("scheduler_dry_run",1)),
                "next_publish_at":profile.get("next_publish_at"),
                "buffer_target":int(profile.get("buffer_target") or 2),
                "persona_angle_count":int(profile.get("persona_angle_count") or 0),
                "persona_pack_ready":bool(profile.get("persona_pack_ready")),
                "auto_linked":str(profile.get("id")) in auto_linked,
                "start_pending":bool(profile.get("id") in SIMPLE_START_TASKS and not SIMPLE_START_TASKS[profile.get("id")].done()),
            })
        primary=p_rows[0] if p_rows else {}
        out.append({
            "page_id":page.get("id"),"page_name":page.get("name"),
            "profiles":p_rows,"profile_count":len(p_rows),
            # Compatibility fields for older frontend callers:
            "profile_id":primary.get("profile_id"),"config_saved":primary.get("config_saved",False),
            "has_face":primary.get("has_face",False),"persona_ready":primary.get("persona_ready",False),
            "video_mode":primary.get("video_mode","AUTO"),"transition_preset":primary.get("transition_preset","chaos_mix"),
            "scene_mode":primary.get("scene_mode","GYM"),"scene_mix":primary.get("scene_mix",[]),
            "scheduler_enabled":primary.get("scheduler_enabled",False),"scheduler_dry_run":primary.get("scheduler_dry_run",True),
            "next_publish_at":primary.get("next_publish_at"),"buffer_target":primary.get("buffer_target",2),
            "persona_angle_count":primary.get("persona_angle_count",0),"persona_pack_ready":primary.get("persona_pack_ready",False),
            "auto_linked":primary.get("auto_linked",False),"start_pending":primary.get("start_pending",False),
        })
    return out


@app.post("/api/simple/pages/save")
def simple_page_save(req: SimplePageSaveRequest):
    page_id=str(req.facebook_page_id or "").strip()
    page=get_fb_page_secret(page_id)
    if not page:
        raise HTTPException(404,"Page chÆ°a Ä‘Æ°á»£c import token")
    existing=get_page_profile(str(req.profile_id).strip()) if req.profile_id else None
    if existing:
        mapped=str(existing.get("facebook_page_id") or "").strip()
        if mapped and mapped!=page_id:
            raise HTTPException(409,"Há»“ sÆ¡ Ä‘ang thuá»™c Facebook Page khÃ¡c")
    mode=str(req.video_mode or "AUTO").upper()
    if mode not in {"AUTO","IMAGE_BEAT","IMAGE_MIX","IMAGE_TO_VIDEO"}:
        mode="AUTO"
    transition=normalize_transition_preset(req.transition_preset)
    scene_mode=normalize_scene_mode(req.scene_mode)
    scene_mix=normalize_scene_mix(req.scene_mix)
    if scene_mode=="MIX" and not scene_mix:
        scene_mix=["GYM","BEACH"]
    persona_path=(req.persona_path or "").strip() or (str(existing.get("persona_path") or "").strip() if existing else None)
    if not persona_path:
        raise HTTPException(400,"Há»“ sÆ¡ má»›i báº¯t buá»™c import áº£nh máº·t FRONT trÆ°á»›c khi LÆ¯U")

    if existing:
        profile_name=str(req.profile_name or existing.get("name") or page.get("name") or page_id).strip()
        save_req=PageProfileSave(
            id=existing.get("id"),name=profile_name,
            theme=existing.get("theme") or "adult glamour lifestyle in Vietnam",
            persona_path=persona_path,
            persona_left_path=existing.get("persona_left_path") or None,
            persona_right_path=existing.get("persona_right_path") or None,
            persona_back_path=existing.get("persona_back_path") or None,
            body_preset=existing.get("body_preset") or "curvy_fit",
            sexiness_level=int(existing.get("sexiness_level") or 60),
            outfit_prompts=existing.get("outfit_prompts") or [],outfit_paths=existing.get("outfit_paths") or [],
            backgrounds=existing.get("backgrounds") or [],poses=existing.get("poses") or [],music_paths=existing.get("music_paths") or [],
            default_video_mode=mode,image_to_video_ratio=0,
            image_model=existing.get("image_model") or "Nano Banana 2",
            video_model=existing.get("video_model") or "Veo 3.1 - Fast",
            facebook_page_id=page_id,title_hint=existing.get("title_hint") or "Phong cÃ¡ch Viá»‡t Nam cuá»‘n hÃºt má»—i ngÃ y",
            caption_style=existing.get("caption_style") or "engaging_short",
            ai_model=existing.get("ai_model") or "",ai_provider=existing.get("ai_provider") or "router9",enabled=True,
        )
    else:
        profile_name,new_id=_unique_profile_identity(str(page.get("name") or page_id),req.profile_name)
        save_req=PageProfileSave(
            id=new_id,name=profile_name,persona_path=persona_path,default_video_mode=mode,
            image_to_video_ratio=0,facebook_page_id=page_id,title_hint="Phong cÃ¡ch Viá»‡t Nam cuá»‘n hÃºt má»—i ngÃ y",enabled=True,
        )

    profile=save_page_profile(save_req)
    cfg=dict(profile.get("scheduler_config") or {})
    cfg["mode"]=mode
    cfg["beat_motion_preset"]=transition
    cfg["scene_mode"]=scene_mode
    cfg["scene_mix"]=scene_mix
    cfg.pop("image_to_video_ratio",None)
    if not cfg.get("scheduler_mode"):
        cfg.update({
            "scheduler_mode":"DAILY_SLOTS","daily_slots":["08:00","14:00","21:00"],
            "daily_random_minutes":30,"resume_random_minutes":30,
            "beat_image_count":10,"beat_duration_sec":15.0,
            "i2v_clip_count":3,"i2v_clip_duration":"8s","image_concurrency":9,"video_concurrency":4,
        })
    with conn() as c:
        c.execute(
            "UPDATE page_profiles SET default_video_mode=?,facebook_page_id=?,scheduler_config_json=?,updated_at=? WHERE id=?",
            (mode,page_id,dumps(cfg),utcnow(),profile["id"]),
        )
    profile=get_page_profile(profile["id"]) or profile
    persist_event_log({
        "type":"SIMPLE_PAGE_SAVED","profileId":profile["id"],"pageId":page_id,
        "message":f"ÄÃ£ lÆ°u há»“ sÆ¡ {profile.get('name')} cho Page {page.get('name') or page_id}.",
    })
    return {"ok":True,"profile":profile,"transition_preset":transition,"scene_mode":scene_mode,"scene_mix":scene_mix,"profile_count":len(_profiles_for_page(page_id))}



@app.post("/api/simple/pages/{profile_id}/settings")
def simple_page_settings_save(profile_id: str, payload: dict[str, Any]):
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y cáº¥u hÃ¬nh Page")
    cfg = dict(profile.get("scheduler_config") or {})
    scheduler_mode = str(payload.get("scheduler_mode") or cfg.get("scheduler_mode") or "DAILY_SLOTS").upper()
    if scheduler_mode not in {"DAILY_SLOTS","INTERVAL"}:
        scheduler_mode = "DAILY_SLOTS"
    scene_mode=normalize_scene_mode(payload.get("scene_mode") or cfg.get("scene_mode") or "GYM")
    scene_mix=normalize_scene_mix(payload.get("scene_mix") if "scene_mix" in payload else cfg.get("scene_mix") or [])
    if scene_mode=="MIX" and not scene_mix:
        scene_mix=["GYM","BEACH"]
    cfg.update({
        "scheduler_mode": scheduler_mode,
        "scene_mode": scene_mode,
        "scene_mix": scene_mix,
        "daily_slots": _normalize_daily_slots(payload.get("daily_slots") or cfg.get("daily_slots") or ["08:00","14:00","21:00"]),
        "daily_random_minutes": max(0, int(payload.get("daily_random_minutes", cfg.get("daily_random_minutes", 30)) or 0)),
        "resume_random_minutes": max(0, int(payload.get("resume_random_minutes", cfg.get("resume_random_minutes", 30)) or 0)),
        "first_publish_delay_minutes": max(0, int(payload.get("first_publish_delay_minutes", cfg.get("first_publish_delay_minutes", 0)) or 0)),
        "beat_image_count": max(3, min(10, int(payload.get("beat_image_count", cfg.get("beat_image_count", 7)) or 7))),
        "beat_duration_sec": float(payload.get("beat_duration_sec", cfg.get("beat_duration_sec", 15.0)) or 15.0),
        "i2v_clip_count": max(2, min(6, int(payload.get("i2v_clip_count", cfg.get("i2v_clip_count", 3)) or 3))),
        "i2v_clip_duration": str(payload.get("i2v_clip_duration") or cfg.get("i2v_clip_duration") or "8s"),
        "mode": str(cfg.get("mode") or profile.get("default_video_mode") or "AUTO").upper(),
        "beat_motion_preset": _simple_transition(profile),
        "image_concurrency": int(cfg.get("image_concurrency") or 9),
        "video_concurrency": int(cfg.get("video_concurrency") or 4),
    })
    interval = max(5, int(payload.get("publish_interval_minutes", profile.get("publish_interval_minutes") or 180) or 180))
    buffer_target = max(1, min(20, int(payload.get("buffer_target", profile.get("buffer_target") or 2) or 2))
    )
    dry_run = bool(payload.get("facebook_dry_run", profile.get("scheduler_dry_run", True)))
    with conn() as c:
        c.execute(
            "UPDATE page_profiles SET publish_interval_minutes=?,buffer_target=?,scheduler_dry_run=?,scheduler_config_json=?,updated_at=? WHERE id=?",
            (interval, buffer_target, 1 if dry_run else 0, dumps(cfg), utcnow(), profile_id),
        )
    persist_event_log({
        "type":"SIMPLE_SETTINGS_SAVED",
        "profileId":profile_id,
        "message":f"ÄÃ£ lÆ°u CÃ i Ä‘áº·t Â· scene={_scene_mode_label(scene_mode,scene_mix)} Â· {scheduler_mode} Â· buffer {buffer_target} Â· " + ("CHáº Y THá»¬" if dry_run else "ÄÄ‚NG THáº¬T"),
    })
    return {"ok":True,"profile":get_page_profile(profile_id)}


async def _simple_start_pipeline(profile_id: str):
    try:
        SIMPLE_START_CANCELLED.discard(profile_id)
        profile = get_page_profile(profile_id)
        if not profile:
            raise RuntimeError("KhÃ´ng tháº¥y cáº¥u hÃ¬nh Page")

        if not profile.get("persona_ready"):
            profile = await asyncio.to_thread(prepare_profile_persona, profile_id)

        missing = [a for a in ("left","right","back") if not profile.get(f"persona_{a}_master_path")]
        if missing:
            # START is a strict 3/3 gate. Partial angle packs are retried only for the
            # still-missing angles; scheduler is never enabled before LEFT/RIGHT/BACK exist.
            for persona_round in range(1, 4):
                if profile_id in SIMPLE_START_CANCELLED:
                    persist_event_log({"type":"SIMPLE_START_CANCELLED","profileId":profile_id,"message":"START bá»‹ STOP trÆ°á»›c khi Persona hoÃ n táº¥t."})
                    return
                profile = get_page_profile(profile_id) or {}
                missing = [a for a in ("left","right","back") if not profile.get(f"persona_{a}_master_path")]
                if not missing:
                    break
                persist_event_log({
                    "type":"SIMPLE_START_PERSONA_PREP",
                    "profileId":profile_id,
                    "message":f"START Persona vÃ²ng {persona_round}/3 â†’ táº¡o gÃ³c cÃ²n thiáº¿u: " + ",".join(missing),
                })
                await page_profile_generate_missing_angles(profile_id)

                round_deadline = time.monotonic() + 210
                last_missing = None
                while time.monotonic() < round_deadline:
                    if profile_id in SIMPLE_START_CANCELLED:
                        persist_event_log({"type":"SIMPLE_START_CANCELLED","profileId":profile_id,"message":"START bá»‹ STOP trÆ°á»›c khi Persona hoÃ n táº¥t."})
                        return
                    profile = get_page_profile(profile_id) or {}
                    missing = [a for a in ("left","right","back") if not profile.get(f"persona_{a}_master_path")]
                    if not missing:
                        break
                    if missing != last_missing:
                        persist_event_log({
                            "type":"SIMPLE_START_WAIT_PERSONA",
                            "profileId":profile_id,
                            "message":f"Persona vÃ²ng {persona_round}/3 Â· cÃ²n thiáº¿u " + ",".join(missing),
                        })
                        last_missing = list(missing)
                    # If the pack has already ended/failed, retry remaining angles in next round.
                    active = list_active_persona_angle_jobs(profile_id)
                    if not active and last_missing is not None:
                        break
                    await asyncio.sleep(2.0)
                if not missing:
                    break

            profile = get_page_profile(profile_id) or {}
            missing = [a for a in ("left","right","back") if not profile.get(f"persona_{a}_master_path")]
            if missing:
                raise RuntimeError("START bá»‹ cháº·n: Persona Pack chÆ°a Ä‘á»§ 3/3 sau 3 vÃ²ng Â· thiáº¿u " + ",".join(missing))
            persist_event_log({
                "type":"SIMPLE_START_PERSONA_READY",
                "profileId":profile_id,
                "message":"Persona Pack Ä‘á»§ 3/3 Â· cho phÃ©p báº­t scheduler.",
            })

        if profile_id in SIMPLE_START_CANCELLED:
            return

        profile = get_page_profile(profile_id) or {}
        # START now follows the saved Facebook setting instead of forcing live mode.
        live = not bool(profile.get("scheduler_dry_run", True))
        req = _simple_scheduler_request(profile, live=live)
        await scheduler_start(profile_id, req)
        persist_event_log({
            "type":"SIMPLE_START_READY",
            "profileId":profile_id,
            "message":"START hoÃ n táº¥t Persona â†’ Scheduler ON Â· " + ("ÄÄ‚NG THáº¬T" if live else "CHáº Y THá»¬"),
        })
        await ui_broadcast({"type":"SIMPLE_PAGE_STARTED","profileId":profile_id,"live":live})
    except Exception as exc:
        persist_event_log({"type":"SIMPLE_START_ERROR","profileId":profile_id,"message":str(exc)})
        await ui_broadcast({"type":"SIMPLE_PAGE_START_ERROR","profileId":profile_id,"error":str(exc)})
    finally:
        SIMPLE_START_TASKS.pop(profile_id, None)
        SIMPLE_START_CANCELLED.discard(profile_id)


@app.post("/api/simple/pages/{profile_id}/start")
async def simple_page_start(profile_id: str):
    _simple_reconcile_all()
    profile = get_page_profile(profile_id)
    if not profile:
        raise HTTPException(404, "KhÃ´ng tháº¥y cáº¥u hÃ¬nh Page")
    if not profile.get("facebook_page_id"):
        raise HTTPException(400, "Há»“ sÆ¡ chÆ°a map Facebook Page. HÃ£y import token Page tÆ°Æ¡ng á»©ng hoáº·c chá»n Ä‘Ãºng Page rá»“i LÆ¯U PAGE.")
    if not profile.get("persona_ready"):
        try:
            profile = await asyncio.to_thread(prepare_profile_persona, profile_id)
        except Exception as exc:
            raise HTTPException(400, str(exc))
    existing = SIMPLE_START_TASKS.get(profile_id)
    if existing and not existing.done():
        return {"ok":True,"start_pending":True,"already_starting":True}
    if profile.get("scheduler_enabled"):
        fill=await scheduler_fill_profile(profile_id)
        return {
            "ok":True,"start_pending":False,"already_enabled":True,
            "fill":fill,"status":scheduler_status(profile_id),
            "message":"Scheduler Ä‘Ã£ ON Â· vá»«a ensure/prefill buffer ngay."
        }
    SIMPLE_START_CANCELLED.discard(profile_id)
    task = spawn(_simple_start_pipeline(profile_id))
    SIMPLE_START_TASKS[profile_id] = task
    return {
        "ok":True,
        "start_pending":True,
        "persona_angle_count":int(profile.get("persona_angle_count") or 0),
        "message":"Äang hoÃ n thiá»‡n Persona; xong sáº½ tá»± cháº¡y theo CÃ i Ä‘áº·t Ä‘Ã£ lÆ°u.",
    }


@app.post("/api/simple/pages/{profile_id}/stop")
async def simple_page_stop(profile_id: str):
    SIMPLE_START_CANCELLED.add(profile_id)
    task=SIMPLE_START_TASKS.get(profile_id)
    if task and not task.done():
        task.cancel()
        SIMPLE_START_TASKS.pop(profile_id,None)
    # scheduler_stop is a synchronous FastAPI handler returning dict.
    return scheduler_stop(profile_id)


@app.post("/api/facebook/pages/import-token")
def facebook_pages_import_token(req: FacebookImportTokenRequest):
    token = str(req.token or "").strip()
    if not token:
        raise HTTPException(400, "Thiáº¿u token Facebook")
    # 1) User token: import all Pages from /me/accounts.
    first_error = None
    try:
        data = fb_request_json(
            "GET",
            facebook_graph_url("me/accounts"),
            params={"fields":"id,name,access_token,tasks","limit":100,"access_token":token},
        )
        saved = 0
        for p in data.get("data") or []:
            pid = str(p.get("id") or "").strip()
            page_token = str(p.get("access_token") or "").strip()
            if pid and page_token:
                with conn() as c:
                    c.execute("DELETE FROM fb_page_ignored WHERE id=?", (pid,))
                save_fb_page(pid, str(p.get("name") or pid), page_token, p.get("tasks") or [])
                saved += 1
        if saved:
            links = _simple_reconcile_all()
            return {"ok":True,"kind":"user_token","saved":saved,"profile_links":len(links),"pages":list_fb_pages()}
    except Exception as exc:
        first_error = str(exc)

    # 2) Page token: /me resolves directly to the Page; store the same token.
    try:
        data = fb_request_json(
            "GET",
            facebook_graph_url("me"),
            params={"fields":"id,name,category","access_token":token},
        )
        pid = str(data.get("id") or "").strip()
        category = str(data.get("category") or "").strip()
        if not pid or not category:
            raise ValueError("Token nÃ y khÃ´ng xÃ¡c Ä‘á»‹nh Ä‘Æ°á»£c lÃ  Page Access Token")
        name = str(data.get("name") or pid)
        with conn() as c:
            c.execute("DELETE FROM fb_page_ignored WHERE id=?", (pid,))
        save_fb_page(pid, name, token, [])
        links = _simple_reconcile_all()
        return {"ok":True,"kind":"page_token","saved":1,"profile_links":len(links),"pages":list_fb_pages()}
    except Exception as exc:
        detail = str(exc)
        if first_error:
            detail = f"User token: {first_error} | Page token: {detail}"
        raise HTTPException(400, detail)


@app.get("/api/facebook/pages")
def facebook_pages():
    return list_fb_pages()


@app.post("/api/facebook/pages")
def facebook_page_save(req: FacebookPageSave):
    page_id = req.page_id.strip()
    # Náº¿u ngÆ°á»i dÃ¹ng chá»§ Ä‘á»™ng Save thá»§ cÃ´ng thÃ¬ bá» Page khá»i ignore list.
    with conn() as c:
        c.execute("DELETE FROM fb_page_ignored WHERE id=?", (page_id,))
    save_fb_page(page_id, req.name.strip(), req.access_token.strip())
    links = _simple_reconcile_all()
    return {"ok": True, "page_id": page_id, "profile_links": len(links)}


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
        raise HTTPException(400, "Thiáº¿u User Access Token")
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
        links = _simple_reconcile_all()
        return {"ok": True, "saved": count, "skipped_ignored": skipped, "profile_links": len(links), "pages": list_fb_pages()}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/facebook/pages/{page_id}/test")
def facebook_page_test(page_id: str):
    page = get_fb_page_secret(page_id)
    if not page:
        raise HTTPException(404, "ChÆ°a lÆ°u Page/token")
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
        raise HTTPException(404, "ChÆ°a lÆ°u Facebook Page/token")
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
        raise HTTPException(400, "Thiáº¿u video_path")
    p = Path(raw)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    if not p.exists():
        raise HTTPException(404, f"KhÃ´ng tháº¥y file: {p}")
    return {"ok": True, "path": str(p), "preflight": ffprobe_info(p)}


async def _agent_disconnect_grace(job_id: str, reason: str="Flow extension máº¥t káº¿t ná»‘i") -> None:
    await asyncio.sleep(RETRY_AGENT_GRACE_SEC)
    job=get_flow_job(job_id) or {}
    status=str(job.get("status") or "")
    if status in {"done","flow_done","downloading_images","downloading","rendering","qc","queued"}:
        return
    # A disconnect is infrastructure. Keep/retry the SAME queue item so buffer fill
    # does not create a second replacement content item.
    if status in {"interrupted","dispatching","running","partial_failed","failed"}:
        ok=await _requeue_same_job_immediate(job_id,reason,stage="disconnect")
        if ok:
            return
    if get_content_queue_by_flow(job_id):
        mark_content_queue_failed(job_id,reason,stage="disconnect")
    else:
        _schedule_factory_retry(job_id,reason,stage="disconnect")


@app.websocket("/ws")
async def extension_ws(ws: WebSocket):
    await ws.accept()
    connection_id = f"agent_{uuid.uuid4().hex[:10]}"
    agent = AgentRuntime(connection_id, ws)
    AGENTS[connection_id] = agent
    persist_event_log({
        "type":"AGENT_WS_ACCEPTED","agentId":connection_id,
        "message":f"WS ACCEPTED Â· /ws -> extension_ws Â· agent={connection_id}",
    })
    await ui_broadcast({"type": "AGENT_CONNECTED", "agent": agent.public()})
    video_upload_ctx: dict[str, Any] | None = None
    try:
        while True:
            packet = await ws.receive()
            if packet.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            agent.last_seen = utcnow()
            raw_bytes = packet.get("bytes")
            if raw_bytes is not None:
                if not video_upload_ctx:
                    continue
                tmp_path = Path(video_upload_ctx["tmp_path"])
                with tmp_path.open("ab") as f:
                    f.write(raw_bytes)
                video_upload_ctx["received"] = int(video_upload_ctx.get("received") or 0) + len(raw_bytes)
                continue
            raw = packet.get("text")
            if raw is None:
                continue
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
                agent.ready = bool(msg.get("failSafeReady") is True)
                compat = extension_version_compatible(agent.version)
                await ui_broadcast({"type": "AGENT_HELLO", "agent": agent.public()})
                if not compat:
                    agent.busy = False
                    agent.job_id = None
                    persist_event_log({
                        "type": "EXTENSION_VERSION_MISMATCH", "agentId": agent.id,
                        "message": f"BLOCK worker v{agent.version or '?'} Â· cáº§n >= {MIN_EXTENSION_VERSION} Â· STOP_ALL Â· KHÃ”NG gá»­i job Flow",
                    })
                    await ui_broadcast({
                        "type":"EXTENSION_VERSION_MISMATCH", "agent":agent.public(),
                        "requiredVersion":MIN_EXTENSION_VERSION,
                    })
                    try:
                        await agent.ws.send_text(dumps({"type":"STOP_ALL","reason":"extension_version_mismatch","requiredVersion":MIN_EXTENSION_VERSION}))
                    except Exception:
                        pass
                    continue
                persist_event_log({
                    "type":"EXTENSION_VERSION_OK","agentId":agent.id,
                    "message":f"Worker v{agent.version} COMPATIBLE Â· ready={'YES' if agent.ready else 'NO'} Â· yÃªu cáº§u >= {MIN_EXTENSION_VERSION}"
                })
                if agent.ready:
                    await dispatch_jobs()
                    for _profile in list_page_profiles():
                        if _profile.get("scheduler_enabled") and _profile.get("enabled"):
                            spawn(scheduler_fill_profile(str(_profile["id"])))
            elif mtype == "AGENT_READY":
                agent.runtime = msg.get("runtime") or agent.runtime
                agent.ready = True
                agent.last_seen = utcnow()
                if not agent.job_id: _touch_agent(agent,"idle",f"agent-ready:{agent.id}")
                persist_event_log({
                    "type":"AGENT_READY","agentId":agent.id,
                    "message":f"Flow Agent v{agent.version or '?'} READY Â· browser automation Ä‘Ã£ má»Ÿ khÃ³a."
                })
                await ui_broadcast({"type":"AGENT_READY","agent":agent.public()})
                await dispatch_jobs()
                for _profile in list_page_profiles():
                    if _profile.get("scheduler_enabled") and _profile.get("enabled"):
                        spawn(scheduler_fill_profile(str(_profile["id"])))
            elif mtype == "AGENT_HEARTBEAT":
                token=msg.get("progressUpdatedAt"); hb_job=str(msg.get("jobId") or "")
                if agent.busy and agent.job_id and (not hb_job or hb_job==agent.job_id): _touch_agent(agent,token=token)
                try: await agent.ws.send_text(dumps({"type":"HEARTBEAT_ACK","ts":server_now_iso()}))
                except Exception: pass
            elif mtype == "PONG":
                await ui_broadcast({"type": "AGENT_PONG", "agentId": agent.id})
            elif mtype == "STOP_ALL_ACK":
                agent.ready = False
                agent.busy = False
                agent.job_id = None
                _touch_agent(agent,"idle",f"stop-ack:{agent.id}")
                persist_event_log({"type": "STOP_ALL_ACK", "agentId": agent.id, "message": str(msg.get("reason") or "extension_stopped")})
                await ui_broadcast({"type":"STOP_ALL_ACK","agentId":agent.id,"reason":msg.get("reason")})
            elif mtype in {"VIDEO_DOWNLOAD_URL", "VIDEO_DOWNLOAD_URL_READY"}:
                jid=str(msg.get("jobId") or agent.job_id or "")
                sid=int(msg.get("sceneId") or 0)
                mid=str(msg.get("mediaId") or "")
                signed_url=str(msg.get("url") or msg.get("signedUrl") or "")
                if not jid or sid<=0 or not mid or not _safe_remote_media_url(signed_url):
                    await ws.send_text(dumps({"type":"VIDEO_URL_DOWNLOAD_ACK","ok":False,"jobId":jid,"sceneId":sid,"mediaId":mid,"error":"Signed URL thiáº¿u/khÃ´ng pháº£i HTTPS"}))
                    continue
                # STREAM-FIRST: save URL immediately. Do NOT download child video unless FFmpeg later needs fallback.
                with conn() as c:
                    row=c.execute("SELECT id FROM assets WHERE job_id=? AND scene_id=? AND kind='video' AND media_id=? ORDER BY created_at DESC LIMIT 1",(jid,sid,mid)).fetchone()
                    if row:
                        c.execute("UPDATE assets SET url=? WHERE id=?",(signed_url,row["id"]))
                    else:
                        add_asset(jid,sid,"video",url=signed_url,local_path=None,media_id=mid,metadata={"source":"SIGNED_URL_STREAM","stream_first":True})
                persist_event_log({"type":"VIDEO_STREAM_READY","jobId":jid,"sceneId":sid,"message":f"Signed URL READY Â· stream trá»±c tiáº¿p Â· scene={sid} Â· media={mid[:8]}â€¦"})
                await ui_broadcast({"type":"VIDEO_STREAM_READY","jobId":jid,"sceneId":sid,"url":signed_url})
                await ws.send_text(dumps({"type":"VIDEO_URL_DOWNLOAD_ACK","ok":True,"jobId":jid,"sceneId":sid,"mediaId":mid,"streamOnly":True,"url":signed_url}))
            elif mtype == "VIDEO_UPLOAD_BEGIN":
                jid = str(msg.get("jobId") or "")
                sid = int(msg.get("sceneId") or 0)
                mid = str(msg.get("mediaId") or "")
                expected_size = int(msg.get("size") or 0)
                if not jid or sid <= 0 or not mid or expected_size <= 0:
                    await ws.send_text(dumps({"type":"VIDEO_UPLOAD_ACK","ok":False,"jobId":jid,"sceneId":sid,"mediaId":mid,"error":"VIDEO_UPLOAD_BEGIN thiáº¿u metadata"}))
                    video_upload_ctx = None
                    continue
                safe_job = re.sub(r"[^A-Za-z0-9._-]+", "_", jid)[:100]
                safe_mid = re.sub(r"[^A-Za-z0-9._-]+", "_", mid)[:80]
                out_dir = OUTPUT_DIR / "flow_downloads" / safe_job
                out_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = out_dir / f"scene_{sid:03d}_{safe_mid}.part"
                tmp_path.unlink(missing_ok=True)
                video_upload_ctx = {
                    "job_id": jid, "scene_id": sid, "media_id": mid,
                    "size": expected_size, "received": 0, "tmp_path": str(tmp_path),
                }
            elif mtype == "VIDEO_UPLOAD_END":
                jid = str(msg.get("jobId") or "")
                sid = int(msg.get("sceneId") or 0)
                mid = str(msg.get("mediaId") or "")
                ctx = video_upload_ctx
                video_upload_ctx = None
                if not ctx or ctx.get("job_id") != jid or int(ctx.get("scene_id") or 0) != sid or ctx.get("media_id") != mid:
                    await ws.send_text(dumps({"type":"VIDEO_UPLOAD_ACK","ok":False,"jobId":jid,"sceneId":sid,"mediaId":mid,"error":"KhÃ´ng khá»›p VIDEO_UPLOAD_BEGIN/END"}))
                    continue
                tmp_path = Path(str(ctx["tmp_path"]))
                expected_size = int(ctx.get("size") or 0)
                received = int(ctx.get("received") or 0)
                actual_size = tmp_path.stat().st_size if tmp_path.exists() else 0
                if actual_size <= 4096 or received != actual_size or (expected_size > 0 and actual_size != expected_size):
                    tmp_path.unlink(missing_ok=True)
                    error = f"Video binary chÆ°a Ä‘á»§: received={received}, file={actual_size}, expected={expected_size}"
                    await ws.send_text(dumps({"type":"VIDEO_UPLOAD_ACK","ok":False,"jobId":jid,"sceneId":sid,"mediaId":mid,"error":error}))
                    persist_event_log({"type":"VIDEO_UPLOAD_ERROR","jobId":jid,"sceneId":sid,"message":error})
                    continue
                final_path = tmp_path.with_suffix(".mp4")
                final_path.unlink(missing_ok=True)
                tmp_path.replace(final_path)
                with conn() as c:
                    row=c.execute("SELECT id,metadata_json FROM assets WHERE job_id=? AND scene_id=? AND kind='video' AND media_id=? ORDER BY created_at DESC LIMIT 1",(jid,sid,mid)).fetchone()
                    if row:
                        c.execute("UPDATE assets SET local_path=?,metadata_json=? WHERE id=?",(str(final_path.resolve()),_merge_metadata_json(row["metadata_json"],{"source":"WS_BINARY_TRANSFER","size":actual_size}),row["id"]))
                    else:
                        add_asset(jid,sid,"video",local_path=str(final_path.resolve()),media_id=mid,metadata={"source":"WS_BINARY_TRANSFER","size":actual_size})
                persist_event_log({"type":"VIDEO_FILE_READY","jobId":jid,"sceneId":sid,"message":f"Video táº£i tháº³ng vÃ o server Â· scene {sid} Â· {actual_size} bytes"})
                await ui_broadcast({"type":"VIDEO_FILE_READY","jobId":jid,"sceneId":sid,"localPath":str(final_path.resolve())})
                await ws.send_text(dumps({"type":"VIDEO_UPLOAD_ACK","ok":True,"jobId":jid,"sceneId":sid,"mediaId":mid,"size":actual_size,"localPath":str(final_path.resolve())}))
            elif mtype == "FLOW_JOB_ACCEPTED":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                if job_id:
                    agent.busy=True; agent.job_id=job_id; _touch_agent(agent,"running",f"accepted:{job_id}")
                    update_flow_job(job_id,status="running",agent_id=agent.id,error=None)
                    await ui_broadcast({"type": "FLOW_JOB_ACCEPTED", "jobId": job_id, "runId": msg.get("runId")})
            elif mtype == "FLOW_JOB_REJECTED":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                if job_id:
                    update_flow_job(job_id, status="failed", error=msg.get("error") or "Agent rejected")
                    if get_content_queue_by_flow(job_id):
                        mark_content_queue_failed(job_id, msg.get("error") or "Agent rejected", stage="dispatch")
                agent.busy=False; agent.job_id=None; _touch_agent(agent,"idle",f"rejected:{job_id}")
                await ui_broadcast({"type":"FLOW_JOB_REJECTED","jobId":job_id,"error":msg.get("error")})
                await dispatch_jobs()
            elif mtype == "FLOW_JOB_RESULT":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                if job_id:
                    await process_flow_result(job_id, msg, agent)
            elif mtype == "FLOW_JOB_INTERRUPTED":
                job_id = str(msg.get("jobId") or agent.job_id or "")
                if job_id:
                    update_flow_job(job_id, status="interrupted", error=msg.get("error") or "Agent interrupted")
                    if get_content_queue_by_flow(job_id):
                        mark_content_queue_failed(job_id, msg.get("error") or "Agent interrupted", stage="disconnect")
                agent.busy=False; agent.job_id=None; _touch_agent(agent,"idle",f"interrupted:{job_id}")
                await ui_broadcast({"type":"FLOW_JOB_INTERRUPTED","jobId":job_id})
                await dispatch_jobs()
            elif mtype == "IMAGE_READY":
                # Future-compatible: the current v14.5.4 extension sends images in FLOW_JOB_RESULT,
                # but V1 server already supports per-image streaming if the extension later emits IMAGE_READY.
                job_id = str(msg.get("jobId") or agent.job_id or "")
                scene_id = int(msg.get("sceneId") or 0)
                url = msg.get("url")
                media_id = msg.get("mediaId")
                local_path = await asyncio.to_thread(cache_image_sync, str(url or ""), job_id, scene_id, media_id)
                asset_id = add_asset(job_id, scene_id, "image", url=url, local_path=local_path, media_id=media_id, title=msg.get("title"), metadata=msg)
                await ui_broadcast({"type": "IMAGE_READY", "jobId": job_id, "sceneId": scene_id, "assetId": asset_id})
            elif mtype == "IMAGE_FILE_READY":
                jid=str(msg.get("jobId") or agent.job_id or ""); sid=int(msg.get("sceneId") or 0); lp=str(msg.get("localPath") or "").strip(); mid=str(msg.get("mediaId") or "").strip()
                valid=await asyncio.to_thread(_valid_local_image_file,lp)
                if not (jid and sid and lp and valid):
                    err=f"IMAGE_FILE_READY invalid Â· scene={sid} Â· path={lp or '-'}"; persist_event_log({"type":"IMAGE_FILE_ERROR","jobId":jid,"sceneId":sid,"mediaId":mid,"message":err}); await ui_broadcast({"type":"IMAGE_FILE_ERROR","jobId":jid,"sceneId":sid,"mediaId":mid,"error":err}); continue
                with conn() as c:
                    row=c.execute("SELECT id,metadata_json FROM assets WHERE job_id=? AND scene_id=? AND kind='image' AND media_id=? ORDER BY created_at DESC LIMIT 1",(jid,sid,mid)).fetchone() if mid else None
                    if not row: row=c.execute("SELECT id,metadata_json FROM assets WHERE job_id=? AND scene_id=? AND kind='image' ORDER BY created_at ASC LIMIT 1",(jid,sid)).fetchone()
                    if row: c.execute("UPDATE assets SET local_path=?,media_id=COALESCE(NULLIF(media_id,''),?),metadata_json=? WHERE id=?",(lp,mid or None,_merge_metadata_json(row["metadata_json"],{"source":"IMAGE_FILE_READY","recovery":bool(msg.get("recovery"))}),row["id"]))
                    else: add_asset(jid,sid,"image",local_path=lp,media_id=mid or None,metadata={"source":"IMAGE_FILE_READY","recovery":bool(msg.get("recovery"))})
                FACTORY_RESUME_RETRY_AFTER.pop(jid,None)
                _touch_agent(agent,"recovering_images",f"image-file:{jid}:{sid}")
                persist_event_log({"type":"IMAGE_FILE_READY","jobId":jid,"sceneId":sid,"mediaId":mid,"localPath":lp,"message":f"IMAGE LOCAL READY Â· scene {sid}"}); await ui_broadcast({"type":"IMAGE_FILE_READY","jobId":jid,"sceneId":sid,"mediaId":mid,"localPath":lp,"exists":True})
            elif mtype == "IMAGE_FILE_ERROR":
                jid = str(msg.get("jobId") or agent.job_id or "")
                persist_event_log({
                    "type":"IMAGE_FILE_ERROR","jobId":jid,"sceneId":msg.get("sceneId"),
                    "mediaId":msg.get("mediaId"),"message":str(msg.get("error") or "Recovery image lá»—i"),
                })
                await ui_broadcast({
                    "type":"IMAGE_FILE_ERROR","jobId":jid,"sceneId":msg.get("sceneId"),
                    "mediaId":msg.get("mediaId"),"error":msg.get("error"),
                })
            elif mtype == "VIDEO_FILE_READY":
                jid=str(msg.get("jobId") or agent.job_id or ""); sid=int(msg.get("sceneId") or 0); lp=str(msg.get("localPath") or "").strip(); mid=str(msg.get("mediaId") or "").strip()
                valid=await asyncio.to_thread(_valid_local_video_file,lp)
                if not (jid and sid and lp and valid):
                    err=f"VIDEO_FILE_READY invalid Â· scene={sid} Â· path={lp or '-'}"; persist_event_log({"type":"VIDEO_FILE_ERROR","jobId":jid,"sceneId":sid,"message":err}); await ui_broadcast({"type":"VIDEO_FILE_ERROR","jobId":jid,"sceneId":sid,"error":err}); continue
                with conn() as c:
                    row=c.execute("SELECT id,metadata_json FROM assets WHERE job_id=? AND scene_id=? AND kind='video' AND media_id=? ORDER BY created_at DESC LIMIT 1",(jid,sid,mid)).fetchone() if mid else None
                    if not row: row=c.execute("SELECT id,metadata_json FROM assets WHERE job_id=? AND scene_id=? AND kind='video' ORDER BY created_at ASC LIMIT 1",(jid,sid)).fetchone()
                    if row: c.execute("UPDATE assets SET local_path=?,media_id=COALESCE(NULLIF(media_id,''),?),metadata_json=? WHERE id=?",(lp,mid or None,_merge_metadata_json(row["metadata_json"],{"source":"VIDEO_FILE_READY","recovery":bool(msg.get("recovery"))}),row["id"]))
                    else: add_asset(jid,sid,"video",local_path=lp,media_id=mid or None,metadata={"source":"VIDEO_FILE_READY","recovery":bool(msg.get("recovery"))})
                FACTORY_RESUME_RETRY_AFTER.pop(jid,None)
                _touch_agent(agent,"recovering_videos",f"video-file:{jid}:{sid}")
                persist_event_log({"type":"VIDEO_FILE_READY","jobId":jid,"sceneId":sid,"message":f"Video local sáºµn sÃ ng Â· scene {sid}"}); await ui_broadcast({"type":"VIDEO_FILE_READY","jobId":jid,"sceneId":sid,"localPath":lp})
            elif mtype == "VIDEO_FILE_ERROR":
                jid = str(msg.get("jobId") or agent.job_id or "")
                persist_event_log({"type":"VIDEO_FILE_ERROR","jobId":jid,"sceneId":msg.get("sceneId"),"message":str(msg.get("error") or "Download video lá»—i")})
                await ui_broadcast({"type":"VIDEO_FILE_ERROR","jobId":jid,"sceneId":msg.get("sceneId"),"error":msg.get("error")})
            elif mtype == "VIDEO_DOWNLOAD_SUMMARY":
                jid = str(msg.get("jobId") or agent.job_id or "")
                await ui_broadcast({"type":"VIDEO_DOWNLOAD_SUMMARY","jobId":jid,"sceneId":msg.get("sceneId"),"expected":msg.get("expected"),"downloads":msg.get("downloads") or []})
            else:
                await ui_broadcast({"type": "AGENT_EVENT", "agentId": agent.id, "message": msg})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if video_upload_ctx:
            try:
                Path(str(video_upload_ctx.get("tmp_path") or "")).unlink(missing_ok=True)
            except Exception:
                pass
        old_job=agent.job_id
        AGENTS.pop(connection_id,None); AGENT_RECOVERY_LOCKS.pop(connection_id,None)
        if old_job:
            job=get_flow_job(old_job) or {}; status=str(job.get("status") or "")
            if old_job in FACTORY_FINALIZE_IN_FLIGHT and status in {"flow_done","downloading_images","downloading","rendering","qc"}:
                persist_event_log({"type":"AGENT_DISCONNECTED_DURING_FINALIZE","agentId":connection_id,"jobId":old_job,"message":"Agent máº¥t káº¿t ná»‘i khi finalize; khÃ´ng overwrite state ngay."})
            elif status not in {"done","qc_failed","failed","queued","retry_wait"}:
                update_flow_job(old_job,status="interrupted",error="Flow extension máº¥t káº¿t ná»‘i Â· grace",agent_id=None)
                spawn(_agent_disconnect_grace(old_job))
        await ui_broadcast({"type":"AGENT_DISCONNECTED","agentId":connection_id})


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
        print(f"[AGENT BRIDGE] KhÃ´ng ná»‘i Ä‘Æ°á»£c web server {HOST}:{WEB_PORT}: {exc}")
        client_writer.close()
        try:
            await client_writer.wait_closed()
        except Exception:
            pass
        return

    # Transparent TCP proxy: WebSocket handshake / frames stay untouched.
    # Extension keeps ws://127.0.0.1:8786/ws while FastAPI lives on a separate UI port.
    left = asyncio.create_task(_proxy_pipe(client_reader, server_writer))
    right = asyncio.create_task(_proxy_pipe(server_reader, client_writer))
    done, pending = await asyncio.wait({left, right}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def serve_dual_port() -> None:
    import uvicorn
    global WEB_PORT

    # DB initialization is owned by FastAPI lifespan. Do it exactly once.
    # Calling init_db() here and again in lifespan caused Windows builds to
    # sometimes stall at "Waiting for application startup".
    WEB_PORT = _select_web_port()
    config = uvicorn.Config(app, host=HOST, port=WEB_PORT, reload=False, log_level="info")
    web_server = uvicorn.Server(config)
    web_task = asyncio.create_task(web_server.serve())

    async def _watch_uvicorn_shutdown_signal() -> None:
        # Uvicorn normally flips should_exit before it tears down websocket
        # connections. Send STOP_ALL in that window so it is truly the last
        # server command the extension receives.
        while not web_server.should_exit and not getattr(web_server, "force_exit", False):
            if web_task.done():
                return
            await asyncio.sleep(0.10)
        try:
            sent = await _send_stop_all_to_agents("server_shutdown_signal")
            print(f"[SHUTDOWN SIGNAL] STOP_ALL sent={sent}", flush=True)
        except Exception as exc:
            print(f"[SHUTDOWN SIGNAL] STOP_ALL lá»—i: {exc}", flush=True)

    stop_watch_task = asyncio.create_task(_watch_uvicorn_shutdown_signal(), name="shutdown-stop-all-watch")

    # Wait until the actual UI/API socket is listening before opening the bridge.
    for _ in range(200):
        if web_server.started:
            break
        if web_task.done():
            await web_task
            if not web_server.started:
                raise RuntimeError("Uvicorn startup tháº¥t báº¡i. Xem lá»—i ngay phÃ­a trÃªn / server_crash.log.")
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
    print(f"Giá»¯ extension cÅ© á»Ÿ {AGENT_PORT}; trÃ¬nh duyá»‡t má»Ÿ UI á»Ÿ {WEB_PORT}.\n")

    try:
        await web_task
    finally:
        stop_watch_task.cancel()
        await asyncio.gather(stop_watch_task, return_exceptions=True)
        if proxy_server is not None:
            proxy_server.close()
            await proxy_server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(serve_dual_port())
    except KeyboardInterrupt:
        pass


