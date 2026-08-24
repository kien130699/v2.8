from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import random
import math
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, quote, parse_qs
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import requests


VN_TZ = timezone(timedelta(hours=7))


class CharacterSave(BaseModel):
    id: str | None = None
    name: str = Field(min_length=2, max_length=80)
    role: str = Field(default="mother", max_length=40)
    age_label: str = Field(default="30", max_length=40)
    visual_prompt: str = Field(min_length=5, max_length=3000)
    voice_prompt: str = Field(min_length=3, max_length=1000)
    reference_path: str | None = None
    enabled: bool = True


class CharacterGenerateRequest(BaseModel):
    character_id: str
    image_model: str = "Nano Banana 2"
    aspect_ratio: str = "9:16"


class CharacterSetSave(BaseModel):
    id: str | None = None
    name: str = Field(min_length=2, max_length=100)
    mother_character_id: str
    child_character_id: str
    father_character_id: str | None = None
    enabled: bool = True


class PlanRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    template_mode: str = Field(default="parenting", max_length=48)
    dialogue_order: str = Field(default="mother_first", max_length=32)
    character_set_id: str
    scene_count: int = Field(default=4, ge=1, le=8)
    dialogue_turns_per_scene: int = Field(default=4, ge=1, le=12)
    model: str = ""
    tone: str = "ấm áp, tự nhiên, hữu ích, không phán xét"


class TestSceneRequest(BaseModel):
    character_set_id: str
    mother_text: str = Field(default="", max_length=500)
    child_text: str = Field(default="", max_length=500)
    dialogue: list[dict[str, str]] = Field(default_factory=list)
    scene_description: str = Field(default="Phòng ngủ trẻ em ấm áp vào buổi tối, bên cạnh một chiếc lều ngủ nhỏ có đèn fairy lights.", max_length=1500)
    image_model: str = "Nano Banana 2"
    video_model: str = "Veo 3.1 - Fast"
    video_duration: str = "8s"
    continuation_mode: str = "auto"
    product_id: str | None = None
    product_visible: bool = True
    product_ad_text: str = Field(default="", max_length=800)
    ad_voice_prompt: str = "Vietnamese adult female advertising voice, clear, warm, confident, slightly faster than the mother voice, natural social-commerce delivery."


class StoryGenerateRequest(BaseModel):
    character_set_id: str
    topic: str = Field(min_length=3, max_length=500)
    template_mode: str = Field(default="parenting", max_length=48)
    dialogue_order: str = Field(default="mother_first", max_length=32)
    scene_count: int = Field(default=4, ge=1, le=8)
    dialogue_turns_per_scene: int = Field(default=4, ge=1, le=12)
    model: str = ""
    tone: str = "ấm áp, tự nhiên, hữu ích, không phán xét"
    image_model: str = "Nano Banana 2"
    video_model: str = "Veo 3.1 - Fast"
    video_duration: str = "8s"
    burn_subtitles: bool = True
    auto_publish: bool = False
    facebook_page_id: str | None = None
    facebook_dry_run: bool = True
    continuation_mode: str = "auto"


class AutoFbTopicsRequest(BaseModel):
    base_topic: str = Field(min_length=3, max_length=500)
    count: int = Field(default=10, ge=3, le=50)
    model: str = ""
    audience: str = "phụ huynh có con nhỏ"
    page_style: str = "ấm áp, thực tế, dễ áp dụng"


class AutoFbQueueRequest(BaseModel):
    character_set_id: str
    base_topic: str = Field(default="", max_length=500)
    topics: list[str] = Field(default_factory=list)
    scene_count: int = Field(default=4, ge=1, le=8)
    dialogue_turns_per_scene: int = Field(default=4, ge=1, le=12)
    model: str = ""
    image_model: str = "Nano Banana 2"
    video_model: str = "Veo 3.1 - Fast"
    video_duration: str = "8s"
    burn_subtitles: bool = True
    auto_publish: bool = False
    facebook_page_id: str | None = None
    facebook_dry_run: bool = True
    continuation_mode: str = "auto"


class ProductInspectRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    model: str = ""
    force_refresh: bool = False


def _clean_shopee_search_keyword(raw: Any) -> str:
    keyword = re.sub(r"\s+", " ", str(raw or "")).strip()[:120]
    if re.match(r"^(?:count|limit|số\s*(?:sp|sản\s*phẩm)|sản\s*phẩm)\s*[:=]?\s*\d+$", keyword, re.I):
        return ""
    return keyword


class ShopeeSearchPreviewRequest(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=20)
    count: int = Field(default=5, ge=1, le=10)
    content_pillar: str = Field(default="mixed", pattern="^(mixed|mother_child|mother_teaches)$")
    affiliate_id: str = Field(default="", max_length=80)


class ProductPlanRequest(BaseModel):
    product_id: str
    character_set_id: str
    story_scene_count: int = Field(default=0, ge=0, le=4)  # 0 = derive from output_duration
    total_dialogue_turns: int = Field(default=0, ge=0, le=12)  # 0 = auto from Vietnamese word/time budget
    output_duration: str = Field(default="32s", max_length=16)  # 8s / 16s / 24s / 32s
    angle_hint: str = Field(default="", max_length=500)
    model: str = ""
    product_reveal_scene: int = Field(default=1, ge=1, le=4)
    story_template_id: str = Field(default="auto", max_length=48)
    # V2.0: when story_scene_count=1 (Test 1 Scene), generate ONE coherent beat
    # instead of collapsing setup + reveal + payoff + resolution into a broken scene.
    single_scene_phase: str = Field(default="product_reveal", max_length=32)
    variation_seed: str = Field(default="", max_length=128)
    previous_dialogue: list[str] = Field(default_factory=list)


class ProductGenerateRequest(ProductPlanRequest):
    affiliate_url: str = ""
    image_model: str = "Nano Banana 2"
    video_model: str = "Veo 3.1 - Fast"
    video_duration: str = "8s"
    burn_subtitles: bool = True
    auto_publish: bool = False
    facebook_page_id: str | None = None
    facebook_dry_run: bool = True
    ad_voice_prompt: str = "Vietnamese adult female advertising voice, clear, warm, confident, slightly faster than the mother voice, natural social-commerce delivery."


class AutoFbPageProfileSave(BaseModel):
    profile_id: str | None = Field(default=None, max_length=128)
    facebook_page_id: str = Field(min_length=2, max_length=128)
    name: str = Field(default="Page X", max_length=120)
    posts_per_day: int = Field(default=3, ge=1, le=24)
    start_hour: int = Field(default=8, ge=0, le=23)
    end_hour: int = Field(default=22, ge=1, le=23)
    dry_run: bool = True
    enabled: bool = True


class AutoFbCampaignStartRequest(BaseModel):
    campaign_id: str | None = None
    name: str = Field(default="Auto FB Hybrid", max_length=120)
    character_set_id: str
    shopee_urls: list[str] = Field(default_factory=list, max_length=50)
    shopee_auto_search: bool = False
    shopee_search_topics: list[str] = Field(default_factory=list, max_length=20)
    shopee_search_count: int = Field(default=5, ge=0, le=10)
    shopee_affiliate_id: str = Field(default="", max_length=80)
    content_pillar: str = Field(default="mixed", pattern="^(mixed|mother_child|mother_teaches)$")
    page_profile_id: str
    script_model: str = ""
    image_model: str = "Nano Banana 2"
    video_model: str = "Veo 3.1 - Fast"
    output_duration: str = Field(default="32s", max_length=16)
    candidate_pool_size: int = Field(default=5, ge=0, le=250)
    selected_per_batch: int = Field(default=10, ge=1, le=30)
    burn_subtitles: bool = True
    music_enabled: bool = True
    music_provider: str = Field(default="auto", pattern="^(auto|local|jamendo|mubert|off)$")
    music_style: str = Field(default="dynamic electronic playful upbeat", max_length=160)
    music_intensity: str = Field(default="high", pattern="^(low|medium|high)$")
    music_volume: float = Field(default=0.12, ge=0.0, le=0.5)
    music_ducking: bool = True


class AutoFbCampaignLinksUpdate(BaseModel):
    shopee_urls: list[str] = Field(default_factory=list, max_length=50)


class AutoFbCampaignStateUpdate(BaseModel):
    action: str = Field(pattern="^(start|pause|resume|stop)$")


class ParentingHandler:
    def __init__(
        self,
        *,
        db_path: Path,
        output_dir: Path,
        create_flow_job: Callable[[str, list[dict[str, Any]], dict[str, Any]], str],
        default_flow_config: Callable[..., dict[str, Any]],
        dispatch_jobs: Callable[[], Any],
        get_flow_job: Callable[[str], dict[str, Any] | None],
        update_flow_job: Callable[..., None],
        add_asset: Callable[..., str],
        router9_chat_json: Callable[..., dict[str, Any]],
        router9_enabled: Callable[[], bool],
        ui_broadcast: Callable[[dict[str, Any]], Any],
        spawn: Callable[[Any], Any],
        create_publish_job: Callable[[Any], str] | None = None,
        facebook_publish_request_cls: Any | None = None,
        inspect_product_url: Callable[[str], Any] | None = None,
        search_products: Callable[[str, int], Any] | None = None,
    ):
        self.db_path = Path(db_path)
        self.output_dir = Path(output_dir)
        self.create_flow_job = create_flow_job
        self.default_flow_config = default_flow_config
        self.dispatch_jobs = dispatch_jobs
        self.get_flow_job = get_flow_job
        self.update_flow_job = update_flow_job
        self.add_asset = add_asset
        self.router9_chat_json = router9_chat_json
        self.router9_enabled = router9_enabled
        self.ui_broadcast = ui_broadcast
        self.spawn = spawn
        self.create_publish_job = create_publish_job
        self.facebook_publish_request_cls = facebook_publish_request_cls
        self.inspect_product_url = inspect_product_url
        self.search_products = search_products
        self._music_recent_keys: list[str] = []
        self._campaign_prepare_running: set[str] = set()
        self._render_running: set[str] = set()
        self.router = APIRouter(prefix="/api/parenting", tags=["Parenting"])
        self._init_db()
        self._register_routes()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _slug(text: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (text or "").strip()).strip("_").lower()
        return s[:48] or f"item_{uuid.uuid4().hex[:8]}"

    def _reference_title(self, character_id: str) -> str:
        return f"{self._slug(character_id)}_master"

    def _reference_filename(self, character_id: str, source_path: str | Path | None = None) -> str:
        ext = '.png'
        if source_path:
            try:
                src_ext = Path(str(source_path)).suffix.strip()
                if src_ext and len(src_ext) <= 8:
                    ext = src_ext.lower()
            except Exception:
                pass
        return f"{self._reference_title(character_id)}{ext}"

    def _reference_store_dir(self, character_id: str) -> Path:
        d = self.output_dir / '_character_refs' / self._slug(character_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _reference_store_path(self, character_id: str, source_path: str | Path | None = None) -> Path:
        return self._reference_store_dir(character_id) / self._reference_filename(character_id, source_path)

    def _materialize_reference_copy(self, character_id: str, source_path: str | None) -> tuple[str | None, str | None]:
        src = Path(str(source_path or '')).expanduser()
        if not str(src) or not src.exists() or not src.is_file():
            return None, None
        dst = self._reference_store_path(character_id, src)
        try:
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
        except Exception:
            try:
                shutil.copy2(src, dst)
            except Exception:
                return str(src.resolve()), src.name
        return str(dst.resolve()), dst.name

    @staticmethod
    def _loads(value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS parenting_characters(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    age_label TEXT,
                    visual_prompt TEXT NOT NULL,
                    voice_prompt TEXT NOT NULL,
                    reference_path TEXT,
                    generated_job_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parenting_characters_role ON parenting_characters(role,enabled,name);

                CREATE TABLE IF NOT EXISTS parenting_character_sets(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    mother_character_id TEXT NOT NULL,
                    child_character_id TEXT NOT NULL,
                    father_character_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS parenting_story_runs(
                    id TEXT PRIMARY KEY,
                    flow_job_id TEXT,
                    character_set_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    title TEXT,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    final_path TEXT,
                    burn_subtitles INTEGER NOT NULL DEFAULT 1,
                    auto_publish INTEGER NOT NULL DEFAULT 0,
                    facebook_page_id TEXT,
                    facebook_dry_run INTEGER NOT NULL DEFAULT 1,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parenting_runs_flow ON parenting_story_runs(flow_job_id);
                CREATE INDEX IF NOT EXISTS idx_parenting_runs_status ON parenting_story_runs(status,created_at);

                CREATE TABLE IF NOT EXISTS parenting_products(
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL DEFAULT 'shopee',
                    source_url TEXT NOT NULL,
                    final_url TEXT,
                    title TEXT,
                    price TEXT,
                    currency TEXT,
                    shop_name TEXT,
                    description TEXT,
                    features_json TEXT NOT NULL DEFAULT '[]',
                    image_urls_json TEXT NOT NULL DEFAULT '[]',
                    local_image_path TEXT,
                    capture_json TEXT NOT NULL DEFAULT '{}',
                    source_method TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_parenting_products_url ON parenting_products(source_url);

                CREATE TABLE IF NOT EXISTS parenting_auto_pages(
                    id TEXT PRIMARY KEY,
                    facebook_page_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    posts_per_day INTEGER NOT NULL DEFAULT 3,
                    start_hour INTEGER NOT NULL DEFAULT 8,
                    end_hour INTEGER NOT NULL DEFAULT 22,
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_parenting_auto_pages_fb ON parenting_auto_pages(facebook_page_id);

                CREATE TABLE IF NOT EXISTS parenting_auto_campaigns(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    character_set_id TEXT NOT NULL,
                    source_links_json TEXT NOT NULL DEFAULT '[]',
                    page_profile_id TEXT NOT NULL,
                    script_model TEXT,
                    image_model TEXT NOT NULL,
                    video_model TEXT NOT NULL,
                    output_duration TEXT NOT NULL DEFAULT '32s',
                    candidate_pool_size INTEGER NOT NULL DEFAULT 5,
                    selected_per_batch INTEGER NOT NULL DEFAULT 10,
                    burn_subtitles INTEGER NOT NULL DEFAULT 1,
                    music_config_json TEXT NOT NULL DEFAULT '{}',
                    shopee_config_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'paused',
                    auto_resume INTEGER NOT NULL DEFAULT 1,
                    current_batch_id TEXT,
                    batch_no INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parenting_auto_campaigns_status ON parenting_auto_campaigns(status,updated_at);

                CREATE TABLE IF NOT EXISTS parenting_auto_batches(
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    batch_no INTEGER NOT NULL,
                    source_links_json TEXT NOT NULL,
                    product_ids_json TEXT NOT NULL DEFAULT '[]',
                    candidates_json TEXT NOT NULL DEFAULT '[]',
                    selected_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    ai_model TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parenting_auto_batches_campaign ON parenting_auto_batches(campaign_id,batch_no);

                CREATE TABLE IF NOT EXISTS parenting_auto_items(
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    rank_no INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    candidate_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    run_id TEXT,
                    flow_job_id TEXT,
                    final_path TEXT,
                    status TEXT NOT NULL DEFAULT 'selected',
                    publish_job_id TEXT,
                    scheduled_at TEXT,
                    published_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parenting_auto_items_batch ON parenting_auto_items(batch_id,rank_no);
                CREATE INDEX IF NOT EXISTS idx_parenting_auto_items_status ON parenting_auto_items(campaign_id,status,updated_at);

                CREATE TABLE IF NOT EXISTS parenting_music_usage(
                    source_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    title TEXT,
                    local_path TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    used_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parenting_music_usage_provider ON parenting_music_usage(provider,used_count,last_used_at);
                CREATE TABLE IF NOT EXISTS parenting_auto_logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    batch_id TEXT,
                    level TEXT NOT NULL DEFAULT 'info',
                    phase TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parenting_auto_logs_campaign ON parenting_auto_logs(campaign_id,id);
                """
            )
            run_cols={str(r[1]) for r in c.execute("PRAGMA table_info(parenting_story_runs)").fetchall()}
            if "campaign_id" not in run_cols:
                c.execute("ALTER TABLE parenting_story_runs ADD COLUMN campaign_id TEXT")
            if "campaign_batch_id" not in run_cols:
                c.execute("ALTER TABLE parenting_story_runs ADD COLUMN campaign_batch_id TEXT")
            if "campaign_item_id" not in run_cols:
                c.execute("ALTER TABLE parenting_story_runs ADD COLUMN campaign_item_id TEXT")
            campaign_cols={str(r[1]) for r in c.execute("PRAGMA table_info(parenting_auto_campaigns)").fetchall()}
            if "music_config_json" not in campaign_cols:
                c.execute("ALTER TABLE parenting_auto_campaigns ADD COLUMN music_config_json TEXT NOT NULL DEFAULT '{}' ")
            if "auto_resume" not in campaign_cols:
                c.execute("ALTER TABLE parenting_auto_campaigns ADD COLUMN auto_resume INTEGER NOT NULL DEFAULT 1")
            if "shopee_config_json" not in campaign_cols:
                c.execute("ALTER TABLE parenting_auto_campaigns ADD COLUMN shopee_config_json TEXT NOT NULL DEFAULT '{}' ")
            item_cols={str(r[1]) for r in c.execute("PRAGMA table_info(parenting_auto_items)").fetchall()}
            if "resume_retry_count" not in item_cols:
                c.execute("ALTER TABLE parenting_auto_items ADD COLUMN resume_retry_count INTEGER NOT NULL DEFAULT 0")
            if "render_retry_count" not in item_cols:
                c.execute("ALTER TABLE parenting_auto_items ADD COLUMN render_retry_count INTEGER NOT NULL DEFAULT 0")
            if "publish_retry_count" not in item_cols:
                c.execute("ALTER TABLE parenting_auto_items ADD COLUMN publish_retry_count INTEGER NOT NULL DEFAULT 0")
            if "next_retry_at" not in item_cols:
                c.execute("ALTER TABLE parenting_auto_items ADD COLUMN next_retry_at TEXT")
            if "last_failure_class" not in item_cols:
                c.execute("ALTER TABLE parenting_auto_items ADD COLUMN last_failure_class TEXT")
            if "checkpoint_recovery_count" not in item_cols:
                c.execute("ALTER TABLE parenting_auto_items ADD COLUMN checkpoint_recovery_count INTEGER NOT NULL DEFAULT 0")
            batch_cols={str(r[1]) for r in c.execute("PRAGMA table_info(parenting_auto_batches)").fetchall()}
            for col, ddl in {
                "phase":"TEXT NOT NULL DEFAULT 'queued'",
                "phase_message":"TEXT",
                "progress_current":"INTEGER NOT NULL DEFAULT 0",
                "progress_total":"INTEGER NOT NULL DEFAULT 0",
                "progress_updated_at":"TEXT",
            }.items():
                if col not in batch_cols:
                    c.execute(f"ALTER TABLE parenting_auto_batches ADD COLUMN {col} {ddl}")

            cols={str(r[1]) for r in c.execute("PRAGMA table_info(parenting_characters)").fetchall()}
            if "reference_media_id" not in cols:
                c.execute("ALTER TABLE parenting_characters ADD COLUMN reference_media_id TEXT")
            if "reference_title" not in cols:
                c.execute("ALTER TABLE parenting_characters ADD COLUMN reference_title TEXT")
            if "reference_file_name" not in cols:
                c.execute("ALTER TABLE parenting_characters ADD COLUMN reference_file_name TEXT")
            # Seed two starter characters, but without pretending an image already exists.
            count = int(c.execute("SELECT COUNT(*) FROM parenting_characters").fetchone()[0])
            if count == 0:
                now = self._now()
                c.execute(
                    "INSERT INTO parenting_characters(id,name,role,age_label,visual_prompt,voice_prompt,reference_path,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "mother_01", "MOTHER_01", "mother", "30",
                        "Young Asian mother around 30 years old, oval gentle face, warm brown eyes, long dark brown slightly wavy hair, cream blouse, sage green pants or skirt, patient and reassuring expression, premium 3D family animation film style, warm cinematic lighting.",
                        "Vietnamese adult female voice, warm, gentle, calm, patient, natural conversational pacing.",
                        None, 1, now, now,
                    ),
                )
                c.execute(
                    "INSERT INTO parenting_characters(id,name,role,age_label,visual_prompt,voice_prompt,reference_path,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "girl_01", "GIRL_01", "child", "4",
                        "4-year-old Asian girl, round cute face, large brown eyes, short dark bob hair, pink pastel pajamas, holding a beige teddy bear, shy and emotional but lovable, premium 3D family animation film style, warm cinematic lighting.",
                        "Vietnamese little girl voice, cute, soft, slightly higher pitch, emotional but clear, natural childlike pacing.",
                        None, 1, now, now,
                    ),
                )
                c.execute(
                    "INSERT INTO parenting_character_sets(id,name,mother_character_id,child_character_id,father_character_id,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    ("mother_girl_01", "Mother 01 + Girl 01", "mother_01", "girl_01", None, 1, now, now),
                )

    def _recover_reference_from_jobs(self, character_id: str, current: dict[str, Any] | None = None) -> dict[str, Any]:
        current = dict(current or {})
        current.setdefault('reference_title', self._reference_title(character_id))
        current.setdefault('reference_file_name', self._reference_filename(character_id, current.get('reference_path')))

        path = str(current.get('reference_path') or '')
        media = str(current.get('reference_media_id') or '')
        file_name = str(current.get('reference_file_name') or '')

        if path and Path(path).exists():
            stable_path, stable_name = self._materialize_reference_copy(character_id, path)
            if stable_path:
                current['reference_path'] = stable_path
                current['reference_file_name'] = stable_name or file_name or self._reference_filename(character_id, stable_path)
        elif file_name:
            candidate = self._reference_store_dir(character_id) / file_name
            if candidate.exists():
                current['reference_path'] = str(candidate.resolve())

        if current.get('reference_path') and Path(str(current.get('reference_path'))).exists() and media and current.get('reference_file_name'):
            return current

        rows = []
        try:
            with self._conn() as c:
                gid = str(current.get('generated_job_id') or '')
                if gid:
                    rows.extend(c.execute(
                        "SELECT a.*,f.scenes_json FROM assets a JOIN flow_jobs f ON f.id=a.job_id WHERE a.job_id=? AND a.kind='image' ORDER BY a.created_at DESC",
                        (gid,),
                    ).fetchall())
                rows.extend(c.execute(
                    "SELECT a.*,f.scenes_json FROM assets a JOIN flow_jobs f ON f.id=a.job_id WHERE f.kind='parenting_character_master' AND a.kind='image' ORDER BY a.created_at DESC LIMIT 200"
                ).fetchall())
        except sqlite3.OperationalError:
            return current

        seen = set()
        for r in rows:
            d = dict(r)
            key = (d.get('job_id'), d.get('id'))
            if key in seen:
                continue
            seen.add(key)
            scenes = self._loads(d.get('scenes_json'), [])
            cid = ''
            if scenes and isinstance(scenes, list):
                cid = str(((scenes[0].get('metadata') or {}).get('characterId')) or '')
            if cid and cid != character_id:
                continue
            lp = str(d.get('local_path') or '')
            mid = str(d.get('media_id') or '')
            if not lp and not mid:
                continue
            stable_path = None
            stable_name = None
            if lp and Path(lp).exists():
                stable_path, stable_name = self._materialize_reference_copy(character_id, lp)
            current['reference_path'] = stable_path or current.get('reference_path') or lp
            current['reference_media_id'] = mid or current.get('reference_media_id')
            current['reference_title'] = current.get('reference_title') or self._reference_title(character_id)
            current['reference_file_name'] = stable_name or current.get('reference_file_name') or self._reference_filename(character_id, lp)
            current['generated_job_id'] = str(d.get('job_id') or current.get('generated_job_id') or '')
            with self._conn() as c:
                c.execute(
                    "UPDATE parenting_characters SET reference_path=?,reference_media_id=?,reference_title=?,reference_file_name=?,generated_job_id=?,updated_at=? WHERE id=?",
                    (current.get('reference_path'), current.get('reference_media_id'), current.get('reference_title'), current.get('reference_file_name'), current.get('generated_job_id'), self._now(), character_id),
                )
            break
        return current

    def _character_from_row(self, r: sqlite3.Row | None) -> dict[str, Any] | None:
        if not r:
            return None
        d = dict(r)
        d["enabled"] = bool(d.get("enabled"))
        d = self._recover_reference_from_jobs(str(d.get('id') or ''), d)
        p = str(d.get("reference_path") or "")
        mid = str(d.get("reference_media_id") or "")
        file_name = str(d.get('reference_file_name') or '')
        if not file_name:
            file_name = Path(p).name if p else self._reference_filename(str(d.get('id') or 'character'))
            d['reference_file_name'] = file_name
        if not d.get('reference_title'):
            d['reference_title'] = self._reference_title(str(d.get('id') or 'character'))
        d["reference_local_ready"] = bool(p and Path(p).exists())
        d["reference_flow_ready"] = bool(mid)
        d["reference_ready"] = bool(d["reference_local_ready"] or d["reference_flow_ready"])
        d["reference_name"] = file_name if file_name else (d.get('reference_title') or mid[:12])
        return d

    def get_character(self, character_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM parenting_characters WHERE id=?", (character_id,)).fetchone()
        return self._character_from_row(r)

    def list_characters(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM parenting_characters ORDER BY role,name").fetchall()
        return [self._character_from_row(r) for r in rows]

    def save_character(self, req: CharacterSave) -> dict[str, Any]:
        cid = self._slug(req.id or req.name)
        now = self._now()
        ref = (req.reference_path or "").strip() or None
        if ref and not Path(ref).exists():
            raise HTTPException(400, f"Không thấy reference_path: {ref}")
        with self._conn() as c:
            old = c.execute("SELECT created_at,generated_job_id,reference_media_id,reference_title,reference_file_name FROM parenting_characters WHERE id=?", (cid,)).fetchone()
            created = str(old["created_at"]) if old else now
            generated_job_id = str(old["generated_job_id"] or "") if old else None
            reference_media_id = str(old["reference_media_id"] or "") if old else None
            reference_title = str(old["reference_title"] or self._reference_title(cid)) if old else self._reference_title(cid)
            reference_file_name = str(old["reference_file_name"] or self._reference_filename(cid, ref)) if old else self._reference_filename(cid, ref)
            stable_ref = ref
            if ref:
                stable_ref, stable_name = self._materialize_reference_copy(cid, ref)
                if stable_name:
                    reference_file_name = stable_name
            c.execute(
                "INSERT OR REPLACE INTO parenting_characters(id,name,role,age_label,visual_prompt,voice_prompt,reference_path,generated_job_id,reference_media_id,reference_title,reference_file_name,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, req.name.strip(), req.role.strip().lower(), req.age_label.strip(), req.visual_prompt.strip(), req.voice_prompt.strip(), stable_ref, generated_job_id, reference_media_id, reference_title, reference_file_name, int(req.enabled), created, now),
            )
        return self.get_character(cid) or {}

    def _set_from_row(self, r: sqlite3.Row | None) -> dict[str, Any] | None:
        if not r:
            return None
        d = dict(r)
        d["enabled"] = bool(d.get("enabled"))
        d["mother"] = self.get_character(d.get("mother_character_id"))
        d["child"] = self.get_character(d.get("child_character_id"))
        d["father"] = self.get_character(d.get("father_character_id")) if d.get("father_character_id") else None
        d["ready"] = bool(d.get("mother") and d["mother"].get("reference_ready") and d.get("child") and d["child"].get("reference_ready"))
        return d

    def get_set(self, set_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM parenting_character_sets WHERE id=?", (set_id,)).fetchone()
        return self._set_from_row(r)

    def list_sets(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM parenting_character_sets ORDER BY enabled DESC,name").fetchall()
        return [self._set_from_row(r) for r in rows]

    def save_set(self, req: CharacterSetSave) -> dict[str, Any]:
        sid = self._slug(req.id or req.name)
        if not self.get_character(req.mother_character_id):
            raise HTTPException(400, "Không tìm thấy character mẹ")
        if not self.get_character(req.child_character_id):
            raise HTTPException(400, "Không tìm thấy character bé")
        if req.father_character_id and not self.get_character(req.father_character_id):
            raise HTTPException(400, "Không tìm thấy character bố")
        now = self._now()
        with self._conn() as c:
            old = c.execute("SELECT created_at FROM parenting_character_sets WHERE id=?", (sid,)).fetchone()
            created = str(old["created_at"]) if old else now
            c.execute(
                "INSERT OR REPLACE INTO parenting_character_sets(id,name,mother_character_id,child_character_id,father_character_id,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (sid, req.name.strip(), req.mother_character_id, req.child_character_id, req.father_character_id or None, int(req.enabled), created, now),
            )
        return self.get_set(sid) or {}

    @staticmethod
    def _validate_shopee_url(raw: str) -> str:
        value = str(raw or "").strip()
        try:
            u = urlparse(value)
        except Exception:
            raise HTTPException(400, "Link Shopee không hợp lệ")
        host = str(u.hostname or "").lower()
        allowed = host == "shopee.vn" or host.endswith(".shopee.vn") or host == "shope.ee" or host.endswith(".shope.ee")
        if u.scheme != "https" or not allowed:
            raise HTTPException(400, "Chỉ hỗ trợ link HTTPS của shopee.vn / shope.ee")
        return value

    @staticmethod
    def _shopee_identity_from_url(raw: str) -> tuple[str, str] | None:
        try:
            u=urlparse(str(raw or ""))
            text=(u.path or "") + "?" + (u.query or "")
            m=re.search(r"-i\.(\d+)\.(\d+)(?:\D|$)", text, re.I)
            if m:
                return m.group(1), m.group(2)
            m=re.search(r"/product/(\d+)/(\d+)(?:\D|$)", text, re.I)
            if m:
                return m.group(1), m.group(2)
            qs=parse_qs(u.query or "")
            shop=(qs.get("shopid") or qs.get("shopId") or [""])[0]
            item=(qs.get("itemid") or qs.get("itemId") or [""])[0]
            return (shop,item) if shop.isdigit() and item.isdigit() else None
        except Exception:
            return None

    @staticmethod
    def _product_id_for_url(url: str) -> str:
        return "shopee_" + hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:14]

    @staticmethod
    def _safe_aff_sub(value: str) -> str:
        text=re.sub(r"[^A-Za-z0-9_.]", "_", str(value or "").strip())
        return text[:40]

    @classmethod
    def _make_shopee_affiliate_link(cls, origin_url: str, affiliate_id: str, sub_ids: list[str] | None=None) -> str:
        origin=cls._validate_shopee_url(origin_url)
        aid=re.sub(r"[^A-Za-z0-9_-]", "", str(affiliate_id or "").strip())[:80]
        if not aid:
            return origin
        link="https://s.shopee.vn/an_redir?origin_link="+quote(origin,safe="")+"&affiliate_id="+quote(aid,safe="")
        parts=[cls._safe_aff_sub(x) for x in (sub_ids or []) if cls._safe_aff_sub(x)][:5]
        if parts:
            link += "&sub_id=" + quote("-".join(parts), safe="")
        return link

    def _campaign_product_search_topics(self, pillar: str, count: int, campaign_id: str, batch_no: int) -> list[str]:
        mother_child=[
            "lều chơi cho bé", "đèn ngủ cho bé", "gấu bông cho bé", "thảm chơi trẻ em", "kệ sách cho bé",
            "bàn ghế trẻ em", "hộp đựng đồ chơi", "bình nước trẻ em", "bộ bát ăn dặm", "ghế ăn cho bé",
            "sách tranh cho bé", "bảng vẽ trẻ em", "đồ chơi xếp hình", "bộ đồ ngủ trẻ em", "đèn đọc sách cho bé",
            "ba lô trẻ em", "hộp cơm trẻ em", "đồng hồ báo thức trẻ em", "rèm phòng trẻ em", "tủ quần áo trẻ em"
        ]
        mother_teaches=[
            "bảng thói quen cho bé", "đồng hồ hẹn giờ trẻ em", "hộp phân loại đồ chơi", "kệ sách montessori", "bộ học chữ cho bé",
            "bộ học số cho bé", "thẻ cảm xúc cho bé", "sách kỹ năng sống cho bé", "bộ dụng cụ đánh răng trẻ em", "bậc kê rửa tay cho bé",
            "bình nước có vạch cho bé", "hộp tiết kiệm trẻ em", "bảng thưởng bé ngoan", "bộ dụng cụ làm việc nhà cho bé", "bộ học buộc dây giày",
            "lịch sinh hoạt cho bé", "đèn ngủ hẹn giờ", "sách truyện cảm xúc", "bộ đồ chơi nấu ăn trẻ em", "bộ xếp hình logic"
        ]
        base=(mother_child+mother_teaches) if pillar=='mixed' else (mother_teaches if pillar=='mother_teaches' else mother_child)
        rng=random.Random(f"search:{campaign_id}:{batch_no}:{pillar}")
        vals=list(dict.fromkeys(base)); rng.shuffle(vals)
        return vals[:max(0,min(int(count or 0),10))]

    def _product_dir(self, product_id: str) -> Path:
        d = self.output_dir / "_products" / self._slug(product_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _download_product_image(self, product_id: str, urls: list[str]) -> str | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
            "Referer": "https://shopee.vn/",
        }
        # Filter and prioritize high resolution product photos over thumbnails and badges
        clean_urls: list[str] = []
        for raw in urls:
            u = str(raw or "").strip()
            if not u.startswith("https://") or ".svg" in u.lower() or "shopeemobile.com" in u.lower():
                continue
            # Remove thumbnail suffix to get full-res original
            u_clean = re.sub(r"_tn$", "", u)
            if u_clean not in clean_urls:
                clean_urls.append(u_clean)
                
        # Sort so that real product images (down-vn.img.susercontent.com) come first
        clean_urls.sort(key=lambda x: (0 if "down-vn.img.susercontent.com/file/vn-" in x else (1 if "down-vn" in x else 2)))
        
        for url in clean_urls[:10]:
            try:
                r = requests.get(url, headers=headers, timeout=20, stream=True, allow_redirects=True)
                if r.status_code != 200:
                    continue
                ctype = str(r.headers.get("content-type") or "").lower()
                if "image" not in ctype:
                    continue
                ext = ".jpg"
                if "png" in ctype:
                    ext = ".png"
                elif "webp" in ctype:
                    ext = ".webp"
                dst = self._product_dir(product_id) / f"product_main{ext}"
                total = 0
                with dst.open("wb") as f:
                    for chunk in r.iter_content(65536):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > 15 * 1024 * 1024:
                            raise RuntimeError("Ảnh sản phẩm quá lớn")
                        f.write(chunk)
                if total > 5000:
                    return str(dst.resolve())
            except Exception:
                continue
        return None

    @staticmethod
    def _looks_like_marketplace_title(value: str) -> bool:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return True
        low = text.lower()
        hard_junk = (
            "shopee việt nam | hot deals",
            "shopee vietnam | hot deals",
            "hot deals, best prices",
            "hot deals best prices",
            "mua sắm online sản phẩm",
            "mua sắm online trên shopee",
        )
        if any(x in low for x in hard_junk):
            return True
        if low in {"shopee", "shopee việt nam", "shopee vietnam", "shopee.vn"}:
            return True
        # A title containing the marketplace brand plus generic commerce words is a page title,
        # not a product name. Real product names may contain the word Shopee only very rarely;
        # in that case we prefer omitting the title rather than speaking marketplace garbage.
        if "shopee" in low and any(x in low for x in ("hot deal", "best price", "mua sắm", "online", "việt nam", "vietnam")):
            return True
        return False

    @classmethod
    def _clean_product_title(cls, value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" |-–—\t\r\n")
        if not text or cls._looks_like_marketplace_title(text):
            return ""
        return text[:500]

    @classmethod
    def _best_product_title_from_capture(cls, capture: dict[str, Any]) -> str:
        candidates: list[str] = []
        for key in ("productTitle", "productName", "title"):
            if capture.get(key):
                candidates.append(str(capture.get(key)))
        raw_candidates = capture.get("titleCandidates")
        if isinstance(raw_candidates, list):
            candidates.extend(str(x) for x in raw_candidates if str(x).strip())
        for candidate in candidates:
            clean = cls._clean_product_title(candidate)
            if clean:
                return clean

        # Last-resort DOM heuristic for v14.5.17 captures: choose a meaningful visible line,
        # but never use a generic Shopee/site header. This is intentionally conservative.
        body = str(capture.get("bodyText") or "")
        nav_junk = (
            "kênh người bán", "trở thành người bán", "tải ứng dụng", "kết nối", "thông báo",
            "đăng ký", "đăng nhập", "giỏ hàng", "shopee đảm bảo", "miễn phí vận chuyển",
            "hot deals", "best prices", "tìm kiếm", "danh mục",
        )
        for line in body.splitlines()[:120]:
            text = re.sub(r"\s+", " ", line).strip()
            low = text.lower()
            if len(text) < 16 or len(text) > 240:
                continue
            if cls._looks_like_marketplace_title(text) or any(x in low for x in nav_junk):
                continue
            if re.fullmatch(r"[\d\s.,₫đ%+\-]+", text, flags=re.I):
                continue
            # Product names usually carry several alphabetic tokens; avoid tiny menu labels.
            if len(re.findall(r"[A-Za-zÀ-ỹ]{2,}", text)) < 4:
                continue
            return text[:500]
        return ""

    def _normalize_product_capture(self, capture: dict[str, Any], model: str = "") -> dict[str, Any]:
        clean_capture_title = self._best_product_title_from_capture(capture)
        desc_blocks = [re.sub(r"\s+", " ", str(x or "")).strip() for x in (capture.get("descriptionBlocks") or []) if str(x or "").strip()]
        detail_text = str(capture.get("detailText") or "").strip()
        raw_description = str(capture.get("description") or "").strip()
        # Merge all useful product-description blocks instead of keeping only the longest one.
        # Shopee often splits material / assembly / included accessories into separate DOM blocks.
        description_parts=[]
        for x in [detail_text, raw_description, *desc_blocks]:
            t=re.sub(r"\s+", " ", str(x or "")).strip()
            if t and t not in description_parts:
                description_parts.append(t)
        description = "\n".join(description_parts)[:10000]
        raw_specs = [re.sub(r"\s+", " ", str(x or "")).strip() for x in (capture.get("specs") or []) if str(x or "").strip()]
        base = {
            "title": clean_capture_title,
            "price": str(capture.get("price") or "").strip()[:120],
            "currency": str(capture.get("currency") or "VND").strip()[:20],
            "shop_name": str(capture.get("shopName") or capture.get("shop_name") or "").strip()[:200],
            "description": description,
            "features": [],
            "specs": raw_specs[:24],
            "suitable_for": [],
            "images": [str(x) for x in (capture.get("images") or []) if str(x).startswith("https://")][:16],
        }
        raw_text = str(capture.get("bodyText") or "")[:32000]
        if not self.router9_enabled():
            # Even without AI, keep useful factual lines from the product detail/spec capture.
            candidates = raw_specs + [x for x in desc_blocks if len(x) <= 500]
            base["features"] = candidates[:12]
            return base
        system = (
            "Bạn là bộ trích xuất dữ liệu sản phẩm từ DOM Shopee. Chỉ dùng dữ liệu THỰC có trong CAPTURE, DESCRIPTION/DETAIL và VISIBLE TEXT; tuyệt đối không tự bịa tính năng, chất liệu, kích thước, giá, công dụng hay chứng nhận. "
            "TITLE phải là tên SẢN PHẨM thật, không phải tên sàn/trang/menu/tên shop. "
            "DESCRIPTION phải là bản mô tả factual cô đọng nhưng đủ chi tiết để một biên kịch hiểu sản phẩm trông thế nào, lắp/dùng ra sao và điểm nổi bật nào đã được xác minh. Không viết quảng cáo. "
            "FEATURES và SPECS phải tách thành từng ý ngắn, không lặp. Nếu không thấy thì để trống. Trả JSON duy nhất."
        )
        capture_small = {k: v for k, v in capture.items() if k not in {"bodyText"}}
        user = (
            "CAPTURE JSON:\n"
            + json.dumps(capture_small, ensure_ascii=False)[:18000]
            + "\n\nVISIBLE TEXT:\n"
            + raw_text[:12000]
            + "\n\nTrả JSON keys: title, price, currency, shop_name, description, features, specs. Không markdown."
        )
        try:
            out = self.router9_chat_json(model=model.strip(), system_prompt=system, user_prompt=user, temperature=0.08)
            if isinstance(out, dict):
                ai_title = self._clean_product_title(str(out.get("title") or ""))
                if ai_title:
                    base["title"] = ai_title
                for k in ("price", "currency", "shop_name", "description"):
                    if out.get(k) is not None:
                        base[k] = str(out.get(k) or "").strip()[:10000 if k == "description" else 500]
                feats = out.get("features") if isinstance(out.get("features"), list) else []
                base["features"] = [re.sub(r"\s+", " ", str(x)).strip()[:350] for x in feats if str(x).strip()][:12]
                specs = out.get("specs") if isinstance(out.get("specs"), list) else []
                base["specs"] = [re.sub(r"\s+", " ", str(x)).strip()[:350] for x in specs if str(x).strip()][:12] or base["specs"][:12]
                suitable = out.get("suitable_for") if isinstance(out.get("suitable_for"), list) else []
                base["suitable_for"] = [str(x).strip()[:220] for x in suitable if str(x).strip()][:6]
        except Exception as exc:
            base["normalize_error"] = str(exc)
        base["title"] = self._clean_product_title(base.get("title") or "")
        return base

    def _save_product_capture(self, source_url: str, capture: dict[str, Any], model: str = "") -> dict[str, Any]:
        product_id = self._product_id_for_url(source_url)
        norm = self._normalize_product_capture(capture, model)
        images = norm.get("images") or [str(x) for x in (capture.get("images") or []) if str(x).startswith("https://")]
        local_image = self._download_product_image(product_id, images)
        now = self._now()
        with self._conn() as c:
            old = c.execute("SELECT created_at,local_image_path FROM parenting_products WHERE id=?", (product_id,)).fetchone()
            created = str(old["created_at"]) if old else now
            if not local_image and old and str(old["local_image_path"] or "") and Path(str(old["local_image_path"])).exists():
                local_image = str(old["local_image_path"])
            sql = (
                "INSERT OR REPLACE INTO parenting_products("
                "id,platform,source_url,final_url,title,price,currency,shop_name,description,features_json,image_urls_json,local_image_path,capture_json,source_method,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            )
            c.execute(
                sql,
                (
                    product_id,
                    "shopee",
                    source_url,
                    str(capture.get("finalUrl") or source_url),
                    norm.get("title") or "",
                    norm.get("price") or "",
                    norm.get("currency") or "VND",
                    norm.get("shop_name") or "",
                    norm.get("description") or "",
                    self._dumps(norm.get("features") or []),
                    self._dumps(images),
                    local_image,
                    self._dumps({**capture, "normalized": norm}),
                    str(capture.get("source") or "extension_browser"),
                    created,
                    now,
                ),
            )
        return self.get_product(product_id) or {}

    def get_product_by_url(self, url: str) -> dict[str, Any] | None:
        raw = str(url or "").strip()
        if not raw:
            return None
        with self._conn() as c:
            r = c.execute(
                "SELECT id FROM parenting_products WHERE source_url=? OR final_url=? ORDER BY updated_at DESC LIMIT 1",
                (raw, raw),
            ).fetchone()
        if r:
            return self.get_product(str(r["id"]))
        ident = self._shopee_identity_from_url(raw)
        if not ident:
            return None
        shop_id, item_id = ident
        needle = f".{shop_id}.{item_id}"
        with self._conn() as c:
            rows = c.execute("SELECT id,source_url,final_url FROM parenting_products ORDER BY updated_at DESC LIMIT 300").fetchall()
        for row in rows:
            for candidate in (str(row["source_url"] or ""), str(row["final_url"] or "")):
                if needle and needle in candidate:
                    return self.get_product(str(row["id"]))
                if self._shopee_identity_from_url(candidate) == ident:
                    return self.get_product(str(row["id"]))
        return None

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM parenting_products WHERE id=?", (product_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["features"] = self._loads(d.pop("features_json"), [])
        d["image_urls"] = self._loads(d.pop("image_urls_json"), [])
        d["capture"] = self._loads(d.pop("capture_json"), {})
        cap = d.get("capture") if isinstance(d.get("capture"), dict) else {}
        normalized = cap.get("normalized") if isinstance(cap.get("normalized"), dict) else {}
        d["specs"] = normalized.get("specs") if isinstance(normalized.get("specs"), list) else (cap.get("specs") if isinstance(cap.get("specs"), list) else [])
        d["suitable_for"] = normalized.get("suitable_for") if isinstance(normalized.get("suitable_for"), list) else []
        d["detail_text"] = str(cap.get("detailText") or "")[:12000]
        d["description_blocks"] = cap.get("descriptionBlocks") if isinstance(cap.get("descriptionBlocks"), list) else []
        # Clean legacy V1.7/V1.8 rows at read time too. Old captures may have persisted
        # the generic Shopee page title; never expose that as a product name/script fact.
        clean_title = self._clean_product_title(str(d.get("title") or ""))
        if not clean_title:
            clean_title = self._best_product_title_from_capture(d.get("capture") or {})
        d["title"] = clean_title
        p = str(d.get("local_image_path") or "")
        d["image_ready"] = bool(p and Path(p).exists())
        d["image_url"] = f"/api/parenting/products/{d['id']}/image" if d["image_ready"] else (d["image_urls"][0] if d["image_urls"] else None)
        return d

    def list_products(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT id FROM parenting_products ORDER BY updated_at DESC LIMIT ?", (min(max(int(limit), 1), 100),)).fetchall()
        return [x for x in (self.get_product(str(r["id"])) for r in rows) if x]

    def _product_ref(self, product: dict[str, Any]) -> dict[str, str]:
        p = str(product.get("local_image_path") or "")
        if not p or not Path(p).exists():
            raise HTTPException(400, "Chưa tải được ảnh sản phẩm Shopee về local; bấm ĐỌC SẢN PHẨM lại.")
        name = f"{self._slug(str(product.get('id') or 'product'))}_main{Path(p).suffix.lower() or '.jpg'}"
        safe_title = self._clean_product_title(str(product.get("title") or "")) or self._slug(str(product.get("id") or "product"))
        return {
            "path": p,
            "name": name,
            "fileName": name,
            "role": "product_reference",
            "mediaId": "",
            "title": self._slug(safe_title)[:48],
            "videoReference": False,
        }

    @staticmethod
    def _contains_marketplace_junk(text: str) -> bool:
        low = re.sub(r"\s+", " ", str(text or "")).lower()
        return any(x in low for x in (
            "shopee việt nam", "shopee vietnam", "hot deals", "best prices", "shopee.vn",
        ))

    @staticmethod
    def _product_noun_from_facts(facts: dict[str, Any]) -> str:
        blob = " ".join(
            [str(facts.get("title") or ""), str(facts.get("description") or "")]
            + [str(x) for x in (facts.get("features") or [])]
        ).lower()
        mapping = [
            (("lều", "tent", "nhà bóng", "nhà chơi"), "chiếc lều"),
            (("đèn ngủ", "night light", "đèn led"), "chiếc đèn"),
            (("bàn học", "study desk", "bàn trẻ em"), "chiếc bàn"),
            (("ghế", "chair"), "chiếc ghế"),
            (("bình nước", "water bottle", "bình giữ nhiệt"), "chiếc bình"),
            (("kệ", "shelf", "tủ đồ chơi"), "chiếc kệ"),
            (("gối", "pillow"), "chiếc gối"),
            (("chăn", "blanket"), "chiếc chăn"),
        ]
        for keys, noun in mapping:
            if any(k in blob for k in keys):
                return noun
        return "món đồ này"

    @staticmethod
    def _product_story_kind(facts: dict[str, Any]) -> str:
        blob = " ".join(
            [str(facts.get("title") or ""), str(facts.get("description") or "")]
            + [str(x) for x in (facts.get("features") or [])]
        ).lower()
        if any(x in blob for x in ("lều", "tent", "nhà bóng", "nhà chơi")):
            return "tent"
        if any(x in blob for x in ("đèn ngủ", "night light", "đèn led")):
            return "night_light"
        if any(x in blob for x in ("bàn học", "study desk", "bàn trẻ em")):
            return "desk"
        if any(x in blob for x in ("bình nước", "water bottle", "bình giữ nhiệt")):
            return "bottle"
        if any(x in blob for x in ("kệ", "tủ đồ chơi", "storage", "organizer")):
            return "storage"
        return "generic"

    @staticmethod
    def _product_story_templates() -> dict[str, str]:
        return {
            "direct_demo": "Template 1 DIRECT DEMO: clip 1 product visible, mother and child handle product; clip 2 assemble/use; clip 3 child reacts after real use; clip 4 resolution plus narrator.",
            "problem_solution": "Template 2 PROBLEM SOLUTION: show a concrete child difficulty with product already visible; mother guides use; child succeeds; close with practical review.",
            "unbox_play": "Template 3 UNBOX PLAY: product enters as unboxing/opening moment in clip 1; parts/features are handled physically; child explores; final review.",
            "before_after": "Template 4 BEFORE AFTER: clip 1 shows messy/difficult before with product in frame; middle clips show use; clip 4 shows after result and review.",
            "mini_challenge": "Template 5 MINI CHALLENGE: child tries a small challenge using product; mother coaches; child completes; final parent-friendly review.",
        }

    def _select_product_template(self, req: ProductPlanRequest, facts: dict[str, Any]) -> tuple[str, str]:
        templates = self._product_story_templates()
        raw = str(req.story_template_id or "auto").strip().lower()
        if raw in templates:
            return raw, templates[raw]
        keys = list(templates.keys())
        seed = f"{req.variation_seed}|{facts.get('title') or ''}|{req.output_duration}"
        key = keys[abs(hash(seed)) % len(keys)]
        return key, templates[key]

    @staticmethod
    def _product_output_profile(value: str) -> dict[str, Any]:
        key = str(value or "32s").strip().lower().replace(" ", "")
        aliases = {"15s":"16s", "15-16s":"16s", "30s":"32s", "30-32s":"32s", "31s":"32s"}
        key = aliases.get(key, key)
        profiles = {
            "8s":  {"key":"8s",  "label":"8s",      "veo_clips":1, "target_seconds":8,  "ad_seconds":4, "turn_min":2, "turn_max":3,  "turn_default":2, "story_words_min":10, "story_words_max":18, "ad_words_min":8,  "ad_words_max":12},
            "16s": {"key":"16s", "label":"15–16s",  "veo_clips":2, "target_seconds":16, "ad_seconds":4, "turn_min":4, "turn_max":5,  "turn_default":5, "story_words_min":30, "story_words_max":45, "ad_words_min":11, "ad_words_max":16},
            "24s": {"key":"24s", "label":"24s",     "veo_clips":3, "target_seconds":24, "ad_seconds":4, "turn_min":5, "turn_max":7,  "turn_default":7, "story_words_min":50, "story_words_max":72, "ad_words_min":12, "ad_words_max":18},
            "32s": {"key":"32s", "label":"30–32s",  "veo_clips":4, "target_seconds":32, "ad_seconds":5, "turn_min":6, "turn_max":10, "turn_default":9, "story_words_min":58, "story_words_max":86, "ad_words_min":14, "ad_words_max":20},
        }
        return dict(profiles.get(key) or profiles["32s"])

    @staticmethod
    def _vi_word_count(text: str) -> int:
        return len([x for x in re.split(r"\s+", re.sub(r"[^0-9A-Za-zÀ-ỹĐđ%+./-]+", " ", str(text or "")).strip()) if x])

    @classmethod
    def _dialogue_word_count(cls, dialogue: list[dict[str, str]]) -> int:
        return sum(cls._vi_word_count(str(x.get("text") or "")) for x in dialogue if isinstance(x, dict))

    @staticmethod
    def _product_turn_budget(scene_count: int, wanted: int, reveal_scene: int) -> list[int]:
        """Allocate complete conversational beats. The final Veo still needs a short answer before the 4–6s narrator review."""
        scene_count=max(1,min(4,int(scene_count or 1)))
        wanted=max(scene_count,min(12,int(wanted or scene_count)))
        if scene_count==1:
            return [wanted]
        if scene_count==2:
            # 5-turn default => [3,2], leaving only two short lines before the 4s narrator review.
            budget=[2,2]
            priority=[0,1]
        elif scene_count==3:
            # 7-turn default => [2,3,2]. Final clip keeps two concise closure lines before review.
            budget=[1,2,2]
            priority=[0,1]
        else:
            # 30–32s default 9 turns => [3,3,2,1]. Story is concentrated in the first
            # three Veo clips; the final clip gets one short resolution line, then 4–6s ad.
            budget=[2,2,2,1]
            priority=[0,1,2,0,1,2]
        i=0
        while sum(budget)<wanted and i<100:
            idx=priority[i%len(priority)]
            if budget[idx]<4:
                budget[idx]+=1
            i+=1
        # If requested count is below the base budget, trim non-final clips first; never leave final at one line.
        while sum(budget)>wanted:
            changed=False
            for idx in range(scene_count-2,-1,-1):
                floor=1 if idx!=max(0,min(scene_count-1,int(reveal_scene or 2)-1)) else 2
                if budget[idx]>floor:
                    budget[idx]-=1; changed=True; break
            if not changed: break
        return budget

    @staticmethod
    def _script_quality_flags(dialogue: list[dict[str, str]]) -> list[str]:
        text = " ".join(str(x.get("text") or "") for x in dialogue).lower()
        flags = []
        robotic = (
            "con thử nói cho mẹ nghe", "con muốn có một góc riêng", "dễ dùng hơn",
            "mình cùng xem", "dùng đúng cách", "món đồ này có hợp",
            "chỗ của con vẫn thiếu", "góc của con vẫn thiếu", "có một thứ muốn cho con xem nhé",
        )
        for phrase in robotic:
            if phrase in text:
                flags.append(f"robotic:{phrase}")
        if "shopee" in text or "hot deals" in text or "best prices" in text:
            flags.append("marketplace_junk")
        seen = set()
        for item in dialogue:
            line = re.sub(r"\s+", " ", str(item.get("text") or "").strip().lower())
            if not line:
                continue
            if line in seen:
                flags.append("duplicate_dialogue")
                break
            seen.add(line)
        return flags

    def _product_facts(self, product: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": self._clean_product_title(str(product.get("title") or "")),
            "price": product.get("price") or "",
            "currency": product.get("currency") or "",
            "description": product.get("description") or product.get("detail_text") or "",
            "features": product.get("features") or [],
            "specs": product.get("specs") or [],
            "suitable_for": product.get("suitable_for") or [],
            "shop_name": product.get("shop_name") or "",
        }

    @staticmethod
    def _choose_turn_count(profile: dict[str, Any], requested: int) -> int:
        if int(requested or 0) > 0:
            return max(int(profile["turn_min"]), min(int(profile["turn_max"]), int(requested)))
        return int(profile["turn_default"])

    @staticmethod
    def _phase_for_scene(scene: int, scene_count: int, reveal_scene: int) -> str:
        if scene_count <= 1:
            return "product_reveal"
        if scene < reveal_scene:
            return "setup"
        if scene == reveal_scene:
            return "product_reveal"
        if scene == scene_count:
            return "resolution"
        return "payoff"

    def _ad_text_from_facts(self, noun: str, facts: dict[str, Any], profile: dict[str, Any]) -> tuple[str, list[str]]:
        raw=[str(x).strip() for x in (facts.get("features") or []) if str(x).strip()]
        specs=[str(x).strip() for x in (facts.get("specs") or []) if str(x).strip()]
        all_claims=[]
        for x in raw+specs:
            if x and not self._contains_marketplace_junk(x) and x not in all_claims:
                all_claims.append(x)
        kind=self._product_story_kind(facts)
        blob=" ".join(all_claims+[str(facts.get("description") or "")]).lower()
        used=[]
        if kind=="tent":
            has_oxford="oxford" in blob or "polyester" in blob
            has_frame="khung" in blob and any(k in blob for k in ("thép","abs","chắc"))
            has_easy="dễ lắp" in blob or "lắp ráp" in blob
            has_mesh="rèm" in blob or "chống muỗi" in blob
            has_light="dây đèn" in blob or "đèn trang trí" in blob
            if has_oxford: used.extend([x for x in all_claims if "oxford" in x.lower() or "polyester" in x.lower()][:1])
            if has_frame: used.extend([x for x in all_claims if "khung" in x.lower()][:1])
            if has_easy: used.extend([x for x in all_claims if "lắp" in x.lower()][:1])
            if has_mesh: used.extend([x for x in all_claims if "rèm" in x.lower() or "muỗi" in x.lower()][:1])
            if has_light: used.extend([x for x in all_claims if "đèn" in x.lower()][:1])
            maxw=int(profile["ad_words_max"])
            if maxw<=12:
                if has_oxford and has_easy:
                    text="Lều vải Oxford dễ lắp, tạo góc chơi riêng cho bé."
                elif has_mesh and has_light:
                    text="Lều kèm rèm lưới và dây đèn cho góc chơi của bé."
                else:
                    bits=[]
                    if has_oxford: bits.append("vải Oxford")
                    if has_easy: bits.append("dễ lắp")
                    if has_mesh: bits.append("rèm lưới")
                    if has_light: bits.append("dây đèn")
                    text="Lều " + ", ".join(bits[:2]) + " cho góc chơi riêng của bé."
            elif maxw<=18:
                bits=[]
                if has_oxford: bits.append("vải Oxford")
                if has_frame: bits.append("khung chắc chắn")
                if has_easy: bits.append("dễ lắp")
                if has_mesh: bits.append("rèm lưới")
                if has_light: bits.append("dây đèn trang trí")
                text="Chiếc lều " + ", ".join(bits[:4]) + ", tạo góc chơi riêng cho bé."
            else:
                bits=[]
                if has_oxford: bits.append("vải Oxford")
                if has_frame: bits.append("khung chắc chắn")
                if has_easy: bits.append("dễ lắp")
                if has_mesh: bits.append("rèm lưới")
                if has_light: bits.append("dây đèn trang trí")
                text="Chiếc lều " + ", ".join(bits[:5]) + ", tạo một góc chơi riêng xinh xắn cho bé."
            if not any((has_oxford,has_frame,has_easy,has_mesh,has_light)):
                text="Chiếc lều tạo một góc chơi riêng gọn gàng và xinh xắn cho bé."
        else:
            used=all_claims[:3]
            text=f"{noun.capitalize()} " + (", ".join(used) if used else "gọn gàng, dễ dùng") + ", phù hợp để ba mẹ tham khảo cho bé."
        max_words=int(profile["ad_words_max"]); min_words=int(profile["ad_words_min"])
        words=text.split()
        if len(words)>max_words:
            # Trim at a natural comma/phrase boundary rather than producing fragments such as "dây."
            candidates=[x.strip() for x in text.rstrip(".").split(",") if x.strip()]
            built=""
            for part in candidates:
                cand=(built+(", " if built else "")+part).strip()
                if len(cand.split())>max_words: break
                built=cand
            text=(built or " ".join(words[:max_words])).rstrip(" ,.;")+"."
        if self._vi_word_count(text)<min_words:
            suffix=" cho bé mỗi ngày"
            cand=text.rstrip(".")+suffix+"."
            if self._vi_word_count(cand)<=max_words: text=cand
        claims=[]
        for x in used+all_claims:
            if x not in claims: claims.append(x)
            if len(claims)>=5: break
        return text,claims

    def _fallback_product_single_scene(self, *, product: dict[str, Any], facts: dict[str, Any], req: ProductPlanRequest, phase: str) -> dict[str, Any]:
        title = self._clean_product_title(str(product.get("title") or ""))
        noun = self._product_noun_from_facts(facts)
        kind = self._product_story_kind(facts)
        profile = self._product_output_profile(req.output_duration)
        phase = phase if phase in {"setup", "product_reveal", "payoff", "resolution"} else "product_reveal"
        wanted = self._choose_turn_count(profile, req.total_dialogue_turns)

        if kind == "tent":
            banks = {
                "setup": [
                    {"speaker":"child","text":"Mẹ ơi, tối nay con ngủ với mẹ nhé."},
                    {"speaker":"mother","text":"Con lại muốn ngủ với mẹ à?"},
                    {"speaker":"child","text":"Phòng con trống quá, con không thích ngủ một mình."},
                    {"speaker":"mother","text":"Ừ, mẹ hiểu. Mình sang phòng con xem thử nhé."},
                    {"speaker":"child","text":"Mẹ đi cùng con nha."},
                    {"speaker":"mother","text":"Mẹ đi cùng con."},
                ],
                "product_reveal": [
                    {"speaker":"mother","text":"Con giữ thanh này nhé, mẹ dựng bên kia."},
                    {"speaker":"child","text":"Mấy thanh này ghép thành lều hả mẹ?"},
                    {"speaker":"mother","text":"Ừ, ghép khung xong mình kéo phần vải lên."},
                    {"speaker":"child","text":"Con giữ khung cho mẹ, mẹ kéo đi ạ."},
                    {"speaker":"mother","text":"Sắp xong rồi, con đưa mẹ đầu dây đèn kia nhé."},
                    {"speaker":"child","text":"Dạ, để con treo cùng mẹ!"},
                    {"speaker":"mother","text":"Xong khung rồi. Hai mẹ con mình bước ra xa khoảng hai bước để nhìn toàn bộ chiếc lều nhé."},
                    {"speaker":"child","text":"Con được chui vào thử chưa mẹ?"},
                    {"speaker":"mother","text":"Được, nhưng từ từ để mẹ chỉnh cửa lưới đã."},
                    {"speaker":"child","text":"Dạ, con đợi mẹ."},
                ],
                "payoff": [
                    {"speaker":"child","text":"Wow, đây là nhà nhỏ của con hả mẹ?"},
                    {"speaker":"mother","text":"Ừ, con thử mang gấu bông vào xem."},
                    {"speaker":"child","text":"Con để gấu bên này, còn con ngồi bên kia nhé!"},
                    {"speaker":"mother","text":"Được chứ, con tự sắp góc của mình đi."},
                    {"speaker":"child","text":"Con thích chỗ này hơn phòng trống lúc nãy rồi."},
                    {"speaker":"mother","text":"Thế tối nay con muốn thử ở đây không?"},
                ],
                "resolution": [
                    {"speaker":"mother","text":"Tối nay con còn muốn ngủ với mẹ nữa không?"},
                    {"speaker":"child","text":"Không ạ, tối nay con ngủ ở nhà nhỏ của con!"},
                    {"speaker":"mother","text":"Mẹ ở ngay phòng bên, cần mẹ thì gọi nhé."},
                    {"speaker":"child","text":"Dạ, sáng mai mẹ qua chơi nhà con nha!"},
                ],
            }
            actions = {
                "setup":"Buổi tối trên giường mẹ. Bé đang nằm sát mẹ và xin ngủ cùng; sản phẩm tuyệt đối chưa xuất hiện.",
                "product_reveal":f"Mẹ và bé ở phòng bé, cùng mở và dựng {noun}. Mẹ lắp khung, kéo vải lên; bé giữ thanh khung và hỗ trợ. Nếu đúng với ảnh sản phẩm có dây đèn/rèm lưới thì chỉ thể hiện các chi tiết đó khi chúng có trong reference.",
                "payoff":f"{noun.capitalize()} đã dựng xong theo đúng ảnh tham chiếu. Bé lần đầu nhìn toàn bộ thành quả, bước vào thử và sắp gấu bông bên trong.",
                "resolution":f"{noun.capitalize()} đã hoàn chỉnh trong phòng bé. Bé ngồi/nằm trong góc riêng; mẹ đứng cạnh cửa và hai mẹ con chốt chuyện ngủ riêng.",
            }
        else:
            banks = {
                "setup":[
                    {"speaker":"child","text":"Mẹ ơi, chỗ này con dùng cứ vướng vướng."},
                    {"speaker":"mother","text":"Vướng chỗ nào, con làm thử cho mẹ xem."},
                    {"speaker":"child","text":"Ngay đây ạ, lần nào con cũng phải loay hoay."},
                    {"speaker":"mother","text":"Ừ, mẹ thấy rồi. Mình đổi cách sắp một chút nhé."},
                ],
                "product_reveal":[
                    {"speaker":"mother","text":f"Con giữ giúp mẹ {noun} một chút nhé."},
                    {"speaker":"child","text":"Mình đặt nó ở đây hả mẹ?"},
                    {"speaker":"mother","text":"Ừ, con giữ bên này, mẹ chỉnh bên kia."},
                    {"speaker":"child","text":"Dạ, con giữ rồi ạ."},
                    {"speaker":"mother","text":"Xong rồi, con thử dùng như bình thường xem."},
                ],
                "payoff":[
                    {"speaker":"child","text":"À, giờ con làm dễ hơn rồi mẹ ạ."},
                    {"speaker":"mother","text":"Con thử thêm một lần nữa xem có thuận không."},
                    {"speaker":"child","text":"Dạ, lần này nhanh hơn thật."},
                    {"speaker":"mother","text":"Vậy mình giữ cách sắp này nhé."},
                ],
                "resolution":[
                    {"speaker":"mother","text":"Giờ con tự làm được chưa?"},
                    {"speaker":"child","text":"Dạ được rồi, lần sau con tự làm ạ."},
                    {"speaker":"mother","text":"Cần mẹ thì cứ gọi nhé."},
                    {"speaker":"child","text":"Dạ!"},
                ],
            }
            actions = {
                "setup":"Mẹ và bé ở bối cảnh đời thường, đang gặp một bất tiện cụ thể. Sản phẩm chưa xuất hiện.",
                "product_reveal":f"Mẹ lần đầu đưa {noun} vào đúng vị trí sử dụng; hai mẹ con cùng cầm/đặt/lắp bằng hành động cụ thể theo thông tin sản phẩm đã xác minh.",
                "payoff":f"{noun.capitalize()} đã được đặt/lắp xong. Bé thử dùng thật rồi mới phản ứng.",
                "resolution":f"Mẹ và bé kết thúc tình huống sau khi đã dùng {noun}; không quảng cáo trực tiếp trong lời nhân vật.",
            }

        rows = [dict(x) for x in banks[phase]]
        seed = str(req.variation_seed or "")
        if seed and len(rows) > wanted:
            # Rotate only within the same coherent beat; never mix beats.
            offset = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16) % max(1, len(rows)-wanted+1)
            rows = rows[offset:] + rows[:offset]
        dialogue = rows[:wanted]
        while len(dialogue) < wanted:
            dialogue.append(dict(rows[len(dialogue) % len(rows)]))
        ad_text, claims = self._ad_text_from_facts(noun, facts, profile)
        return {
            "title": title[:100] or noun.capitalize(),
            "product_noun": noun,
            "angle": f"TEST 1 SCENE · {phase}",
            "hook": dialogue[0]["text"] if dialogue else "",
            "scenes":[{"scene":1,"phase":phase,"summary":phase,"action":actions[phase],"dialogue":dialogue,"product_visible":phase != "setup"}],
            "product_reveal_scene": 1 if phase != "setup" else 2,
            "product_ad":{"text":ad_text,"voice_role":"narrator","verified_claims":claims},
            "verified_product_facts":facts,
            "ai_used":False,
            "product_id":product["id"],
            "single_scene_phase":phase,
            "timing":{**profile,"dialogue_turns":wanted,"story_word_count":self._dialogue_word_count(dialogue),"ad_word_count":self._vi_word_count(ad_text)},
        }

    def _generate_product_single_scene(self, *, product: dict[str, Any], facts: dict[str, Any], req: ProductPlanRequest) -> dict[str, Any]:
        phase = str(req.single_scene_phase or "product_reveal").strip().lower()
        if phase not in {"setup", "product_reveal", "payoff", "resolution"}:
            phase = "product_reveal"
        fallback = self._fallback_product_single_scene(product=product, facts=facts, req=req, phase=phase)
        if not self.router9_enabled():
            return fallback
        profile = self._product_output_profile(req.output_duration)
        wanted = self._choose_turn_count(profile, req.total_dialogue_turns)
        noun = self._product_noun_from_facts(facts)
        kind = self._product_story_kind(facts)
        phase_rule = {
            "setup":"Chỉ dựng vấn đề/cảm xúc; tuyệt đối chưa thấy sản phẩm.",
            "product_reveal":"Mẹ và bé đang trực tiếp mở/lắp/dựng/đặt sản phẩm; lời thoại phải bám hành động tay đang làm, chưa khen đẹp.",
            "payoff":"Sản phẩm đã hoàn tất; bé nhìn/thử thật rồi mới phản ứng.",
            "resolution":"Chốt hành vi/cảm xúc sau khi dùng sản phẩm; không quảng cáo trong lời mẹ/bé.",
        }[phase]
        system = (
            "Bạn là biên kịch thoại ngắn tiếng Việt cho Facebook Reels. Viết đúng MỘT beat mẹ-bé, không nén toàn bộ câu chuyện vào một cảnh. "
            "Mỗi câu phải có mục đích: hỏi, trả lời, phản ứng hoặc phối hợp hành động. Cấm văn chatbot, cấm giải thích đạo lý, cấm nói Shopee/tên shop/tên sản phẩm dài. "
            "Dùng mô tả/specs đã xác minh để action cụ thể hơn, nhưng nhân vật không đọc thông số kỹ thuật thành tiếng. Trả JSON duy nhất."
        )
        base_user = (
            "VERIFIED PRODUCT FACTS RICH:\n" + self._dumps(facts) + "\n"
            f"Product kind: {kind}\nProduct noun: {noun}\nPHASE: {phase}\nPHASE RULE: {phase_rule}\n"
            f"OUTPUT PROFILE: {profile['label']} / {profile['veo_clips']} Veo clip(s).\n"
            f"Số lượt thoại bắt buộc: {wanted}. Mục tiêu tổng từ tiếng Việt của đoạn thoại: {profile['story_words_min']}–{profile['story_words_max']} từ.\n"
            f"VARIATION SEED: {req.variation_seed or 'none'}\nSCRIPT TRƯỚC CẦN TRÁNH LẶP: {self._dumps(req.previous_dialogue or [])}\n"
            f"Angle hint: {req.angle_hint or 'tự chọn nhưng phải đúng phase'}\n"
            "Câu phải đời thường và cụ thể theo hành động. Ví dụ tốt: 'Con giữ giúp mẹ thanh này nhé', 'Mấy thanh này ghép thành cái lều hả mẹ?', 'Sao thế, con lại muốn ngủ với mẹ à?'. "
            "Trả keys: action, dialogue. dialogue đúng số lượt, speaker chỉ mother/child, text tiếng Việt."
        )
        errors=[]
        for attempt in range(2):
            try:
                user = base_user + ("\nLẦN TRƯỚC BỊ REJECT: " + ", ".join(errors) + ". Viết lại hoàn toàn." if errors else "")
                out = self.router9_chat_json(model=req.model.strip(), system_prompt=system, user_prompt=user, temperature=0.52 if attempt==0 else 0.32)
                raw = out.get("dialogue") if isinstance(out, dict) else None
                dialogue=[]
                for turn in raw or []:
                    if not isinstance(turn, dict): continue
                    sp=str(turn.get("speaker") or "").strip().lower(); tx=re.sub(r"\s+", " ", str(turn.get("text") or "")).strip()[:300]
                    if sp in {"mother","child"} and tx: dialogue.append({"speaker":sp,"text":tx})
                errors=[]
                if len(dialogue)!=wanted: errors.append(f"turn_count:{len(dialogue)}!={wanted}")
                errors.extend(self._script_quality_flags(dialogue))
                words=self._dialogue_word_count(dialogue)
                if words < max(1,int(profile["story_words_min"]*.75)) or words > int(profile["story_words_max"]*1.15): errors.append(f"word_count:{words}")
                joined=" ".join(x["text"] for x in dialogue).lower()
                if phase in {"setup","product_reveal"} and any(x in joined for x in ("đẹp quá","xinh quá","phòng đẹp","thích quá","wow")): errors.append("premature_praise")
                if kind=="tent" and phase=="setup" and not any(x in joined for x in ("ngủ với mẹ","ngủ cùng mẹ","ngủ một mình","phòng con")): errors.append("tent_setup_not_sleep")
                if phase=="product_reveal" and kind=="tent" and not any(x in joined for x in ("thanh","khung","dựng","lắp","kéo","giữ","mở")): errors.append("reveal_not_physical")
                prev=" ".join(str(x).strip().lower() for x in (req.previous_dialogue or []) if str(x).strip())
                if prev and joined==prev: errors.append("same_as_previous")
                if errors: continue
                action=self._flow_explicit_action(re.sub(r"\s+", " ", str(out.get("action") or fallback["scenes"][0]["action"])).strip())[:1400]
                if self._contains_marketplace_junk(action): errors=["marketplace_junk_action"]; continue
                if kind=="tent" and phase=="product_reveal" and "phòng khách" in action.lower(): errors=["tent_reveal_wrong_room"]; continue
                fallback["scenes"][0]["action"]=action
                fallback["scenes"][0]["dialogue"]=dialogue
                fallback["hook"]=dialogue[0]["text"] if dialogue else ""
                fallback["ai_used"]=True; fallback["ai_model"]=req.model.strip() or "AUTO"; fallback["script_quality"]="PASS"
                fallback["timing"]["story_word_count"]=words
                return fallback
            except Exception as exc:
                errors=[str(exc)]
        fallback["ai_rejected"]=errors
        return fallback

    def _fallback_product_story(self, *, product: dict[str, Any], facts: dict[str, Any], req: ProductPlanRequest, reveal_scene: int, profile: dict[str, Any], wanted: int) -> dict[str, Any]:
        title = self._clean_product_title(str(product.get("title") or ""))
        noun = self._product_noun_from_facts(facts)
        kind = self._product_story_kind(facts)
        scene_count = int(profile["veo_clips"])
        budgets = self._product_turn_budget(scene_count, wanted, reveal_scene)

        if kind == "tent":
            banks = {
                "setup":[
                    {"speaker":"child","text":"Mẹ ơi, tối nay con ngủ với mẹ nhé."},
                    {"speaker":"mother","text":"Con lại muốn ngủ với mẹ à?"},
                    {"speaker":"child","text":"Phòng con trống quá, con không thích ngủ một mình."},
                    {"speaker":"mother","text":"Ừ, sang phòng con với mẹ nhé."},
                ],
                "product_reveal":[
                    {"speaker":"mother","text":"Con giữ thanh này nhé, mẹ dựng bên kia."},
                    {"speaker":"child","text":"Mấy thanh này ghép thành lều hả mẹ?"},
                    {"speaker":"mother","text":"Ừ, ghép khung xong mình kéo phần vải lên."},
                    {"speaker":"child","text":"Con giữ cho mẹ, mẹ kéo đi ạ."},
                ],
                "payoff":[
                    {"speaker":"child","text":"Wow, đây là nhà nhỏ của con hả mẹ?"},
                    {"speaker":"mother","text":"Ừ, con mang gấu bông vào trong lều nhé."},
                    {"speaker":"child","text":"Con để gấu bên này, còn con ngồi bên kia nhé!"},
                ],
                "resolution":[
                    {"speaker":"mother","text":"Tối nay con còn muốn ngủ với mẹ nữa không?"},
                    {"speaker":"child","text":"Không ạ, tối nay con ngủ ở nhà nhỏ của con!"},
                    {"speaker":"mother","text":"Mẹ ở ngay phòng bên, cần mẹ thì gọi nhé."},
                ],
            }
            actions = {
                "setup":"Buổi tối trên giường mẹ. Bé xin ngủ cùng mẹ; sản phẩm hoàn toàn chưa xuất hiện.",
                "product_reveal":f"Mẹ và bé sang phòng bé, cùng mở và dựng {noun}. Mẹ lắp khung/kéo vải, bé giữ thanh và hỗ trợ; bám đúng hình sản phẩm tham chiếu.",
                "payoff":f"{noun.capitalize()} đã dựng xong. Bé lần đầu nhìn toàn bộ thành quả, bước vào thử và sắp gấu bông bên trong.",
                "resolution":f"{noun.capitalize()} đã hoàn chỉnh. Bé ở trong góc riêng, mẹ đứng cạnh cửa; chốt việc bé thử ngủ riêng.",
            }
        else:
            banks = {
                "setup":[
                    {"speaker":"child","text":"Mẹ ơi, chỗ này con dùng cứ vướng vướng."},
                    {"speaker":"mother","text":"Vướng chỗ nào, con làm thử cho mẹ xem."},
                    {"speaker":"child","text":"Ngay đây ạ, lần nào con cũng phải loay hoay."},
                ],
                "product_reveal":[
                    {"speaker":"mother","text":f"Con giữ giúp mẹ {noun} một chút nhé."},
                    {"speaker":"child","text":"Mình đặt nó ở đây hả mẹ?"},
                    {"speaker":"mother","text":"Ừ, con giữ bên này, mẹ chỉnh bên kia."},
                ],
                "payoff":[
                    {"speaker":"child","text":"À, giờ con làm dễ hơn rồi mẹ ạ."},
                    {"speaker":"mother","text":"Con thử lại như bình thường xem."},
                    {"speaker":"child","text":"Dạ, lần này nhanh hơn thật."},
                ],
                "resolution":[
                    {"speaker":"mother","text":"Giờ con tự làm được chưa?"},
                    {"speaker":"child","text":"Dạ được rồi, lần sau con tự làm ạ."},
                ],
            }
            actions = {
                "setup":"Mẹ và bé ở bối cảnh đời thường, đang gặp một bất tiện cụ thể. Sản phẩm chưa xuất hiện.",
                "product_reveal":f"Mẹ lần đầu đưa {noun} vào đúng vị trí dùng; hai mẹ con cùng cầm/đặt/lắp bằng hành động cụ thể.",
                "payoff":f"{noun.capitalize()} đã được đặt/lắp xong. Bé thử dùng thật rồi mới phản ứng.",
                "resolution":f"Mẹ và bé kết thúc tình huống sau khi đã dùng {noun}.",
            }

        scenes=[]
        for i in range(1,scene_count+1):
            phase=self._phase_for_scene(i,scene_count,reveal_scene)
            rows=[dict(x) for x in banks[phase]]
            limit=budgets[i-1]
            if kind=="tent" and scene_count==3 and i==scene_count:
                if limit<=2:
                    rows=[
                        {"speaker":"child","text":"Wow, nhà nhỏ của con đẹp quá! Tối nay con ngủ ở đây."},
                        {"speaker":"mother","text":"Ừ, mẹ sang chúc con ngủ ngon nhé."},
                    ][:limit]
                else:
                    rows=[
                        {"speaker":"child","text":"Wow, đây là nhà nhỏ của con hả mẹ?"},
                        {"speaker":"mother","text":"Tối nay con còn muốn ngủ với mẹ nữa không?"},
                        {"speaker":"child","text":"Không ạ, tối nay con ngủ ở nhà nhỏ của con!"},
                    ][:limit]
            elif len(rows)<limit:
                rows=(rows*((limit//max(1,len(rows)))+1))[:limit]
            else:
                rows=rows[:limit]
            action = actions[phase]
            summary = phase
            if phase == "payoff" and scene_count >= 4:
                if i == 2:
                    action = f"Clip vận hành: mẹ đặt {noun} đúng hướng, bé dùng tay đẩy/thử một thao tác cụ thể; máy quay cận chi tiết sản phẩm đang hoạt động."
                    rows = [
                        {"speaker":"mother","text":"Con thử bước này trước nhé."},
                        {"speaker":"child","text":"Dạ, con đẩy thử bên này nha mẹ."},
                        {"speaker":"mother","text":"Đúng rồi, giữ chậm thôi là được."},
                    ][:limit]
                    summary = "physical_use"
                elif i == 3:
                    action = f"Clip kết quả: {noun} đã hoạt động/đặt xong; bé nhìn thành quả, phản ứng vui vì tự làm được; mẹ chỉ quan sát và khích lệ."
                    rows = [
                        {"speaker":"child","text":"À, giờ con hiểu cách chơi rồi."},
                        {"speaker":"mother","text":"Con làm lại một lần cho mẹ xem nhé."},
                        {"speaker":"child","text":"Dạ, lần này con tự làm được."},
                    ][:limit]
                    summary = "child_payoff"
            scenes.append({"scene":i,"phase":phase,"summary":summary,"action":action,"dialogue":rows,"product_visible":i>=reveal_scene})
        # Special compact output: one clip must reveal immediately and keep dialogue tiny.
        if scene_count==1:
            scenes[0]["action"]=f"Một clip ngắn: mẹ và bé đang trực tiếp mở/dựng {noun}; sản phẩm hiện ngay, bé tò mò và hỗ trợ. Cuối clip chuyển sang product beauty shot để narrator review."
        ad_text, claims=self._ad_text_from_facts(noun,facts,profile)
        all_dialogue=[t for sc in scenes for t in sc["dialogue"]]
        return {
            "title":title[:100] or noun.capitalize(),"product_noun":noun,
            "angle":"Story-first, hành động cụ thể, product reveal đúng nhịp rồi mới payoff",
            "hook":all_dialogue[0]["text"] if all_dialogue else "","scenes":scenes,"product_reveal_scene":reveal_scene,
            "story_template_id":self._select_product_template(req,facts)[0],"story_template_rule":self._select_product_template(req,facts)[1],
            "product_ad":{"text":ad_text,"voice_role":"narrator","verified_claims":claims},"verified_product_facts":facts,
            "ai_used":False,"product_id":product["id"],"turn_budget":budgets,
            "timing":{**profile,"dialogue_turns":wanted,"story_word_count":self._dialogue_word_count(all_dialogue),"ad_word_count":self._vi_word_count(ad_text)},
        }

    def _validate_product_plan_draft(self, *, scenes: list[dict[str, Any]], ad_text: str, facts: dict[str, Any], profile: dict[str, Any], budgets: list[int], reveal_scene: int) -> list[str]:
        errors=[]
        if len(scenes)!=len(budgets): return [f"scene_count:{len(scenes)}!={len(budgets)}"]
        all_dialogue=[]
        seen_actions=set()
        for i,row in enumerate(scenes,1):
            turns=row.get("dialogue") if isinstance(row.get("dialogue"),list) else []
            if len(turns)!=budgets[i-1]: errors.append(f"scene{i}_turns:{len(turns)}!={budgets[i-1]}")
            all_dialogue.extend(turns)
            joined=" ".join(str(x.get("text") or "") for x in turns).lower()
            phase=self._phase_for_scene(i,len(scenes),reveal_scene)
            action=str(row.get("action") or "").lower()
            action_key=re.sub(r"\W+", " ", action).strip()[:180]
            if action_key and action_key in seen_actions:
                errors.append(f"scene{i}_duplicate_action")
            seen_actions.add(action_key)
            if any(x in action for x in ("lùi lại","nhìn một chút","xem thử","một chút nào")): errors.append(f"scene{i}_vague_flow_action")
            if self._product_story_kind(facts)=="tent" and phase=="product_reveal" and "phòng khách" in action: errors.append(f"scene{i}_wrong_room")
            if i<=reveal_scene and any(x in joined for x in ("đẹp quá","xinh quá","phòng đẹp","thích quá","wow")): errors.append(f"scene{i}_premature_praise")
            if phase=="setup" and any(x in joined for x in ("lều","đèn trang trí","khung lều","dựng lều")): errors.append(f"scene{i}_product_leak")
        errors.extend(self._script_quality_flags(all_dialogue))
        story_words=self._dialogue_word_count(all_dialogue)
        if story_words < int(profile["story_words_min"]*.85) or story_words > int(profile["story_words_max"]*1.12): errors.append(f"story_words:{story_words}")
        ad_words=self._vi_word_count(ad_text)
        if ad_words < int(profile["ad_words_min"]*.7) or ad_words > int(profile["ad_words_max"]*1.15): errors.append(f"ad_words:{ad_words}")
        kind=self._product_story_kind(facts)
        if kind=="tent":
            setup=" ".join(str(t.get("text") or "") for r in scenes if r.get("phase")=="setup" for t in (r.get("dialogue") or [])).lower()
            reveal=" ".join(str(t.get("text") or "") for r in scenes if r.get("phase")=="product_reveal" for t in (r.get("dialogue") or [])).lower()
            if len(scenes)>1 and not any(x in setup for x in ("ngủ với mẹ","ngủ cùng mẹ","ngủ một mình","phòng con")): errors.append("tent_setup_not_sleep")
            if not any(x in reveal for x in ("thanh","khung","dựng","lắp","kéo","giữ","mở")): errors.append("tent_reveal_not_physical")
        return errors

    def generate_product_plan(self, req: ProductPlanRequest) -> dict[str, Any]:
        product=self.get_product(req.product_id)
        if not product: raise HTTPException(404,"Không tìm thấy sản phẩm")
        self._require_ready_set(req.character_set_id)
        facts=self._product_facts(product)
        template_id, template_rule = self._select_product_template(req, facts)
        if int(req.story_scene_count or 0)==1 and str(req.single_scene_phase or "").strip():
            return self._generate_product_single_scene(product=product,facts=facts,req=req)

        profile=self._product_output_profile(req.output_duration)
        scene_count=int(profile["veo_clips"])
        wanted=self._choose_turn_count(profile,req.total_dialogue_turns)
        reveal_scene=max(1,min(scene_count,int(req.product_reveal_scene or 1)))
        budgets=self._product_turn_budget(scene_count,wanted,reveal_scene)
        fallback=self._fallback_product_story(product=product,facts=facts,req=req,reveal_scene=reveal_scene,profile=profile,wanted=wanted)
        if not self.router9_enabled(): return fallback

        noun=self._product_noun_from_facts(facts)
        kind=self._product_story_kind(facts)
        system=(
            "Bạn là biên kịch Facebook Reels parenting + social commerce tiếng Việt. Viết MINI-STORY MẸ↔BÉ giống hội thoại thật, không phải quảng cáo trá hình. "
            "Câu thoại phải có nhịp hỏi-đáp-phản ứng-hành động, mỗi câu ngắn-vừa và nói được trong thời lượng. Cấm văn chatbot, cấm câu chung chung, cấm Shopee/tên shop/tên page. "
            "Dùng DESCRIPTION/FEATURES/SPECS đã xác minh để action và prompt hình cụ thể hơn; không cho mẹ/bé đọc thông số kỹ thuật. Product ad chỉ ở cuối, narrator riêng. "
            "Bắt buộc product-visible story-first: clip 1 đã thấy sản phẩm thật trong khung hình; không dùng setup rỗng kiểu 'sản phẩm chưa xuất hiện'. Sau đó đi theo nhịp physically use → payoff → resolution → narrator review. "
            "Action phải mô tả bằng động tác nhìn thấy được: ví dụ 'mẹ và bé bước ra xa chiếc lều khoảng hai bước, đứng cạnh nhau nhìn toàn bộ chiếc lều'; không dùng action mơ hồ như 'lùi lại', 'xem thử', 'một chút'. Trả JSON duy nhất."
        )
        base_user=(
            "VERIFIED PRODUCT FACTS RICH:\n"+self._dumps(facts)+"\n"
            f"PRODUCT KIND: {kind}\nPRODUCT NOUN: {noun}\nSTORY TEMPLATE ID: {template_id}\nSTORY TEMPLATE RULE: {template_rule}\nOUTPUT: {profile['label']} = EXACT {scene_count} Veo clips x 8s.\n"
            f"Đoạn review sản phẩm cuối chiếm khoảng {profile['ad_seconds']}s. Phần story còn lại phải có {wanted} lượt thoại, tổng {profile['story_words_min']}–{profile['story_words_max']} từ tiếng Việt.\n"
            f"AD narrator: {profile['ad_words_min']}–{profile['ad_words_max']} từ, chỉ dùng claim xác minh.\n"
            f"TURN BUDGET từng clip: {budgets}. PRODUCT REVEAL clip: {reveal_scene}.\n"
            f"Angle hint: {req.angle_hint or 'tự chọn tình huống đời thường hợp nhất với sản phẩm'}\n"
            "Nếu là lều trẻ em và output 30–32s, benchmark nhịp tốt: clip 1 bé xin ngủ với mẹ và mẹ hỏi lý do; clip 2 mẹ dẫn sang phòng, hai mẹ con trực tiếp mở/dựng lều; clip 3 lều đã xong, bé mới wow/chui vào; clip 4 mẹ hỏi lại chuyện ngủ cùng, bé tự chọn ngủ riêng; sau đó narrator review 4–6s. "
            "Đừng copy benchmark từng chữ. Hãy dùng chi tiết sản phẩm thật để action có vật lý rõ: khung, vải, rèm, dây đèn... chỉ khi facts có.\n"
            "Mỗi scene trả: scene, phase, summary, action, dialogue. dialogue speaker chỉ mother/child. product_ad={text,verified_claims}."
        )
        reject=[]
        for attempt in range(2):
            try:
                user=base_user+("\nDRAFT TRƯỚC BỊ REJECT: "+", ".join(reject)+". Viết lại toàn bộ, sửa đúng các lỗi trên." if reject else "")
                out=self.router9_chat_json(model=req.model.strip(),system_prompt=system,user_prompt=user,temperature=0.58 if attempt==0 else 0.34)
                raw_scenes=out.get("scenes") if isinstance(out,dict) else None
                if not isinstance(raw_scenes,list): reject=["missing_scenes"]; continue
                norm=[]
                for i in range(1,scene_count+1):
                    r=raw_scenes[i-1] if i-1<len(raw_scenes) and isinstance(raw_scenes[i-1],dict) else {}
                    turns=[]
                    for t in (r.get("dialogue") or []):
                        if not isinstance(t,dict): continue
                        sp=str(t.get("speaker") or "").strip().lower(); tx=re.sub(r"\s+"," ",str(t.get("text") or "")).strip()[:300]
                        if sp in {"mother","child"} and tx and not self._contains_marketplace_junk(tx): turns.append({"speaker":sp,"text":tx})
                    phase=self._phase_for_scene(i,scene_count,reveal_scene)
                    action=self._flow_explicit_action(re.sub(r"\s+"," ",str(r.get("action") or "")).strip())[:1500]
                    summary=re.sub(r"\s+"," ",str(r.get("summary") or phase)).strip()[:400]
                    norm.append({"scene":i,"phase":phase,"summary":summary,"action":action,"dialogue":turns,"product_visible":i>=reveal_scene})
                ad=out.get("product_ad") if isinstance(out.get("product_ad"),dict) else {}
                ad_text=re.sub(r"\s+"," ",str(ad.get("text") or "")).strip()[:700]
                reject=self._validate_product_plan_draft(scenes=norm,ad_text=ad_text,facts=facts,profile=profile,budgets=budgets,reveal_scene=reveal_scene)
                if reject: continue
                claims=[str(x).strip()[:300] for x in (ad.get("verified_claims") or []) if str(x).strip()][:6]
                all_dialogue=[t for sc in norm for t in sc["dialogue"]]
                result={
                    "title":self._clean_product_title(str(out.get("title") or facts.get("title") or ""))[:100] or noun.capitalize(),
                    "product_noun":noun,"angle":str(out.get("angle") or "")[:500],"hook":str(out.get("hook") or (all_dialogue[0]["text"] if all_dialogue else ""))[:500],
                    "scenes":norm,"product_reveal_scene":reveal_scene,"story_template_id":template_id,"story_template_rule":template_rule,"product_ad":{"text":ad_text,"verified_claims":claims,"voice_role":"narrator"},
                    "verified_product_facts":facts,"ai_used":True,"ai_model":req.model.strip() or "AUTO","product_id":product["id"],"turn_budget":budgets,
                    "timing":{**profile,"dialogue_turns":wanted,"story_word_count":self._dialogue_word_count(all_dialogue),"ad_word_count":self._vi_word_count(ad_text)},
                }
                return result
            except Exception as exc:
                reject=[str(exc)]
        fallback["ai_rejected"]=reject
        return fallback

    @staticmethod
    def _flow_explicit_action(text: str) -> str:
        """Rewrite vague Vietnamese blocking into physical actions Veo/Flow can visualize."""
        t=re.sub(r"\s+", " ", str(text or "")).strip()
        replacements=(
            (r"\blùi lại nhìn(?: thử)?(?: một chút)?\b", "bước lùi khoảng hai bước khỏi sản phẩm, đứng cạnh nhau và nhìn toàn bộ sản phẩm"),
            (r"\blùi lại\b", "bước lùi khoảng hai bước, vẫn quay mặt về phía sản phẩm"),
            (r"\bnhìn thử xem\b", "đứng yên và nhìn trực tiếp toàn bộ sản phẩm từ khoảng cách khoảng hai mét"),
            (r"\bnhìn một chút\b", "đứng yên và quan sát toàn bộ sản phẩm trong vài giây"),
            (r"\bxem thử\b", "quan sát trực tiếp bằng mắt và chạm nhẹ vào sản phẩm"),
        )
        for pat,repl in replacements:
            t=re.sub(pat,repl,t,flags=re.I)
        return t

    def _product_prompt_details(self, product: dict[str, Any]) -> str:
        facts=self._product_facts(product)
        rows=[]
        for x in list(facts.get("features") or [])+list(facts.get("specs") or []):
            t=re.sub(r"\s+", " ", str(x or "")).strip()
            if t and not self._contains_marketplace_junk(t) and t not in rows:
                rows.append(t[:260])
            if len(rows)>=8: break
        if not rows:
            desc=re.sub(r"\s+", " ", str(facts.get("description") or "")).strip()
            if desc: rows=[desc[:700]]
        return "; ".join(rows)[:1200]

    def _product_story_image_prompt(self, cset: dict[str, Any], product: dict[str, Any], scene: dict[str, Any], *, include_product: bool) -> str:
        mother = cset["mother"]
        child = cset["child"]
        action = self._flow_explicit_action(str(scene.get("action") or scene.get("summary") or ""))
        if not include_product:
            return (
                "Use uploaded MOTHER and CHILD reference images as identity source of truth. Create one premium 3D animated family-film frame, vertical 9:16. "
                f"Scene: {action}. Mother: {mother['visual_prompt']}. Child: {child['visual_prompt']}. "
                "IMPORTANT: This product-story build keeps the product relevant from scene 1. If include_product is false, still make the problem/action visibly about the same product category without showing a wrong replacement. "
                "Keep mother and child identities exact. Warm cinematic family lighting. Natural blocking and believable room continuity. No text, no subtitles, no watermark."
            )
        verified_details=self._product_prompt_details(product)
        return (
            "Use uploaded MOTHER, CHILD and PRODUCT reference images as identity/design source of truth. Create one premium 3D animated family-film frame, vertical 9:16. "
            f"Scene: {action}. Mother: {mother['visual_prompt']}. Child: {child['visual_prompt']}. "
            f"The product reference represents {self._product_noun_from_facts({'title':product.get('title'),'description':product.get('description'),'features':product.get('features')})}; preserve its exact visible shape, colors, pattern, proportions and distinctive design. "
            + (f"Verified product details from Shopee description/specs for scene accuracy: {verified_details}. " if verified_details else "") +
            "Use those details only when consistent with the uploaded product reference. Do not redesign, replace or invent product features. Product must be physically placed/held in a believable location, never floating. Keep mother and child identities exact. Warm cinematic family lighting. No text, no subtitles, no watermark."
        )

    def _product_video_prompt_for_turns(self, cset: dict[str, Any], product: dict[str, Any], action: str, turns: list[dict[str, str]], *, include_product: bool) -> str:
        action = self._flow_explicit_action(action)
        mother = cset["mother"]
        child = cset["child"]
        parts = []
        for i, turn in enumerate(turns, 1):
            sp = "MOTHER" if turn.get("speaker") == "mother" else "CHILD"
            voice = mother["voice_prompt"] if sp == "MOTHER" else child["voice_prompt"]
            parts.append(f'Turn {i}: {sp} speaks Vietnamese, exact words: "{turn.get("text")}". Voice style: {voice}')
        product_rule = (
            "Keep the PRODUCT visible and visually identical to the generated scene/reference; it stays physically consistent and naturally integrated. "
            if include_product else
            "PRODUCT HAS NOT ENTERED THE STORY YET. Do not reveal, tease, show, hold or imply the later product in this clip. "
        )
        return (
            "Animate the uploaded scene image while preserving exact MOTHER and CHILD identities. "
            f"Action: {action}. " + " ".join(parts) + " Follow turns in exact order; only current speaker moves mouth. "
            + product_rule +
            "Native Vietnamese dialogue clear, conversational and emotionally natural. No subtitles, no text, no watermark."
        )

    def build_product_story(self, req: ProductGenerateRequest, prebuilt_plan: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        cset = self._require_ready_set(req.character_set_id)
        product = self.get_product(req.product_id)
        if not product:
            raise HTTPException(404, "Không tìm thấy sản phẩm")
        base_refs = self._input_refs(cset)
        product_ref = self._product_ref(product)
        plan = prebuilt_plan or self.generate_product_plan(
            ProductPlanRequest(
                product_id=req.product_id, character_set_id=req.character_set_id,
                story_scene_count=req.story_scene_count, total_dialogue_turns=req.total_dialogue_turns,
                output_duration=req.output_duration, angle_hint=req.angle_hint, model=req.model,
                product_reveal_scene=req.product_reveal_scene, single_scene_phase=req.single_scene_phase,
                variation_seed=req.variation_seed, previous_dialogue=req.previous_dialogue,
            )
        )
        story_rows = plan.get("scenes") or []
        story_count = max(1, len(story_rows))
        reveal_scene = 1
        product_name = self._clean_product_title(str(product.get("title") or "")) or self._product_noun_from_facts({"title": product.get("title"), "description": product.get("description"), "features": product.get("features")})
        if story_rows:
            first = story_rows[0]
            first["phase"] = "product_reveal"
            first["summary"] = f"Mở đầu trực tiếp với sản phẩm {product_name}; bé tò mò khi mẹ đặt món đồ chơi lên sàn để cùng khám phá."
            first["action"] = f"Mẹ đặt rõ sản phẩm {product_name} ở giữa khung hình; bé ngồi cạnh quan sát xe khủng long và đường ray, chỉ tay vào chi tiết nổi bật trước khi hai mẹ con bắt đầu chơi."
            first["product_visible"] = True
        ad_text = str((plan.get("product_ad") or {}).get("text") or "").strip()
        scenes=[]
        for i,srow in enumerate(story_rows,1):
            turns=self._dialogue_turns(srow)
            action=str(srow.get("action") or srow.get("summary") or "")
            include_product=True
            srow["product_visible"] = True
            ip=self._product_story_image_prompt(cset,product,srow,include_product=include_product)
            vp=self._product_video_prompt_for_turns(cset,product,action,turns,include_product=include_product)
            is_last=i==story_count
            if is_last and ad_text:
                vp += (
                    " AFTER the mother-child story lines finish, both characters stop speaking and do not lip-sync. "
                    "Use the remaining final 4–6 seconds as a clean product beauty/review moment. "
                    f'OFF-SCREEN Vietnamese adult female advertising narrator says exact words: "{self._safe_dialogue_line(ad_text)}". '
                    f"Narrator voice style: {req.ad_voice_prompt}. Keep product clearly visible and exact to reference. "
                    "Do not make mother or child mouth the narrator line."
                )
            input_refs=base_refs+([product_ref] if include_product else [])
            dialogue_meta=list(turns)
            if is_last and ad_text:
                dialogue_meta.append({"speaker":"narrator","text":ad_text})
            scenes.append({
                "sceneId":i,"imagePrompt":ip,"videoPrompt":vp,"videoSegments":[{"role":"segment_1","prompt":vp}],"inputImages":input_refs,
                "metadata":{
                    "parenting":True,"parentingMode":"product_story","videoInputMode":"scene_image_only","characterSetId":cset["id"],"productId":product["id"],
                    "productTitle":product.get("title"),"productRevealScene":reveal_scene,"productVisible":include_product,
                    "storyPhase":srow.get("phase") or self._phase_for_scene(i,story_count,reveal_scene),
                    "referenceCount":len(input_refs),"summary":srow.get("summary"),"action":action,
                    "dialogueTurns":dialogue_meta,"dialogueChunks":[dialogue_meta],"videoChainFactor":1,
                    "containsAdVoice":bool(is_last and ad_text),"adVoicePrompt":req.ad_voice_prompt if is_last else "",
                }
            })
        # Output length is duration-driven: 8/16/24/32 sec = exactly 1/2/3/4 Veo clips.
        flow=self.default_flow_config(
            imageModel=req.image_model, videoModel=req.video_model,
            imageConcurrency=min(9,max(1,len(scenes))), videoConcurrency=min(4,max(1,len(scenes))),
            aspectRatio="9:16", imageOutputs="x1", videoOutputs="x1",
            videoDuration="8s", videoExtendFactor="x1", autoDownloadVideo=False, maxSubmitsPerMinute=8,
        )
        plan["product"]=product
        plan["story_scene_count"]=len(scenes)
        plan["product_reveal_scene"]=reveal_scene
        plan["ad_scene_id"]=len(scenes)  # narrator is integrated into the final Veo clip
        plan["total_scenes"]=len(scenes)
        plan["video_chain_factor"]=1
        plan["veo_clip_count"]=len(scenes)
        return plan,scenes,flow

    def _save_product_run(self, req: ProductGenerateRequest, plan: dict[str, Any], flow_job_id: str) -> str:
        rid=f"product_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now=self._now(); product=plan.get("product") or {}
        topic=f"Shopee Product · {product.get('title') or req.product_id}"
        with self._conn() as c:
            c.execute(
                "INSERT INTO parenting_story_runs(id,flow_job_id,character_set_id,topic,title,plan_json,status,burn_subtitles,auto_publish,facebook_page_id,facebook_dry_run,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid,flow_job_id,req.character_set_id,topic,str(plan.get("title") or topic),self._dumps(plan),"generating",int(req.burn_subtitles),int(req.auto_publish),req.facebook_page_id or None,int(req.facebook_dry_run),now,now),
            )
        return rid

    def generate_auto_topics(self, req: AutoFbTopicsRequest) -> dict[str, Any]:
        base=req.base_topic.strip()
        fallback=self._clean_topic_lines([
            f"{base}: Khi nào nên bắt đầu cho trẻ ngủ riêng?",
            f"{base}: Trẻ sợ ngủ một mình thì xử lý thế nào?",
            f"{base}: 5 câu nói giúp bé hợp tác hơn trước giờ ngủ",
            f"{base}: Thói quen buổi tối nào giúp bé dễ ngủ hơn?",
            f"{base}: Bé hay đòi ngủ cùng mẹ thì nên phản ứng ra sao?",
            f"{base}: Làm sao trấn an bé mà không tạo phụ thuộc?",
            f"{base}: Vì sao ép ngủ riêng quá nhanh dễ phản tác dụng?",
            f"{base}: Tạo góc ngủ riêng cho bé như thế nào để bé thích?",
            f"{base}: Nên đồng hành bao lâu khi bé mới tập ngủ riêng?",
            f"{base}: Dấu hiệu cho thấy bé đã sẵn sàng tiến thêm một bước",
            f"{base}: Khi bé khóc vì sợ, mẹ nên nói gì trước?",
            f"{base}: Cách giảm bớt lo âu giờ đi ngủ cho trẻ nhỏ",
        ])[:req.count]
        if not self.router9_enabled():
            return {"topics":fallback,"ai_used":False}
        system=(
            "Bạn là chiến lược gia nội dung Facebook cho kênh parenting. "
            "Từ một chủ đề gốc, hãy mở rộng ra nhiều ý tưởng video nhỏ, cụ thể, dễ viral và dễ chuyển thành hội thoại mẹ-bé 25-30 giây. "
            "Trả JSON duy nhất dạng {topics:[...]}. Không markdown."
        )
        user=(
            f"Chủ đề gốc: {base}\n"
            f"Số lượng cần tạo: {req.count}\n"
            f"Đối tượng: {req.audience}\n"
            f"Phong cách page: {req.page_style}\n"
            "Mỗi topic phải riêng biệt, cụ thể, ngắn gọn, có sức hút Facebook và không trùng ý."
        )
        try:
            out=self.router9_chat_json(model=req.model.strip(), system_prompt=system, user_prompt=user, temperature=0.85)
            topics=out.get('topics') if isinstance(out,dict) else None
            if isinstance(topics,list):
                clean=self._clean_topic_lines([str(x) for x in topics])
                if clean:
                    return {"topics":clean[:req.count],"ai_used":True,"ai_model":req.model.strip() or "AUTO"}
        except Exception as exc:
            return {"topics":fallback,"ai_used":False,"ai_error":str(exc)}
        return {"topics":fallback,"ai_used":False}

    def queue_auto_topics(self, req: AutoFbQueueRequest) -> dict[str, Any]:
        self._require_ready_set(req.character_set_id)
        topics=self._clean_topic_lines(req.topics)
        if not topics and req.base_topic.strip():
            topics=self.generate_auto_topics(AutoFbTopicsRequest(base_topic=req.base_topic.strip(), count=10, model=req.model.strip())).get('topics',[])
        if not topics:
            raise HTTPException(400, "Chưa có topic nào để queue")
        created=[]
        for topic in topics:
            story_req=StoryGenerateRequest(
                character_set_id=req.character_set_id,
                topic=topic,
                scene_count=req.scene_count,
                dialogue_turns_per_scene=req.dialogue_turns_per_scene,
                model=req.model,
                image_model=req.image_model,
                video_model=req.video_model,
                video_duration=req.video_duration,
                burn_subtitles=req.burn_subtitles,
                auto_publish=req.auto_publish,
                facebook_page_id=req.facebook_page_id,
                facebook_dry_run=req.facebook_dry_run,
                continuation_mode=req.continuation_mode,
            )
            plan,scenes,flow=self.build_story(story_req)
            jid=self.create_flow_job("parenting_story",scenes,flow)
            rid=self._save_run(story_req,plan,jid)
            created.append({"topic":topic,"run_id":rid,"job_id":jid,"scene_count":len(scenes),"title":plan.get("title") or topic})
        return {"items":created,"count":len(created)}

    @staticmethod
    def _normalize_template_mode(value: str | None) -> str:
        raw = str(value or "parenting").strip().lower()
        return raw if raw in {"parenting", "shopee", "hybrid", "english_context", "mother_teaches_ai"} else "parenting"

    @staticmethod
    def _normalize_dialogue_order_value(value: str | None) -> str:
        raw = str(value or "mother_first").strip().lower()
        return raw if raw in {"mother_first", "child_first", "free"} else "mother_first"

    @staticmethod
    def _allowed_dialogue_speakers() -> set[str]:
        return {"mother", "child", "child_a", "child_b", "friend", "teacher", "adult", "narrator"}

    def _dialogue_order_instruction(self, order: str) -> str:
        order = self._normalize_dialogue_order_value(order)
        if order == "child_first":
            return "Dialogue order: child speaks first, then mother responds naturally."
        if order == "mother_first":
            return "Dialogue order: mother speaks first, then child responds naturally."
        return "Dialogue order: preserve natural authored speaker order."

    def _normalize_dialogue_sequence(self, dialogue: list[dict[str, Any]], order: str, max_turns: int) -> list[dict[str, Any]]:
        clean = []
        seen = set()
        for turn in dialogue:
            speaker = str(turn.get("speaker") or "").strip().lower()
            text = re.sub(r"\s+", " ", str(turn.get("text") or "")).strip()[:320]
            if not speaker or not text:
                continue
            key = (speaker, text.lower())
            if key in seen:
                continue
            seen.add(key)
            row = {"speaker": speaker, "text": text}
            lang = str(turn.get("language") or turn.get("lang") or "").strip().lower()
            if lang in {"vi", "en"}:
                row["language"] = lang
            clean.append(row)
        order = self._normalize_dialogue_order_value(order)
        if order != "free" and clean:
            first = "child" if order == "child_first" else "mother"
            idx = next((i for i, turn in enumerate(clean) if turn.get("speaker") == first), None)
            if idx is not None and idx > 0:
                clean.insert(0, clean.pop(idx))
        return clean[:max(1, min(12, int(max_turns or 4)))]

    def _english_context_fallback(self, topic: str, scene_count: int, turns: int) -> dict[str, Any]:
        lower = topic.lower()
        examples = [
            (["thank", 'c\u1ea3m \u01a1n'], 'Khi \u0111\u01b0\u1ee3c \u0111\u01b0a m\xf3n \u0111\u1ed3 n\xe0y, con n\xf3i c\u1ea3m \u01a1n b\u1eb1ng ti\u1ebfng Anh th\u1ebf n\xe0o?', "Thank you", 'M\u1eb9 \u0111\u01b0a m\u1ed9t c\u1ed1c n\u01b0\u1edbc b\u1eb1ng hai tay cho b\xe9; b\xe9 nh\u1eadn c\u1ed1c n\u01b0\u1edbc r\xf5 r\xe0ng r\u1ed3i nh\xecn m\u1eb9 tr\u01b0\u1edbc khi tr\u1ea3 l\u1eddi.', 'C\u1ed1c n\u01b0\u1edbc ph\u1ea3i \u0111\u01b0\u1ee3c m\u1eb9 trao sang tay b\xe9.'),
            (["please", 'l\xe0m \u01a1n'], 'Khi mu\u1ed1n nh\u1edd m\u1eb9 l\u1ea5y gi\xfap \u0111\u1ed3 v\u1eadt, con n\xf3i l\xe0m \u01a1n b\u1eb1ng ti\u1ebfng Anh th\u1ebf n\xe0o?', "Please", 'M\u1ed9t m\xf3n \u0111\u1ed3 n\u1eb1m tr\xean k\u1ec7 cao; b\xe9 ch\u1ec9 v\xe0o m\xf3n \u0111\u1ed3 v\xe0 nh\u1edd m\u1eb9 l\u1ea5y gi\xfap, m\u1eb9 nh\xecn theo tay b\xe9.', '\u0110\u1ed3 v\u1eadt c\u1ea7n nh\u1edd l\u1ea5y ph\u1ea3i nh\xecn th\u1ea5y r\xf5 v\xe0 n\u1eb1m ngo\xe0i t\u1ea7m v\u1edbi c\u1ee7a b\xe9.'),
            (["sorry", 'xin l\u1ed7i'], 'Khi v\xf4 t\xecnh l\xe0m \u0111\u1ed5 \u0111\u1ed3 c\u1ee7a b\u1ea1n, con n\xf3i xin l\u1ed7i b\u1eb1ng ti\u1ebfng Anh th\u1ebf n\xe0o?', "Sorry", 'B\xe9 v\xf4 t\xecnh l\xe0m \u0111\u1ed5 v\xe0i kh\u1ed1i \u0111\u1ed3 ch\u01a1i c\u1ee7a b\u1ea1n; b\xe9 c\xfai xu\u1ed1ng nh\u1eb7t l\u1ea1i r\u1ed3i nh\xecn b\u1ea1n \u0111\u1ec3 xin l\u1ed7i.', 'Kh\u1ed1i \u0111\u1ed3 ch\u01a1i b\u1ecb \u0111\u1ed5 v\xe0 h\xe0nh \u0111\u1ed9ng nh\u1eb7t l\u1ea1i ph\u1ea3i nh\xecn th\u1ea5y r\xf5.'),
            (["who is that", 'ng\u01b0\u1eddi \u1ea5y', 'ng\u01b0\u1eddi kia'], 'Ng\u01b0\u1eddi \u1ea5y l\xe0 ai, ti\u1ebfng Anh n\xf3i sao con?', "Who is that?", 'M\u1eb9 v\xe0 b\xe9 \u0111\u1ee9ng \u1edf c\xf4ng vi\xean; m\u1eb9 ch\u1ec9 nh\u1eb9 v\u1ec1 ph\xeda m\u1ed9t ng\u01b0\u1eddi quen \u0111ang \u0111\u1ee9ng c\xe1ch v\xe0i m\xe9t, b\xe9 nh\xecn theo \u0111\xfang h\u01b0\u1edbng tr\u01b0\u1edbc khi tr\u1ea3 l\u1eddi.', 'Ph\u1ea3i c\xf3 ng\u01b0\u1eddi th\u1ee9 ba trong h\u1eadu c\u1ea3nh \u0111\u1ec3 m\u1eb9 ch\u1ec9 tay.'),
            (["where", '\u1edf \u0111\xe2u'], 'C\xe1i c\u1eb7p \u1edf \u0111\xe2u, ti\u1ebfng Anh h\u1ecfi sao con?', "Where is the bag?", 'M\u1ed9t chi\u1ebfc c\u1eb7p tr\u1ebb em \u0111\u1eb7t r\xf5 d\u01b0\u1edbi gh\u1ebf; m\u1eb9 v\xe0 b\xe9 c\xf9ng nh\xecn v\u1ec1 ph\xeda chi\u1ebfc c\u1eb7p, m\u1eb9 ch\u1ec9 xu\u1ed1ng v\u1ecb tr\xed \u0111\xf3 khi h\u1ecfi.', '\u0110\u1ed3 v\u1eadt ph\u1ea3i n\u1eb1m \u1edf v\u1ecb tr\xed r\xf5 \u0111\u1ec3 minh h\u1ecda where.'),
        ]
        selected = next((row for row in examples if any(key in lower for key in row[0])), examples[0])
        _, question, answer, action, anchor = selected
        max_turns = max(2, min(12, int(turns or 4)))
        base_dialogue = [
            {"speaker": "mother", "text": question, "language": "vi"},
            {"speaker": "child", "text": answer, "language": "en"},
            {"speaker": "mother", "text": f"Đúng rồi, mình dùng câu {answer} trong tình huống này.", "language": "vi"},
            {"speaker": "child", "text": answer, "language": "en"},
        ]
        scenes = []
        for idx in range(1, max(1, scene_count) + 1):
            scenes.append({
                "scene": idx,
                "summary": 'M\u1eb9 t\u1ea1o ng\u1eef c\u1ea3nh \u0111\u1eddi th\u01b0\u1eddng \u0111\u1ec3 b\xe9 t\u1eadp m\u1ed9t m\u1eabu c\xe2u ti\u1ebfng Anh ng\u1eafn.',
                "action": action,
                "visual_anchor": anchor,
                "lesson_rule": f"Dùng '{answer}' đúng lúc trong tình huống nhìn thấy rõ.",
                "character_mode": "mother_child",
                "dialogue": base_dialogue[:max_turns],
                "dialogue_order": "mother_first",
                "mother": "",
                "child": "",
            })
        return {"title": topic[:100], "hook": question, "lesson": 'D\u1ea1y ti\u1ebfng Anh b\u1eb1ng ng\u1eef c\u1ea3nh tr\u1ef1c quan, m\u1ed7i scene m\u1ed9t m\u1eabu c\xe2u.', "scenes": scenes, "ai_used": False, "template_mode": "english_context"}

    def _life_rules_fallback(self, topic: str, scene_count: int, turns: int) -> dict[str, Any]:
        lower = topic.lower()
        two_children = any(word in lower for word in ['b\u1ea1n', 'r\u1ee7', 'gi\xe0nh', 'n\xf3i d\u1ed1i', 'b\u1eaft n\u1ea1t', 'l\u1ea5y \u0111\u1ed3'])
        if two_children:
            mode = "two_children"
            dialogue = [
                {"speaker": "child_a", "text": 'B\u1ea1n \u01a1i, m\xecnh l\u1ea5y th\u1eed m\xf3n \u0111\u1ed3 \u0111\xf3 \u0111i.'},
                {"speaker": "child_b", "text": 'Kh\xf4ng \u0111\u01b0\u1ee3c \u0111\xe2u, \u0111\u1ed3 \u0111\xf3 kh\xf4ng ph\u1ea3i c\u1ee7a m\xecnh.'},
                {"speaker": "child_a", "text": 'M\xecnh ch\u1ec9 xem m\u1ed9t ch\xfat th\xf4i m\xe0.'},
                {"speaker": "child_b", "text": 'M\xecnh h\u1ecfi ng\u01b0\u1eddi l\u1edbn tr\u01b0\u1edbc th\xec an to\xe0n h\u01a1n.'},
            ]
            action = 'Hai b\u1ea1n nh\u1ecf \u0111\u1ee9ng c\u1ea1nh k\u1ec7 \u0111\u1ed3 ch\u01a1i; m\u1ed9t b\u1ea1n v\u1edbi tay v\u1ec1 m\xf3n \u0111\u1ed3 kh\xf4ng ph\u1ea3i c\u1ee7a m\xecnh, b\u1ea1n kia l\xf9i l\u1ea1i m\u1ed9t b\u01b0\u1edbc v\xe0 ra hi\u1ec7u d\u1eebng.'
            summary = 'M\u1ed9t b\u1ea1n bi\u1ebft t\u1eeb ch\u1ed1i l\u1eddi r\u1ee7 l\xe0m sai v\xe0 ch\u1ecdn h\u1ecfi ng\u01b0\u1eddi l\u1edbn.'
        else:
            mode = "mother_child"
            dialogue = [
                {"speaker": "child", "text": 'M\u1eb9 \u01a1i, con n\xean l\xe0m g\xec trong t\xecnh hu\u1ed1ng n\xe0y?'},
                {"speaker": "mother", "text": 'Con d\u1eebng l\u1ea1i, nh\xecn quanh v\xe0 h\u1ecfi ng\u01b0\u1eddi l\u1edbn tr\u01b0\u1edbc nh\xe9.'},
                {"speaker": "child", "text": 'V\u1eady con kh\xf4ng t\u1ef1 l\xe0m khi ch\u01b0a ch\u1eafc \u1ea1.'},
                {"speaker": "mother", "text": '\u0110\xfang r\u1ed3i, an to\xe0n tr\u01b0\u1edbc r\u1ed3i m\xecnh x\u1eed l\xfd ti\u1ebfp.'},
            ]
            action = 'M\u1eb9 ng\u1ed3i xu\u1ed1ng ngang t\u1ea7m m\u1eaft b\xe9, ch\u1ec9 v\xe0o t\xecnh hu\u1ed1ng tr\u01b0\u1edbc m\u1eb7t v\xe0 h\u01b0\u1edbng d\u1eabn b\xe9 th\u1ef1c h\xe0nh m\u1ed9t b\u01b0\u1edbc an to\xe0n.'
            summary = 'M\u1eb9 h\u01b0\u1edbng d\u1eabn b\xe9 ch\u1ecdn c\xe1ch an to\xe0n trong t\xecnh hu\u1ed1ng \u0111\u1eddi th\u01b0\u1eddng.'
        max_turns = max(2, min(12, int(turns or 4)))
        scenes = []
        for idx in range(1, max(1, scene_count) + 1):
            scenes.append({
                "scene": idx,
                "summary": summary,
                "action": action,
                "visual_anchor": 'Ph\u1ea3i th\u1ea5y r\xf5 m\xf3n \u0111\u1ed3/t\xecnh hu\u1ed1ng g\xe2y v\u1ea5n \u0111\u1ec1 v\xe0 h\xe0nh \u0111\u1ed9ng d\u1eebng l\u1ea1i ho\u1eb7c h\u1ecfi ng\u01b0\u1eddi l\u1edbn.',
                "lesson_rule": 'Kh\xf4ng l\u1ea5y \u0111\u1ed3 ho\u1eb7c l\xe0m vi\u1ec7c ch\u01b0a an to\xe0n khi ch\u01b0a h\u1ecfi ng\u01b0\u1eddi l\u1edbn.',
                "character_mode": mode,
                "dialogue": dialogue[:max_turns],
                "dialogue_order": "free",
                "mother": "",
                "child": "",
            })
        return {"title": topic[:100], "hook": dialogue[0]["text"], "lesson": 'D\u1ea1y tr\u1ebb nh\u1eadn ra t\xecnh hu\u1ed1ng sai, d\u1eebng l\u1ea1i v\xe0 ch\u1ecdn c\xe1ch an to\xe0n.', "scenes": scenes, "ai_used": False, "template_mode": "mother_teaches_ai"}

    def _template_fallback_plan(self, topic: str, scene_count: int, turns: int, mode: str) -> dict[str, Any]:
        mode = self._normalize_template_mode(mode)
        if mode == "english_context":
            return self._english_context_fallback(topic, scene_count, turns)
        if mode == "mother_teaches_ai":
            return self._life_rules_fallback(topic, scene_count, turns)
        return self._fallback_plan(topic, scene_count, turns)

    def _fallback_plan(self, topic: str, scene_count: int, turns: int = 4) -> dict[str, Any]:
        templates = [
            (
                "Mở vấn đề bằng tình huống gần gũi giữa mẹ và bé.",
                "Bé ôm gấu bông, đứng sát mẹ và tỏ vẻ chưa yên tâm; mẹ ngồi xuống ngang tầm mắt để trò chuyện.",
                [
                    ('child','Mẹ ơi, tối nay con ngủ với mẹ nhé?'),
                    ('mother','Mẹ biết con đang lo, nên mình không cần vội đâu con.'),
                    ('child','Nhưng con sợ lúc tắt đèn sẽ buồn lắm.'),
                    ('mother','Vậy mình để đèn ngủ nhỏ sáng một chút nhé.'),
                    ('child','Mẹ vẫn ở gần con chứ ạ?'),
                    ('mother','Có, mẹ sẽ ở cạnh con để con thấy yên tâm.'),
                    ('child','Nếu con vẫn chưa ngủ được thì sao?'),
                    ('mother','Con cứ gọi mẹ, rồi mình thử thở chậm cùng nhau.'),
                    ('child','Con có được ôm gấu cả đêm không?'),
                    ('mother','Tất nhiên, gấu sẽ là bạn ngủ cùng con.'),
                    ('child','Vậy con thử nằm ở đây một lúc nhé.'),
                    ('mother','Được rồi, mình chỉ cần thử từng chút một thôi.')
                ]
            ),
            (
                "Biến giải pháp thành trải nghiệm vui.",
                "Mẹ và bé cùng chuẩn bị góc riêng, xếp gối, đèn ngủ và gấu bông như một trò chơi trước giờ đi ngủ.",
                [
                    ('mother','Con muốn tự chọn chăn hay gấu bông trước nào?'),
                    ('child','Con chọn gấu bông nằm bên này ạ.'),
                    ('mother','Được rồi, đây sẽ là bạn ngủ cùng con tối nay.'),
                    ('child','Con còn muốn chiếc đèn ngôi sao nữa.'),
                    ('mother','Tuyệt, mình bật đèn nhỏ để căn phòng trông ấm áp hơn nhé.'),
                    ('child','Như vậy con thấy thích hơn rồi ạ.'),
                    ('mother','Con muốn đặt chiếc gối ở đâu?'),
                    ('child','Ngay cạnh gấu bông để con dễ ôm ạ.'),
                    ('mother','Vậy góc ngủ này là do chính con sắp xếp rồi.'),
                    ('child','Con thấy giống một ngôi nhà nhỏ của con.'),
                    ('mother','Đúng rồi, nơi này sẽ ngày càng quen thuộc hơn.'),
                    ('child','Tối nay con muốn thử ngủ ở đây ạ.')
                ]
            ),
            (
                "Mẹ trấn an và cho bé quyền tiến từng bước.",
                "Mẹ ngồi cạnh, nắm tay bé; bé nằm trong lều nhỏ và dần bình tĩnh khi nghe mẹ hướng dẫn từng bước.",
                [
                    ('child','Mẹ đừng đi ngay nhé.'),
                    ('mother','Mẹ sẽ chưa đi đâu, mình cùng thở chậm ba lần trước nhé.'),
                    ('child','Một mình con vẫn thấy hơi run.'),
                    ('mother','Khi con run, con ôm gấu và gọi mẹ là được.'),
                    ('child','Mẹ kể cho con một câu chuyện ngắn nhé.'),
                    ('mother','Được, rồi khi con buồn ngủ hơn mình sẽ cùng nhắm mắt.'),
                    ('child','Nếu con mở mắt mà không thấy mẹ thì sao?'),
                    ('mother','Mẹ sẽ báo trước khi bước ra ngoài, không biến mất đột ngột đâu.'),
                    ('child','Mẹ có thể để cửa mở một chút không?'),
                    ('mother','Được chứ, để con vẫn nghe thấy tiếng mẹ bên ngoài.'),
                    ('child','Con thấy yên tâm hơn rồi.'),
                    ('mother','Tốt lắm, mình thử thêm vài phút nữa nhé.')
                ]
            ),
            (
                "Kết thúc tích cực, nhấn mạnh cảm giác an toàn.",
                "Bé ôm gấu nằm yên hơn; mẹ mỉm cười kéo chăn, khen bé đã cố gắng và hứa mai lại tiếp tục.",
                [
                    ('mother','Hôm nay con đã cố gắng hơn hôm qua rất nhiều rồi.'),
                    ('child','Con thấy cũng không đáng sợ như lúc đầu nữa.'),
                    ('mother','Đúng rồi, vì con đang lớn dần và biết tự trấn an mình.'),
                    ('child','Nếu con tỉnh dậy thì con gọi mẹ nhé?'),
                    ('mother','Tất nhiên, mẹ luôn ở đây khi con cần.'),
                    ('child','Vậy con thử ngủ ngoan trong góc của con ạ.'),
                    ('mother','Mẹ rất vui vì con chịu thử, không cần phải hoàn hảo ngay.'),
                    ('child','Ngày mai con có thể tự chọn chuyện trước khi ngủ không?'),
                    ('mother','Được, mình sẽ biến giờ ngủ thành khoảng thời gian con mong chờ.'),
                    ('child','Con nghĩ con sẽ quen dần thôi.'),
                    ('mother','Đúng rồi, mỗi ngày mình tiến thêm một chút.'),
                    ('child','Chúc mẹ ngủ ngon ạ.')
                ]
            ),
        ]
        scenes=[]
        max_turns=max(1,min(12,turns))
        for i in range(scene_count):
            summary,action,dialogue=templates[i%len(templates)]
            turns_norm=[{"speaker":sp,"text":tx} for sp,tx in dialogue[:max_turns]]
            scenes.append({"scene":i+1,"summary":summary,"action":action,"dialogue":turns_norm,"mother":"","child":""})
        return {"title":topic[:90],"hook":topic,"lesson":"Ưu tiên cảm giác an toàn, đồng hành từng bước và tránh ép trẻ; toàn video nên đủ nội dung trao đổi khoảng 25-30 giây.","scenes":scenes,"ai_used":False}

    def generate_plan(self, req: PlanRequest) -> dict[str, Any]:
        cset = self.get_set(req.character_set_id)
        if not cset:
            raise HTTPException(404, "Kh\u00f4ng t\u00ecm th\u1ea5y Character Set")
        template_mode = self._normalize_template_mode(getattr(req, "template_mode", "parenting"))
        effective_order = self._normalize_dialogue_order_value(getattr(req, "dialogue_order", "mother_first"))
        fallback = self._template_fallback_plan(req.topic, req.scene_count, req.dialogue_turns_per_scene, template_mode)
        if not self.router9_enabled():
            return fallback

        if template_mode == "english_context":
            template_instruction = (
                "Template 4: teach English by visible context for Vietnamese children. Required format: mother speaks Vietnamese, child answers in English. "
                "Each scene teaches one short English phrase only. The image must show the context: object/person/place/action must be visible. "
                "Dialogue speakers only mother/child. Add language='vi' for mother and language='en' for child. "
                "Return character_mode='mother_child' and visual_anchor for required prop/person/location."
            )
            allowed = {"mother", "child"}
            effective_order = "mother_first"
        elif template_mode == "mother_teaches_ai":
            template_instruction = (
                "Template 5: children life lesson/rule/situation. Do not force mother-child. Choose character_mode: mother_child, two_children, or teacher_two_children. "
                "Use child_a/child_b for two_children; teacher/child_a/child_b for teacher_two_children; mother/child for mother_child. "
                "Structure: concrete problem appears -> one character may suggest a wrong action -> another refuses or chooses safe/right action -> visible practice -> short lesson_rule. "
                "Avoid scary detail, violence, or step-by-step bad behavior. Keep dialogue natural and short."
            )
            allowed = self._allowed_dialogue_speakers()
            effective_order = "free"
        else:
            template_instruction = "Template 1: warm Vietnamese mother-child mini story with concrete action, natural dialogue, and gentle ending."
            allowed = {"mother", "child"}

        order_instruction = self._dialogue_order_instruction(effective_order) if effective_order != "free" else "Preserve natural authored speaker order."
        system = (
            "You write family-friendly 3D Facebook Reels scripts. Output JSON only. Use Vietnamese dialogue unless english_context child answers in English. "
            "Be concrete and visual; no medical diagnosis, no threats, no judgment. "
            + template_instruction + " " + order_instruction + " "
            "Each line must be short enough for 6-8 second lip-sync clips."
        )
        user = (
            f"Template: {template_mode}\nTopic: {req.topic}\nScenes: {req.scene_count}\nDialogue turns per scene: {req.dialogue_turns_per_scene}\nTone: {req.tone}\n"
            "Return JSON keys: title, hook, lesson, scenes. Each scene: scene, summary, action, character_mode, visual_anchor, lesson_rule, dialogue. "
            "dialogue is array of {speaker,text,language}; language is vi or en. action must include setting plus visible hand/body action; characters cannot only stand and talk."
        )
        try:
            temperature = 0.72 if template_mode == "english_context" else 0.82 if template_mode == "mother_teaches_ai" else 0.65
            out = self.router9_chat_json(model=req.model.strip(), system_prompt=system, user_prompt=user, temperature=temperature)
            scenes = out.get("scenes") if isinstance(out, dict) else None
            if not isinstance(scenes, list) or not scenes:
                return fallback
            norm = []
            fb_scenes = fallback.get("scenes") or []
            for idx, scene in enumerate(scenes[:req.scene_count], 1):
                scene = scene if isinstance(scene, dict) else {}
                character_mode = str(scene.get("character_mode") or ("mother_child" if template_mode != "mother_teaches_ai" else "")).strip().lower()
                if template_mode == "mother_teaches_ai" and character_mode not in {"mother_child", "two_children", "teacher_two_children"}:
                    topic_lower = req.topic.lower()
                    character_mode = "two_children" if any(word in topic_lower for word in ["b\u1ea1n", "r\u1ee7", "gi\u00e0nh", "n\u00f3i d\u1ed1i", "b\u1eaft n\u1ea1t", "l\u1ea5y \u0111\u1ed3"]) else "mother_child"
                if template_mode != "mother_teaches_ai":
                    character_mode = "mother_child"
                raw_dialogue = scene.get("dialogue") if isinstance(scene.get("dialogue"), list) else []
                dialogue = []
                for turn in raw_dialogue[:max(2, req.dialogue_turns_per_scene)]:
                    if not isinstance(turn, dict):
                        continue
                    speaker = str(turn.get("speaker") or "").strip().lower()
                    text_line = str(turn.get("text") or "").strip()[:320]
                    if speaker not in allowed or not text_line:
                        continue
                    language = str(turn.get("language") or turn.get("lang") or "").strip().lower()
                    if language not in {"vi", "en"}:
                        language = "en" if template_mode == "english_context" and speaker == "child" else "vi"
                    dialogue.append({"speaker": speaker, "text": text_line, "language": language})
                if template_mode == "english_context":
                    dialogue = [turn for turn in dialogue if turn["speaker"] in {"mother", "child"}]
                    for turn in dialogue:
                        turn["language"] = "en" if turn["speaker"] == "child" else "vi"
                    dialogue = self._normalize_dialogue_sequence(dialogue, "mother_first", req.dialogue_turns_per_scene)
                else:
                    dialogue = self._normalize_dialogue_sequence(dialogue, effective_order, req.dialogue_turns_per_scene)
                fallback_scene = fb_scenes[(idx - 1) % len(fb_scenes)] if fb_scenes else {}
                if len(dialogue) < 2:
                    dialogue = list(fallback_scene.get("dialogue") or [])[:req.dialogue_turns_per_scene]
                    character_mode = str(fallback_scene.get("character_mode") or character_mode)
                norm.append({
                    "scene": idx,
                    "summary": str(scene.get("summary") or fallback_scene.get("summary") or "")[:500],
                    "action": str(scene.get("action") or fallback_scene.get("action") or "")[:1400],
                    "visual_anchor": str(scene.get("visual_anchor") or fallback_scene.get("visual_anchor") or "")[:900],
                    "lesson_rule": str(scene.get("lesson_rule") or fallback_scene.get("lesson_rule") or "")[:600],
                    "character_mode": character_mode,
                    "dialogue": dialogue[:req.dialogue_turns_per_scene],
                    "dialogue_order": "free" if template_mode == "mother_teaches_ai" else "mother_first" if template_mode == "english_context" else effective_order,
                    "mother": "",
                    "child": "",
                })
            while len(norm) < req.scene_count and fb_scenes:
                row = dict(fb_scenes[len(norm) % len(fb_scenes)])
                row["scene"] = len(norm) + 1
                norm.append(row)
            return {
                "title": str(out.get("title") or fallback["title"])[:100],
                "hook": str(out.get("hook") or fallback["hook"])[:500],
                "lesson": str(out.get("lesson") or fallback["lesson"])[:1000],
                "scenes": norm,
                "ai_used": True,
                "ai_model": req.model.strip() or "AUTO",
                "template_mode": template_mode,
            }
        except Exception as exc:
            fallback["ai_error"] = str(exc)
            return fallback

    def _require_ready_set(self, set_id: str) -> dict[str, Any]:
        cset = self.get_set(set_id)
        if not cset:
            raise HTTPException(404, "Không tìm thấy Character Set")
        if not cset.get("ready"):
            raise HTTPException(400, "Character Set chưa READY. Cần ảnh reference cho cả mẹ và bé trước.")
        return cset

    def _input_refs(self, cset: dict[str, Any]) -> list[dict[str, Any]]:
        refs = []
        for key, role in [("mother", "mother_reference"), ("child", "child_reference"), ("father", "father_reference")]:
            char = cset.get(key)
            if not char:
                continue
            p = str(char.get("reference_path") or "")
            mid = str(char.get("reference_media_id") or "")
            title = str(char.get("reference_title") or self._reference_title(str(char.get('id') or key)))
            file_name = str(char.get("reference_file_name") or self._reference_filename(str(char.get('id') or key), p))
            if (p and Path(p).exists()) or mid:
                refs.append({
                    "path": p if p and Path(p).exists() else "",
                    "name": file_name,
                    "fileName": file_name,
                    "role": role,
                    "characterId": str(char.get("id") or key),
                    "characterSetId": str(cset.get("id") or ""),
                    "mediaId": mid,
                    "title": title,
                    # V3.7: character refs are scene-image inputs only.
                    # Veo receives the generated scene image as the single source frame.
                    "videoReference": False,
                })
        return refs

    async def on_reference_media_replaced(self, msg: dict[str, Any]) -> None:
        """Persist a newly uploaded Flow mediaId for a stable character reference.

        The extension only emits this after the old exact mediaId was not usable and
        a known local reference file was uploaded again. Match by old mediaId first,
        then stable file name / character id. Never update unrelated characters by role alone.
        """
        old_id=str(msg.get("oldMediaId") or "").strip()
        new_id=str(msg.get("newMediaId") or "").strip()
        character_id=str(msg.get("characterId") or "").strip()
        file_name=str(msg.get("fileName") or msg.get("name") or "").strip()
        if not new_id or new_id.startswith("composer:"):
            return
        now=self._now()
        with self._conn() as c:
            row=None
            if character_id:
                row=c.execute("SELECT id FROM parenting_characters WHERE id=? LIMIT 1",(character_id,)).fetchone()
            if row is None and old_id:
                row=c.execute("SELECT id FROM parenting_characters WHERE reference_media_id=? ORDER BY updated_at DESC LIMIT 1",(old_id,)).fetchone()
            if row is None and file_name:
                row=c.execute("SELECT id FROM parenting_characters WHERE reference_file_name=? ORDER BY updated_at DESC LIMIT 1",(file_name,)).fetchone()
            if row is None:
                return
            cid=str(row["id"])
            c.execute("UPDATE parenting_characters SET reference_media_id=?,updated_at=? WHERE id=?",(new_id,now,cid))
        try:
            await self.ui_broadcast({"type":"PARENTING_REFERENCE_MEDIA_UPDATED","characterId":cid,"oldMediaId":old_id or None,"newMediaId":new_id,"fileName":file_name or None})
        except Exception:
            pass

    def _dialogue_turns(self, scene: dict[str, Any]) -> list[dict[str,str]]:
        out=[]
        raw=scene.get('dialogue') if isinstance(scene.get('dialogue'),list) else []
        for turn in raw[:12]:
            if not isinstance(turn,dict): continue
            sp=str(turn.get('speaker') or '').strip().lower()
            tx=self._safe_dialogue_line(str(turn.get('text') or ''))
            if sp in {'mother','child'} and tx: out.append({'speaker':sp,'text':tx})
        if not out:
            for sp,key in [('mother','mother'),('child','child')]:
                tx=self._safe_dialogue_line(str(scene.get(key) or ''))
                if tx: out.append({'speaker':sp,'text':tx})
        return out

    @staticmethod
    def _safe_dialogue_line(text: str) -> str:
        return (text or "").replace('"', "'").strip()

    @staticmethod
    def _continuation_factor(mode: str, requested_turns: int) -> int:
        m=str(mode or "auto").strip().lower()
        if m in {"off","x1","1","none"}:
            return 1
        if m in {"x2","2"}:
            return 2
        if m in {"x3","3"}:
            return 3
        if m in {"x4","4"}:
            return 4
        turns=max(1,int(requested_turns or 1))
        if turns <= 4:
            return 1
        if turns <= 6:
            return 2
        if turns <= 8:
            return 3
        return 4

    @staticmethod
    def _split_turns(turns: list[dict[str,str]], factor: int) -> list[list[dict[str,str]]]:
        factor=max(1,min(4,int(factor or 1)))
        if factor == 1:
            return [turns]
        total=len(turns)
        if total == 0:
            return [[] for _ in range(factor)]
        chunks=[]
        start=0
        for i in range(factor):
            remaining=total-start
            slots=factor-i
            take=max(0,(remaining+slots-1)//slots)
            chunks.append(turns[start:start+take])
            start += take
        return chunks

    def _video_prompt_for_turns(self, *, cset: dict[str, Any], action: str, turns: list[dict[str,str]], segment_no: int=1, segment_total: int=1) -> str:
        mother=cset["mother"]
        child=cset["child"]
        dialogue=[]
        for idx,turn in enumerate(turns,1):
            speaker='MOTHER' if turn['speaker']=='mother' else 'CHILD'
            voice=mother['voice_prompt'] if turn['speaker']=='mother' else child['voice_prompt']
            dialogue.append(f'Turn {idx}: {speaker} speaks Vietnamese, exact words: "{turn["text"]}". Voice style: {voice}')
        if not dialogue:
            dialogue.append("No new spoken dialogue in this continuation; continue the same action naturally.")
        continuity=(
            f"This is continuation segment {segment_no}/{segment_total}. Continue seamlessly from the previous clip; do not restart the scene. "
            if segment_no>1 else
            f"This is segment 1/{segment_total}. Begin naturally and leave motion/composition ready for the next continuation. "
        )
        return (
            continuity +
            "Keep the exact same mother and child faces, outfits, room, lighting and spatial positions. "
            f"Action: {action}. " + " ".join(dialogue) + " "
            "Follow the dialogue turns in exact order. Lip movement must closely match Vietnamese speech. "
            "Only the current speaker moves their mouth; the other listens and reacts naturally. "
            "Preserve camera direction and action continuity across segments. Natural blinking, breathing and hand motion. "
            "Keep native dialogue audio clear. No subtitles, no on-screen text, no watermark."
        )

    def _build_video_chain(self, *, cset: dict[str, Any], scene: dict[str, Any], requested_turns: int, continuation_mode: str) -> tuple[str,list[dict[str,str]],list[list[dict[str,str]]],int]:
        action=str(scene.get("action") or scene.get("summary") or "Mẹ và bé tương tác tự nhiên.").strip()
        turns=self._dialogue_turns(scene)
        factor=self._continuation_factor(continuation_mode,requested_turns)
        chunks=self._split_turns(turns,factor)
        prompts=[self._video_prompt_for_turns(cset=cset,action=action,turns=chunk,segment_no=i+1,segment_total=factor) for i,chunk in enumerate(chunks)]
        segments=[{"role":f"segment_{i+1}","prompt":prompt} for i,prompt in enumerate(prompts)]
        return prompts[0],segments,chunks,factor

    def _scene_prompts(self, *, cset: dict[str, Any], scene: dict[str, Any], topic: str) -> tuple[str, str]:
        mother = cset["mother"]
        child = cset["child"]
        action = str(scene.get("action") or scene.get("summary") or "Mẹ và bé tương tác tự nhiên trong phòng ngủ trẻ em ấm áp.").strip()
        turns = self._dialogue_turns(scene)
        image_prompt = (
            "Use the uploaded MOTHER and CHILD reference images as the identity source of truth. "
            "Create one premium 3D animated family-film frame, vertical 9:16. "
            f"Topic: {topic}. Scene: {action}. "
            f"Mother: {mother['visual_prompt']}. Child: {child['visual_prompt']}. "
            "Keep the exact same faces, hair, age feel, outfit colors and proportions from the references. "
            "Warm cozy family atmosphere, clear expressions, natural body language, clean composition, soft cinematic lighting. "
            "No text, no subtitles, no watermark."
        )
        dialogue = []
        for idx, turn in enumerate(turns, 1):
            speaker = 'MOTHER' if turn['speaker'] == 'mother' else 'CHILD'
            voice = mother['voice_prompt'] if turn['speaker'] == 'mother' else child['voice_prompt']
            dialogue.append(f'Turn {idx}: {speaker} speaks Vietnamese, exact words: "{turn["text"]}". Voice style: {voice}')
        if not dialogue:
            dialogue.append("No spoken dialogue; only natural room ambience and subtle character movement.")
        video_prompt = (
            "Animate the scene with the exact same mother and child identities. "
            f"Action: {action}. " + " ".join(dialogue) + " "
            "Follow all dialogue turns in the exact listed order. Lip movement must match Vietnamese speech closely. "
            "Only the current speaker moves their mouth; the other character listens and reacts naturally. "
            "Natural blinking, breathing, gentle head and hand motion, realistic timing, medium close-up or medium shot. "
            "Keep native dialogue audio clear. No subtitles, no text, no watermark."
        )
        return image_prompt, video_prompt

    def build_test_scene(self, req: TestSceneRequest) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        cset = self._require_ready_set(req.character_set_id)
        product = self.get_product(str(req.product_id)) if req.product_id else None
        if req.product_id and not product:
            raise HTTPException(404, "Không tìm thấy sản phẩm Shopee cho Test 1 Scene")
        scene={
            "scene": 1,
            "summary": "Lip-sync test mẹ và bé" + (" + sản phẩm Shopee" if product else ""),
            "action": req.scene_description,
            "mother": req.mother_text,
            "child": req.child_text,
            "dialogue": req.dialogue,
        }
        if product:
            # V2.0: Test Shopee can test SETUP without leaking the product, or reveal/payoff with it.
            # Product is still IMAGE-only; Veo receives the generated scene frame rather than a fourth raw ingredient.
            include_product = bool(req.product_visible)
            ip = self._product_story_image_prompt(cset, product, scene, include_product=include_product)
            refs = self._input_refs(cset) + ([self._product_ref(product)] if include_product else [])
            if include_product:
                scene["action"] = (str(scene.get("action") or "").strip() + " Product is already part of this scene; preserve the exact referenced design.").strip()
        else:
            ip, _ = self._scene_prompts(cset=cset, scene=scene, topic="Test lip-sync tiếng Việt mẹ và bé")
            refs = self._input_refs(cset)
        requested=max(1,len(self._dialogue_turns(scene)))
        vp,video_segments,chunks,factor=self._build_video_chain(cset=cset,scene=scene,requested_turns=requested,continuation_mode=req.continuation_mode)
        ad_text=self._safe_dialogue_line(str(req.product_ad_text or "")).strip() if product else ""
        if ad_text and video_segments:
            ad_instruction=(
                " AFTER the mother-child lines finish, both characters stop speaking and stop lip-sync. "
                "Reserve the final about 4 seconds for a clean product beauty/review shot. "
                f'OFF-SCREEN Vietnamese adult female narrator says exact words: "{ad_text}". '
                f"Narrator voice style: {req.ad_voice_prompt}. Do not make mother or child mouth this narrator line."
            )
            video_segments[-1]["prompt"] = str(video_segments[-1].get("prompt") or "") + ad_instruction
            if factor==1:
                vp = video_segments[-1]["prompt"]
        flow = self.default_flow_config(
            imageModel=req.image_model,
            videoModel=req.video_model,
            imageConcurrency=1,
            videoConcurrency=1,
            aspectRatio="9:16",
            imageOutputs="x1",
            videoOutputs="x1",
            videoDuration=req.video_duration,
            videoExtendFactor=f"x{factor}",
            autoDownloadVideo=False,
            maxSubmitsPerMinute=4,
        )
        scenes=[{
            "sceneId": 1,
            "imagePrompt": ip,
            "videoPrompt": vp,
            "videoSegments": video_segments,
            "inputImages": refs,
            "metadata": {
                "parenting": True,
                "parentingMode": "test_shopee_scene" if product else "test_scene",
                "characterSetId": cset["id"],
                "motherText": req.mother_text,
                "childText": req.child_text,
                "dialogueTurns": self._dialogue_turns(scene),
                "dialogueChunks": chunks,
                "videoChainFactor": factor,
                "continuationMode": req.continuation_mode,
                "productId": product.get("id") if product else None,
                "productTitle": product.get("title") if product else None,
                "productVisible": bool(req.product_visible) if product else False,
                "containsAdVoice": bool(ad_text),
                "productAdText": ad_text,
                "adVoicePrompt": req.ad_voice_prompt if ad_text else "",
            },
        }]
        return scenes, flow

    def build_story(self, req: StoryGenerateRequest) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        cset = self._require_ready_set(req.character_set_id)
        plan = self.generate_plan(PlanRequest(topic=req.topic, character_set_id=req.character_set_id, scene_count=req.scene_count, dialogue_turns_per_scene=req.dialogue_turns_per_scene, model=req.model, tone=req.tone, template_mode=req.template_mode, dialogue_order=req.dialogue_order))
        refs = self._input_refs(cset)
        factor=self._continuation_factor(req.continuation_mode,req.dialogue_turns_per_scene)
        scenes=[]
        for i, s in enumerate(plan["scenes"], 1):
            ip, _ = self._scene_prompts(cset=cset, scene=s, topic=req.topic)
            vp,video_segments,chunks,_factor=self._build_video_chain(cset=cset,scene=s,requested_turns=req.dialogue_turns_per_scene,continuation_mode=req.continuation_mode)
            scenes.append({
                "sceneId": i,
                "imagePrompt": ip,
                "videoPrompt": vp,
                "videoSegments": video_segments,
                "inputImages": refs,
                "metadata": {
                    "parenting": True,
                    "parentingMode": "story",
                    "contentTemplate": req.template_mode,
                    "videoInputMode": "scene_image_only",
                    "characterSetId": cset["id"],
                    "topic": req.topic,
                    "summary": s.get("summary"),
                    "action": s.get("action"),
                    "motherText": s.get("mother") or "",
                    "childText": s.get("child") or "",
                    "dialogueTurns": self._dialogue_turns(s),
                    "dialogueChunks": chunks,
                    "dialogueVi": [x.get("text") for x in self._dialogue_turns(s)],
                    "videoChainFactor": _factor,
                    "continuationMode": req.continuation_mode,
                },
            })
        flow = self.default_flow_config(
            imageModel=req.image_model,
            videoModel=req.video_model,
            imageConcurrency=min(4, max(1, len(scenes))),
            videoConcurrency=min(2, max(1, len(scenes))),
            aspectRatio="9:16",
            imageOutputs="x1",
            videoOutputs="x1",
            videoDuration=req.video_duration,
            videoExtendFactor=f"x{factor}",
            autoDownloadVideo=False,
            maxSubmitsPerMinute=6,
        )
        plan["continuation_mode"]=req.continuation_mode
        plan["video_chain_factor"]=factor
        return plan, scenes, flow

    def _save_run(self, req: StoryGenerateRequest, plan: dict[str, Any], flow_job_id: str) -> str:
        rid = f"parent_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = self._now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO parenting_story_runs(id,flow_job_id,character_set_id,topic,title,plan_json,status,burn_subtitles,auto_publish,facebook_page_id,facebook_dry_run,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, flow_job_id, req.character_set_id, req.topic, str(plan.get("title") or req.topic), self._dumps(plan), "generating", int(req.burn_subtitles), int(req.auto_publish), req.facebook_page_id or None, int(req.facebook_dry_run), now, now),
            )
        return rid

    def get_run_by_flow(self, flow_job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM parenting_story_runs WHERE flow_job_id=?", (flow_job_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["plan"] = self._loads(d.pop("plan_json"), {})
        d["burn_subtitles"] = bool(d["burn_subtitles"])
        d["auto_publish"] = bool(d["auto_publish"])
        d["facebook_dry_run"] = bool(d["facebook_dry_run"])
        return d

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM parenting_story_runs ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 200),)).fetchall()
        out=[]
        for r in rows:
            d=dict(r)
            d["plan"] = self._loads(d.pop("plan_json"), {})
            d["burn_subtitles"] = bool(d["burn_subtitles"])
            d["auto_publish"] = bool(d["auto_publish"])
            d["facebook_dry_run"] = bool(d["facebook_dry_run"])
            if d.get("final_path"):
                try:
                    rel=Path(d["final_path"]).resolve().relative_to(self.output_dir.resolve())
                    d["final_url"]="/outputs/" + "/".join(rel.parts)
                except Exception:
                    d["final_url"] = None
            out.append(d)
        return out

    def _run_cmd(self, cmd: list[str], timeout: int = 600, cwd: str | Path | None = None) -> None:
        run_cmd=list(cmd)
        exe=Path(str(run_cmd[0])).name.lower() if run_cmd else ""
        if exe in {"ffmpeg", "ffmpeg.exe"}:
            # Hide the 4KB build banner so logs contain the REAL failure, not gcc/config noise.
            if "-hide_banner" not in run_cmd:
                run_cmd[1:1]=["-hide_banner","-loglevel","error"]
        try:
            cp = subprocess.run(run_cmd, capture_output=True, text=True, timeout=timeout, cwd=(str(cwd) if cwd else None))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"FFMPEG_TIMEOUT sau {timeout}s · {' '.join(str(x) for x in run_cmd[:8])}") from exc
        if cp.returncode != 0:
            err=(cp.stderr or cp.stdout or "ffmpeg failed").strip()[-3500:]
            short=' '.join(str(x) for x in run_cmd[:10])
            raise RuntimeError(f"FFMPEG_EXIT_{cp.returncode} · {short} · {err}")

    def _strict_video_probe(self, path: Path) -> dict[str, Any]:
        p=Path(path)
        if not p.exists() or not p.is_file():
            raise RuntimeError(f"SOURCE_VIDEO_INVALID: file không tồn tại · {p}")
        if p.stat().st_size < max(1024,int(os.getenv('FLOW_VIDEO_MIN_VALID_BYTES','4096') or 4096)):
            raise RuntimeError(f"SOURCE_VIDEO_INVALID: file quá nhỏ {p.stat().st_size} bytes · {p.name}")
        cp=subprocess.run([
            "ffprobe","-v","error","-show_entries",
            "format=duration:stream=codec_type,width,height,duration","-of","json",str(p)
        ],capture_output=True,text=True,timeout=30)
        if cp.returncode != 0:
            raise RuntimeError(f"SOURCE_VIDEO_INVALID: ffprobe reject {p.name} · {(cp.stderr or cp.stdout or '')[-800:]}")
        try: data=json.loads(cp.stdout or '{}')
        except Exception as exc: raise RuntimeError(f"SOURCE_VIDEO_INVALID: ffprobe JSON {p.name} · {exc}")
        streams=data.get('streams') if isinstance(data,dict) else []
        vids=[x for x in (streams or []) if isinstance(x,dict) and x.get('codec_type')=='video']
        if not vids:
            raise RuntimeError(f"SOURCE_VIDEO_INVALID: không có video stream · {p.name}")
        vals=[]
        for raw in [vids[0].get('duration'),((data.get('format') or {}).get('duration') if isinstance(data,dict) else None)]:
            try:
                if raw not in (None,''): vals.append(float(raw))
            except Exception: pass
        dur=max(vals or [0.0])
        if dur < max(0.25,float(os.getenv('FLOW_VIDEO_MIN_VALID_DURATION','1.0') or 1.0)):
            raise RuntimeError(f"SOURCE_VIDEO_INVALID: duration {dur:.3f}s · {p.name}")
        return {'duration':dur,'has_audio':any(isinstance(x,dict) and x.get('codec_type')=='audio' for x in (streams or []))}

    def _video_assets(self, job_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM assets WHERE job_id=? AND kind='video' ORDER BY scene_id ASC,created_at ASC", (job_id,)).fetchall()
        out=[]
        seen=set()
        for r in rows:
            d=dict(r)
            p=str(d.get("local_path") or "")
            if not p or not Path(p).exists():
                continue
            try:
                d["probe"] = self._strict_video_probe(Path(p))
            except Exception:
                continue
            key=str(d.get('media_id') or '') or str(Path(p).resolve())
            if key in seen:
                continue
            seen.add(key)
            meta=self._loads(d.get('metadata_json'),{})
            d['metadata']=meta if isinstance(meta,dict) else {}
            try:
                d['media_index']=int((d['metadata'] or {}).get('mediaIndex',9999))
            except Exception:
                d['media_index']=9999
            out.append(d)
        out.sort(key=lambda x:(int(x.get('scene_id') or 0),int(x.get('media_index') if x.get('media_index') is not None else 9999),str(x.get('created_at') or '')))
        return out

    @staticmethod
    def _ass_time(seconds: float) -> str:
        cs = max(0, int(round(seconds * 100)))
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s, c = divmod(rem, 100)
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"

    @staticmethod
    def _ass_escape(text: str) -> str:
        return str(text or "").replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")

    def _write_ass(self, path: Path, scenes: list[dict[str, Any]], durations: list[float]) -> None:
        lines=[
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
            "Style: Default,Arial,62,&H00FFFFFF,&H000000FF,&H00111111,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,70,70,180,1",
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        ]
        t=0.0
        for i, dur in enumerate(durations):
            meta=(scenes[i].get("metadata") or {}) if i < len(scenes) else {}
            parts=[]
            turns=meta.get("dialogueTurns") if isinstance(meta.get("dialogueTurns"),list) else []
            if turns:
                for turn in turns:
                    if not isinstance(turn,dict) or not turn.get('text'): continue
                    speaker=str(turn.get('speaker') or '')
                    prefix="Mẹ: " if speaker=='mother' else ("Bé: " if speaker=='child' else "Quảng cáo: ")
                    parts.append(prefix + str(turn.get('text')))
            else:
                if meta.get("motherText"): parts.append("Mẹ: " + str(meta.get("motherText")))
                if meta.get("childText"): parts.append("Bé: " + str(meta.get("childText")))
            if parts:
                text=self._ass_escape("\\N".join(parts))
                start=t+0.25
                end=max(start+0.5, t+dur-0.25)
                lines.append(f"Dialogue: 0,{self._ass_time(start)},{self._ass_time(end)},Default,,0,0,0,,{text}")
            t += dur
        path.write_text("\n".join(lines), encoding="utf-8-sig")

    def _ffprobe_duration(self, path: Path) -> float:
        cp=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],capture_output=True,text=True,timeout=30)
        if cp.returncode != 0:
            return 8.0
        try:
            return max(0.1, float(cp.stdout.strip()))
        except Exception:
            return 8.0

    @staticmethod
    def _truthy_env(name: str, default: bool = False) -> bool:
        raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
        return raw in {"1", "true", "yes", "on", "y"}

    def _music_library_dir(self) -> Path:
        raw = str(os.getenv("MUSIC_LIBRARY_DIR", "") or "").strip()
        p = Path(raw).expanduser() if raw else (self.db_path.parent / "music")
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def _music_cache_dir(self) -> Path:
        p = self.output_dir / "_music_cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _local_music_files(self) -> list[Path]:
        allowed = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
        root = self._music_library_dir()
        files: list[Path] = []
        try:
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in allowed and p.stat().st_size > 4096:
                    files.append(p.resolve())
        except Exception:
            pass
        return sorted(files, key=lambda x: x.name.lower())

    def music_status(self) -> dict[str, Any]:
        local = self._local_music_files()
        jamendo_id = str(os.getenv("JAMENDO_CLIENT_ID", "") or "").strip()
        jamendo_license = self._truthy_env("JAMENDO_COMMERCIAL_LICENSE_CONFIRMED", False)
        mubert_customer = str(os.getenv("MUBERT_CUSTOMER_ID", "") or "").strip()
        mubert_token = str(os.getenv("MUBERT_ACCESS_TOKEN", "") or "").strip()
        return {
            "local": {"ready": bool(local), "tracks": len(local), "dir": str(self._music_library_dir())},
            "jamendo": {
                "ready": bool(jamendo_id and jamendo_license),
                "client_id": bool(jamendo_id),
                "commercial_license_confirmed": jamendo_license,
            },
            "mubert": {
                "ready": bool(mubert_customer and mubert_token),
                "customer_id": bool(mubert_customer),
                "access_token": bool(mubert_token),
                "playlist_index": str(os.getenv("MUBERT_PLAYLIST_INDEX", "") or "").strip(),
            },
            "security": {
                "remote_code_execution": False,
                "download_types": ["audio"],
                "max_download_mb": max(1, int(os.getenv("MUSIC_MAX_DOWNLOAD_MB", "30") or 30)),
                "ffprobe_validation": True,
            },
        }

    @staticmethod
    def _music_style_tags(style: str) -> str:
        s = (style or "").lower()
        tags: list[str] = []
        mapping = [
            (("electronic", "điện tử"), "electronic"),
            (("dance", "nhảy", "giật"), "dance"),
            (("playful", "vui", "cute"), "playful"),
            (("upbeat", "sôi động", "energetic", "dynamic"), "groove"),
            (("pop",), "pop"),
            (("hiphop", "hip hop"), "hiphop"),
            (("funk",), "funk"),
        ]
        for keys, tag in mapping:
            if any(k in s for k in keys) and tag not in tags:
                tags.append(tag)
        if not tags:
            tags = ["electronic", "groove", "pop"]
        return " ".join(tags[:4])

    def _audio_probe(self, path: Path) -> dict[str, Any]:
        cp = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,codec_name",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if cp.returncode != 0:
            raise RuntimeError("ffprobe không đọc được file nhạc")
        try:
            data = json.loads(cp.stdout or "{}")
        except Exception:
            data = {}
        streams = data.get("streams") if isinstance(data, dict) else []
        if not any(isinstance(x, dict) and x.get("codec_type") == "audio" for x in (streams or [])):
            raise RuntimeError("File tải về không có audio stream")
        try:
            dur = float(((data.get("format") or {}).get("duration") or 0))
        except Exception:
            dur = 0.0
        if dur <= 0.2:
            raise RuntimeError("File nhạc quá ngắn/không hợp lệ")
        return {"duration": dur, "streams": streams}

    def _safe_audio_download(self, url: str, dst: Path, *, provider: str) -> Path:
        allowed_suffixes = list({
            "jamendo": ("jamendo.com",),
            "mubert": ("mubert.com", "storage.googleapis.com"),
        }.get(provider, ()))
        extra=[x.strip().lower().lstrip('.') for x in str(os.getenv("MUSIC_EXTRA_AUDIO_HOSTS", "") or "").split(',') if x.strip()]
        allowed_suffixes.extend(extra)
        current = str(url or "").strip()
        if not current:
            raise RuntimeError(f"{provider}: thiếu audio URL")
        max_bytes = max(1, int(os.getenv("MUSIC_MAX_DOWNLOAD_MB", "30") or 30)) * 1024 * 1024
        from urllib.parse import urljoin
        for _ in range(4):
            u = urlparse(current)
            host = (u.hostname or "").lower()
            if u.scheme != "https" or not host or not any(host == s or host.endswith("." + s) for s in allowed_suffixes):
                raise RuntimeError(f"{provider}: chặn audio URL ngoài allowlist: {host or 'unknown'}")
            resp = requests.get(
                current, stream=True, timeout=(10, 45), allow_redirects=False,
                headers={"User-Agent": "ParentingContentFactory/2.5"},
            )
            if resp.status_code in {301, 302, 303, 307, 308}:
                nxt = resp.headers.get("Location") or ""
                resp.close()
                if not nxt:
                    raise RuntimeError(f"{provider}: redirect thiếu Location")
                current = urljoin(current, nxt)
                continue
            resp.raise_for_status()
            ctype = str(resp.headers.get("Content-Type") or "").lower()
            if ctype and not any(x in ctype for x in ("audio/", "application/octet-stream", "binary/octet-stream", "application/force-download")):
                resp.close()
                raise RuntimeError(f"{provider}: Content-Type không phải audio ({ctype[:80]})")
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(dst.suffix + ".part")
            total = 0
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        resp.close()
                        tmp.unlink(missing_ok=True)
                        raise RuntimeError(f"{provider}: audio vượt {max_bytes // 1024 // 1024}MB")
                    f.write(chunk)
            resp.close()
            if total < 4096:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"{provider}: audio tải về quá nhỏ")
            tmp.replace(dst)
            self._audio_probe(dst)
            return dst.resolve()
        raise RuntimeError(f"{provider}: quá nhiều redirect")

    def _music_mark_used(self, source_key: str, provider: str, title: str, local_path: str, metadata: dict[str, Any]) -> None:
        now = self._now()
        with self._conn() as c:
            old = c.execute(
                "SELECT used_count,created_at FROM parenting_music_usage WHERE source_key=?",
                (source_key,),
            ).fetchone()
            count = int(old["used_count"] or 0) + 1 if old else 1
            created = str(old["created_at"]) if old else now
            c.execute(
                "INSERT OR REPLACE INTO parenting_music_usage(source_key,provider,title,local_path,metadata_json,used_count,last_used_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (source_key, provider, title, local_path, self._dumps(metadata), count, now, created, now),
            )
        self._music_recent_keys.append(source_key)
        self._music_recent_keys = self._music_recent_keys[-20:]

    def _choose_local_music(self) -> dict[str, Any]:
        files = self._local_music_files()
        if not files:
            raise RuntimeError(f"LOCAL MUSIC: thư mục {self._music_library_dir()} chưa có MP3/WAV")
        recent = set(self._music_recent_keys[-min(10, len(files)):])
        with self._conn() as c:
            usage = {
                str(r["source_key"]): (int(r["used_count"] or 0), str(r["last_used_at"] or ""))
                for r in c.execute(
                    "SELECT source_key,used_count,last_used_at FROM parenting_music_usage WHERE provider='local'"
                ).fetchall()
            }
        scored = []
        for p in files:
            key = "local:" + hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:24]
            used, last = usage.get(key, (0, ""))
            scored.append((key in recent, used, last, random.random(), key, p))
        scored.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        _, _, _, _, key, p = scored[0]
        probe = self._audio_probe(p)
        meta = {"provider": "local", "source_key": key, "title": p.stem, "duration": probe["duration"], "path": str(p)}
        self._music_mark_used(key, "local", p.stem, str(p), meta)
        return {"path": str(p), "metadata": meta}

    def _jamendo_music(self, *, style: str) -> dict[str, Any]:
        client_id = str(os.getenv("JAMENDO_CLIENT_ID", "") or "").strip()
        if not client_id:
            raise RuntimeError("JAMENDO_CLIENT_ID chưa cấu hình")
        if not self._truthy_env("JAMENDO_COMMERCIAL_LICENSE_CONFIRMED", False):
            raise RuntimeError(
                "Jamendo bị khóa: đặt JAMENDO_COMMERCIAL_LICENSE_CONFIRMED=1 chỉ sau khi bạn có quyền/license thương mại phù hợp"
            )
        params = {
            "client_id": client_id,
            "format": "json",
            "limit": 50,
            "audioformat": "mp32",
            "audiodlformat": "mp32",
            "fuzzytags": self._music_style_tags(style),
            "speed": "high veryhigh",
            "vocalinstrumental": "instrumental",
            "prolicensing": "true",
            "include": "musicinfo",
        }
        resp = requests.get(
            "https://api.jamendo.com/v3.0/tracks/",
            params=params, timeout=(10, 30),
            headers={"User-Agent": "ParentingContentFactory/2.5"},
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        rows = data.get("results") if isinstance(data, dict) else []
        choices = []
        recent = set(self._music_recent_keys[-20:])
        for row in rows or []:
            if not isinstance(row, dict) or not bool(row.get("audiodownload_allowed")):
                continue
            audio = str(row.get("audiodownload") or "").strip()
            if not audio:
                continue
            key = f"jamendo:{row.get('id')}"
            choices.append((key in recent, random.random(), key, row, audio))
        if not choices:
            raise RuntimeError("Jamendo không trả track instrumental/high-speed có audiodownload_allowed")
        choices.sort(key=lambda x: (x[0], x[1]))
        _, _, key, row, audio = choices[0]
        dst = self._music_cache_dir() / f"jamendo_{self._slug(str(row.get('id') or uuid.uuid4().hex[:8]))}.mp3"
        if not dst.exists() or dst.stat().st_size < 4096:
            self._safe_audio_download(audio, dst, provider="jamendo")
        probe = self._audio_probe(dst)
        meta = {
            "provider": "jamendo",
            "source_key": key,
            "track_id": str(row.get("id") or ""),
            "title": str(row.get("name") or ""),
            "artist": str(row.get("artist_name") or ""),
            "license_ccurl": str(row.get("license_ccurl") or ""),
            "prolicensing": True,
            "duration": probe["duration"],
        }
        self._music_mark_used(key, "jamendo", meta["title"], str(dst), meta)
        return {"path": str(dst.resolve()), "metadata": meta}

    def _mubert_music(self, *, style: str, intensity: str, duration: float) -> dict[str, Any]:
        customer = str(os.getenv("MUBERT_CUSTOMER_ID", "") or "").strip()
        token = str(os.getenv("MUBERT_ACCESS_TOKEN", "") or "").strip()
        if not customer or not token:
            raise RuntimeError("MUBERT_CUSTOMER_ID / MUBERT_ACCESS_TOKEN chưa cấu hình")
        headers = {
            "customer-id": customer,
            "access-token": token,
            "Content-Type": "application/json",
            "User-Agent": "ParentingContentFactory/2.5",
        }
        playlist = str(os.getenv("MUBERT_PLAYLIST_INDEX", "") or "").strip()
        seconds = max(8, min(40, int(math.ceil(duration))))
        body: dict[str, Any] = {
            "duration": seconds,
            "bitrate": 128,
            "format": "mp3",
            "intensity": intensity if intensity in {"low", "medium", "high"} else "high",
        }
        if playlist:
            body.update({"playlist_index": playlist, "mode": "jingle" if seconds <= 40 else "track"})
        else:
            prompt = (style or "dynamic electronic playful upbeat").strip()[:150]
            body.update({
                "prompt": f"{prompt}, instrumental, rhythmic, short-form social video, no vocals"[:200],
                "mode": "loop",
            })
        resp = requests.post(
            "https://music-api.mubert.com/api/v3/public/tracks",
            headers=headers, json=body, timeout=(10, 30),
        )
        if resp.status_code not in {200, 201, 202}:
            raise RuntimeError(f"Mubert create HTTP {resp.status_code}: {(resp.text or '')[:300]}")
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        track = (payload.get("data") if isinstance(payload, dict) else None) or {}
        track_id = str(track.get("id") or "")
        if not track_id:
            raise RuntimeError("Mubert create không trả track id; kiểm tra quyền Track Generation của license")
        import time
        timeout = max(30, int(os.getenv("MUBERT_GENERATION_TIMEOUT", "150") or 150))
        deadline = time.time() + timeout
        done = None
        while time.time() < deadline:
            gr = requests.get(
                f"https://music-api.mubert.com/api/v3/public/tracks/{track_id}",
                headers={k: v for k, v in headers.items() if k != "Content-Type"},
                timeout=(10, 30),
            )
            gr.raise_for_status()
            gd = gr.json() if gr.content else {}
            td = (gd.get("data") if isinstance(gd, dict) else None) or {}
            gens = td.get("generations") if isinstance(td, dict) else []
            for g in gens or []:
                if isinstance(g, dict) and str(g.get("status") or "").lower() == "done" and g.get("url"):
                    done = (td, g)
                    break
            if done:
                break
            time.sleep(2)
        if not done:
            raise RuntimeError(f"Mubert generation timeout sau {timeout}s")
        td, g = done
        url = str(g.get("url") or "")
        dst = self._music_cache_dir() / f"mubert_{self._slug(track_id)}.mp3"
        self._safe_audio_download(url, dst, provider="mubert")
        probe = self._audio_probe(dst)
        key = f"mubert:{track_id}"
        meta = {
            "provider": "mubert",
            "source_key": key,
            "track_id": track_id,
            "title": f"Mubert {track_id[:8]}",
            "duration": probe["duration"],
            "bpm": td.get("bpm"),
            "key": td.get("key"),
            "intensity": td.get("intensity") or body.get("intensity"),
            "mode": td.get("mode") or body.get("mode"),
        }
        self._music_mark_used(key, "mubert", meta["title"], str(dst), meta)
        return {"path": str(dst.resolve()), "metadata": meta}

    def _resolve_music_for_run(self, run: dict[str, Any], duration: float) -> dict[str, Any] | None:
        plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        cfg = plan.get("music_config") if isinstance(plan.get("music_config"), dict) else {}
        if not cfg or not bool(cfg.get("enabled", False)):
            return None
        provider = str(cfg.get("provider") or "auto").lower()
        style = str(cfg.get("style") or "dynamic electronic playful upbeat")
        intensity = str(cfg.get("intensity") or "high").lower()
        if provider == "off":
            return None
        status = self.music_status()
        if provider == "auto":
            if status["mubert"]["ready"]:
                providers = ["mubert", "local", "jamendo"]
            elif status["local"]["ready"]:
                providers = ["local", "jamendo"]
            elif status["jamendo"]["ready"]:
                providers = ["jamendo"]
            else:
                providers = []
        else:
            providers = [provider]
        errors: list[str] = []
        for p in providers:
            try:
                if p == "local":
                    return self._choose_local_music()
                if p == "jamendo":
                    return self._jamendo_music(style=style)
                if p == "mubert":
                    return self._mubert_music(style=style, intensity=intensity, duration=duration)
            except Exception as exc:
                errors.append(f"{p}: {exc}")
        if bool(cfg.get("required", False)):
            raise RuntimeError("Không lấy được background music: " + " | ".join(errors[:3]))
        return {"path": None, "metadata": {"provider": provider, "skipped": True, "errors": errors[:3]}}

    def _mix_background_music(self, video: Path, music_path: Path, final: Path, *, volume: float, ducking: bool) -> None:
        probe = self._audio_probe(music_path)
        vdur = self._ffprobe_duration(video)
        mdur = float(probe.get("duration") or 0)
        max_start = max(0.0, mdur - vdur - 0.25)
        start = random.uniform(0, max_start) if max_start > 1.0 else 0.0
        vol = max(0.0, min(0.5, float(volume)))
        if ducking:
            filt = (
                f"[1:a]atrim=start={start:.3f},asetpts=PTS-STARTPTS,volume={vol:.4f},afade=t=in:st=0:d=0.12[m];"
                f"[m][0:a]sidechaincompress=threshold=0.018:ratio=10:attack=12:release=260[md];"
                f"[0:a][md]amix=inputs=2:duration=first:dropout_transition=2,alimiter=limit=0.96[aout]"
            )
        else:
            filt = (
                f"[1:a]atrim=start={start:.3f},asetpts=PTS-STARTPTS,volume={vol:.4f},afade=t=in:st=0:d=0.12[m];"
                f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=2,alimiter=limit=0.96[aout]"
            )
        self._run_cmd(
            [
                "ffmpeg", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(music_path),
                "-filter_complex", filt,
                "-map", "0:v:0", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movflags", "+faststart", "-shortest", str(final),
            ],
            timeout=300,
        )

    def render_story_sync(self, flow_job_id: str) -> str:
        run = self.get_run_by_flow(flow_job_id)
        if not run:
            raise RuntimeError("Không tìm thấy parenting run")
        job = self.get_flow_job(flow_job_id) or {}
        # Idempotent render: if a valid final already exists, never spend FFmpeg time again.
        existing = str(run.get("final_path") or "").strip()
        if existing and Path(existing).exists():
            try:
                if self._ffprobe_duration(Path(existing)) > 1.0:
                    return str(Path(existing).resolve())
            except Exception:
                pass
        scenes = job.get("scenes") or []
        all_videos = self._video_assets(flow_job_id)
        # Select artifacts scene-by-scene. Retries may leave several successful mediaIds
        # for the same scene; taking the first N globally can duplicate scene 1 and omit
        # a later scene. Prefer the newest valid attempt for x1, or one clip per mediaIndex
        # for chained output.
        videos=[]
        missing=[]
        for sid,scene in enumerate(scenes,1):
            need=max(1,int(((scene.get('metadata') or {}).get('videoChainFactor') or 1)))
            cand=[v for v in all_videos if int(v.get('scene_id') or 0)==sid]
            chosen=[]
            if need==1:
                if cand: chosen=[cand[-1]]
            else:
                by_index={}
                for v in cand:
                    idx=int(v.get('media_index') if v.get('media_index') is not None else 9999)
                    if idx!=9999: by_index[idx]=v
                if len(by_index)>=need:
                    chosen=[by_index[k] for k in sorted(by_index)[:need]]
                elif len(cand)>=need:
                    chosen=cand[-need:]
            if len(chosen)<need:
                missing.append(f"scene {sid}: {len(chosen)}/{need}")
            videos.extend(chosen)
        expected=sum(max(1,int(((s.get('metadata') or {}).get('videoChainFactor') or 1))) for s in scenes)
        if missing or len(videos) < expected:
            raise RuntimeError(f"SOURCE_VIDEO_INVALID_OR_MISSING: thiếu video segment theo scene: {', '.join(missing) or f'{len(videos)}/{expected}'}")
        outdir=self.output_dir / "parenting" / flow_job_id
        work=outdir / "work"
        work.mkdir(parents=True, exist_ok=True)
        normalized=[]
        durations=[]
        render_segments=[]
        for i, asset in enumerate(videos[:expected], 1):
            src=Path(asset["local_path"])
            dst=work / f"segment_{i:03d}.mp4"
            probe=self._strict_video_probe(src)
            vf="scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p"
            if probe.get("has_audio"):
                cmd=["ffmpeg","-y","-i",str(src),"-vf",vf,
                     "-c:v","libx264","-preset","veryfast","-crf","20",
                     "-c:a","aac","-b:a","192k","-ar","48000","-ac","2",
                     "-movflags","+faststart",str(dst)]
            else:
                # Some Flow clips legitimately have no audio stream. Add silence so concat/music
                # filters always receive a deterministic stereo track instead of dying on [0:a].
                cmd=["ffmpeg","-y","-i",str(src),"-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000",
                     "-map","0:v:0","-map","1:a:0","-vf",vf,
                     "-c:v","libx264","-preset","veryfast","-crf","20",
                     "-c:a","aac","-b:a","192k","-ar","48000","-ac","2","-shortest",
                     "-movflags","+faststart",str(dst)]
            self._run_cmd(cmd, timeout=300)
            normalized.append(dst)
            durations.append(self._ffprobe_duration(dst))
            sid=int(asset.get('scene_id') or 0)
            scene=(scenes[sid-1] if 0 < sid <= len(scenes) else {"metadata":{}})
            meta=dict(scene.get('metadata') or {})
            chunks=meta.get('dialogueChunks') if isinstance(meta.get('dialogueChunks'),list) else []
            seg_idx=int(asset.get('media_index') if asset.get('media_index') not in (None,9999) else 0)
            if chunks and 0 <= seg_idx < len(chunks):
                meta['dialogueTurns']=chunks[seg_idx]
            elif chunks and len(chunks)==1:
                meta['dialogueTurns']=chunks[0]
            render_segments.append({"metadata":meta})
        concat=work / "concat.txt"
        concat.write_text("\n".join("file '" + p.as_posix().replace("'", "'\\''") + "'" for p in normalized) + "\n", encoding="utf-8")
        joined=work / "joined.mp4"
        self._run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(joined)], timeout=300)
        visual=outdir / "final_parenting_nomusic.mp4"
        final=outdir / "final_parenting.mp4"
        if run.get("burn_subtitles"):
            ass=work / "dialogue.ass"
            self._write_ass(ass, render_segments, durations)
            # Windows filter parser treats the drive colon in D:/... as the next ASS
            # positional option (original_size). Run ffmpeg from the work directory and
            # pass a colon-free relative filename instead.
            ass_filter = "ass=filename=dialogue.ass"
            self._run_cmd(["ffmpeg","-y","-i",str(joined.resolve()),"-vf",ass_filter,"-c:v","libx264","-preset","medium","-crf","20","-c:a","copy","-movflags","+faststart",str(visual.resolve())], timeout=600, cwd=work)
        else:
            shutil.copy2(joined, visual)
        music_info=self._resolve_music_for_run(run,self._ffprobe_duration(visual))
        if music_info and music_info.get("path"):
            cfg=((run.get("plan") or {}).get("music_config") or {})
            self._mix_background_music(visual,Path(str(music_info["path"])),final,volume=float(cfg.get("volume",0.12)),ducking=bool(cfg.get("ducking",True)))
        else:
            shutil.copy2(visual,final)
        asset_meta={"source":"PARENTING_RENDER","runId":run["id"],"segments":expected}
        if music_info:
            asset_meta["music"]=music_info.get("metadata") or {}
        self.add_asset(flow_job_id, 0, "final_video", local_path=str(final.resolve()), title=str(run.get("title") or "Parenting video"), metadata=asset_meta)
        with self._conn() as c:
            c.execute("UPDATE parenting_story_runs SET status='done',final_path=?,error=NULL,updated_at=? WHERE flow_job_id=?", (str(final.resolve()), self._now(), flow_job_id))
            if run.get("campaign_item_id"):
                c.execute("UPDATE parenting_auto_items SET status='ready',final_path=?,error=NULL,updated_at=? WHERE id=?", (str(final.resolve()), self._now(), str(run.get("campaign_item_id"))))
        return str(final.resolve())

    async def render_story(self, flow_job_id: str) -> None:
        # One render per job at a time. Duplicate FLOW_RESULT / download-complete events
        # are expected in a recovery engine and must be idempotent.
        if flow_job_id in self._render_running:
            await self.ui_broadcast({"type":"PARENTING_RENDER_DEDUPED","jobId":flow_job_id})
            return
        self._render_running.add(flow_job_id)
        try:
            run=self.get_run_by_flow(flow_job_id) or {}
            existing=str(run.get("final_path") or "").strip()
            if existing and Path(existing).exists():
                try:
                    if self._ffprobe_duration(Path(existing)) > 1.0:
                        self.update_flow_job(flow_job_id,status="done",error=None)
                        with self._conn() as c:
                            c.execute("UPDATE parenting_story_runs SET status='done',error=NULL,updated_at=? WHERE flow_job_id=?",(self._now(),flow_job_id))
                            if run.get("campaign_item_id"):
                                c.execute("UPDATE parenting_auto_items SET status='ready',final_path=?,error=NULL,next_retry_at=NULL,last_failure_class=NULL,updated_at=? WHERE id=?",(existing,self._now(),str(run.get("campaign_item_id"))))
                        await self.ui_broadcast({"type":"PARENTING_VIDEO_READY","jobId":flow_job_id,"localPath":existing,"reused":True})
                        if run.get("campaign_id"):
                            await self.campaign_tick()
                        return
                except Exception:
                    pass

            self.update_flow_job(flow_job_id, status="rendering", error=None)
            with self._conn() as c:
                c.execute("UPDATE parenting_story_runs SET status='rendering',updated_at=? WHERE flow_job_id=?", (self._now(), flow_job_id))
            await self.ui_broadcast({"type":"PARENTING_RENDER_STARTED","jobId":flow_job_id})
            final=await asyncio.to_thread(self.render_story_sync, flow_job_id)
            self.update_flow_job(flow_job_id, status="done", error=None)
            await self.ui_broadcast({"type":"PARENTING_VIDEO_READY","jobId":flow_job_id,"localPath":final})
            run=self.get_run_by_flow(flow_job_id) or {}
            if run.get("auto_publish") and run.get("facebook_page_id") and self.create_publish_job and self.facebook_publish_request_cls:
                req=self.facebook_publish_request_cls(
                    page_id=run["facebook_page_id"], video_path=final,
                    title=str(run.get("title") or ""), description=str(run.get("title") or ""),
                    dry_run=bool(run.get("facebook_dry_run", True))
                )
                self.create_publish_job(req)
            if run.get("campaign_id"):
                with self._conn() as c:
                    total=int(c.execute("SELECT COUNT(*) FROM parenting_auto_items WHERE batch_id=?",(str(run.get("campaign_batch_id") or ''),)).fetchone()[0])
                    ready=int(c.execute("SELECT COUNT(*) FROM parenting_auto_items WHERE batch_id=? AND status IN ('ready','publishing','published')",(str(run.get("campaign_batch_id") or ''),)).fetchone()[0])
                await self._auto_log(str(run.get("campaign_id")),str(run.get("campaign_batch_id") or ''),'video_ready',f'Video READY {ready}/{total} · {Path(final).name} · xét đăng NGAY theo lịch Page X; không chờ đủ {total} video.',current=ready,total=total)
                await self.campaign_tick()
        except Exception as exc:
            run=self.get_run_by_flow(flow_job_id) or {}
            item_id=str(run.get("campaign_item_id") or "")
            err_text=str(exc)
            # Corrupt/missing local MP4 is NOT a render retry. Return the Flow job to the
            # checkpoint dispatcher: it first re-downloads the SAVED mediaId; only if that
            # mediaId cannot be verified does V4.5 regenerate that scene.
            if err_text.startswith("SOURCE_VIDEO_INVALID"):
                self.update_flow_job(flow_job_id,status="queued",error=None,agent_id=None,
                                     retry_reason="render_preflight_media_recovery",last_stage="render_preflight_recovery",next_retry_at=None)
                with self._conn() as c:
                    c.execute("UPDATE parenting_story_runs SET status='waiting_media_recovery',error=?,updated_at=? WHERE flow_job_id=?",(err_text[:1000],self._now(),flow_job_id))
                    if item_id:
                        c.execute("UPDATE parenting_auto_items SET status='generating',next_retry_at=NULL,last_failure_class='render_media_recovery',error=?,updated_at=? WHERE id=?",(err_text[:1000],self._now(),item_id))
                await self.ui_broadcast({"type":"PARENTING_RENDER_MEDIA_RECOVERY","jobId":flow_job_id,"error":err_text})
                if run.get("campaign_id"):
                    await self._auto_log(str(run.get("campaign_id")),str(run.get("campaign_batch_id") or ''),'render_media_recovery',f'Render preflight phát hiện MP4 local hỏng/thiếu → cứu mediaId trước, không tạo lại ngay · {err_text}',level='warning')
                await self.dispatch_jobs()
                return
            retry=0
            if item_id:
                with self._conn() as c:
                    row=c.execute("SELECT render_retry_count FROM parenting_auto_items WHERE id=?",(item_id,)).fetchone()
                    retry=int(row[0] or 0)+1 if row else 1
            max_retry=max(1,int(os.getenv('AUTO_FB_RENDER_MAX_RETRIES','3') or 3))
            if item_id and retry <= max_retry:
                delay=min(90,5*(2**max(0,retry-1)))
                next_at=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(timespec='seconds')
                # Flow artifacts are still good. Retry only FFmpeg/render later.
                self.update_flow_job(flow_job_id,status="flow_done",error=None)
                with self._conn() as c:
                    c.execute("UPDATE parenting_story_runs SET status='render_retry',error=?,updated_at=? WHERE flow_job_id=?",(str(exc)[:1000],self._now(),flow_job_id))
                    c.execute("UPDATE parenting_auto_items SET status='render_retry',render_retry_count=?,next_retry_at=?,last_failure_class='render_transient',error=?,updated_at=? WHERE id=?",(retry,next_at,str(exc)[:1000],self._now(),item_id))
                await self.ui_broadcast({"type":"PARENTING_RENDER_RETRY","jobId":flow_job_id,"retryCount":retry,"maxRetries":max_retry,"delaySec":delay,"error":str(exc)})
                if run.get("campaign_id"):
                    await self._auto_log(str(run.get("campaign_id")),str(run.get("campaign_batch_id") or ''),'render_retry',f'Render lỗi nhưng KHÔNG generate lại Flow · retry {retry}/{max_retry} sau {delay}s · {exc}',level='warning')
            else:
                self.update_flow_job(flow_job_id, status="failed", error=f"Parenting render lỗi: {exc}")
                with self._conn() as c:
                    c.execute("UPDATE parenting_story_runs SET status='failed',error=?,updated_at=? WHERE flow_job_id=?", (str(exc), self._now(), flow_job_id))
                    if item_id:
                        c.execute("UPDATE parenting_auto_items SET status='failed',last_failure_class='render_permanent',error=?,updated_at=? WHERE id=?",(str(exc)[:1000],self._now(),item_id))
                await self.ui_broadcast({"type":"PARENTING_RENDER_FAILED","jobId":flow_job_id,"error":str(exc)})
                if run.get("campaign_id"):
                    await self._auto_log(str(run.get("campaign_id")),str(run.get("campaign_batch_id") or ''),'render_failed',f'Render FAILED sau retry budget · {flow_job_id} · {exc}',level='error')
                    await self.campaign_tick()
        finally:
            self._render_running.discard(flow_job_id)

    async def on_image_ready(self, job: dict[str, Any], scene_id: int, local_path: str | None, media_id: str | None = None, title: str | None = None) -> None:
        if not local_path and not media_id:
            return
        kind=str(job.get("kind") or "")
        if kind != "parenting_character_master":
            return
        scenes=job.get("scenes") or []
        if not (0 < scene_id <= len(scenes)):
            return
        meta=scenes[scene_id-1].get("metadata") or {}
        cid=str(meta.get("characterId") or "")
        if not cid:
            return
        stable_path = None
        stable_name = None
        if local_path:
            stable_path, stable_name = self._materialize_reference_copy(cid, local_path)
        ref_title = self._reference_title(cid)
        ref_file_name = stable_name or self._reference_filename(cid, stable_path or local_path)
        with self._conn() as c:
            resolved = stable_path or (str(Path(local_path).resolve()) if local_path else None)
            c.execute(
                "UPDATE parenting_characters SET reference_path=COALESCE(?,reference_path),reference_media_id=COALESCE(?,reference_media_id),reference_title=COALESCE(?,reference_title),reference_file_name=COALESCE(?,reference_file_name),generated_job_id=?,updated_at=? WHERE id=?",
                (resolved, media_id or None, ref_title, ref_file_name, job.get("id"), self._now(), cid),
            )
        await self.ui_broadcast({"type":"PARENTING_CHARACTER_READY","characterId":cid,"jobId":job.get("id"),"localPath":stable_path or local_path,"mediaId":media_id,"title":ref_title,"fileName":ref_file_name})

    async def on_flow_complete(self, job_id: str, ok: bool) -> None:
        job=self.get_flow_job(job_id) or {}
        kind=str(job.get("kind") or "")
        if kind == "parenting_story":
            if ok:
                self.spawn(self.render_story(job_id))
            else:
                run=self.get_run_by_flow(job_id) or {}
                flow_error=str(job.get("error") or "Flow failed").strip()[:1000]
                with self._conn() as c:
                    c.execute("UPDATE parenting_story_runs SET status='failed',error=?,updated_at=? WHERE flow_job_id=?", (flow_error, self._now(), job_id))
                    if run.get("campaign_item_id"):
                        failure_class='flow_permanent' if flow_error.lower().startswith('permanent:') else 'flow_transient'
                        c.execute("UPDATE parenting_auto_items SET status='failed',last_failure_class=?,error=?,updated_at=? WHERE id=?", (failure_class, flow_error, self._now(), str(run.get("campaign_item_id"))))

    # ---------------- Auto FB Hybrid campaign V3.1 ----------------
    @staticmethod
    def _clean_shopee_urls(urls: list[str]) -> list[str]:
        out=[]
        for raw in urls or []:
            u=str(raw or '').strip()
            if not u:
                continue
            if u not in out:
                out.append(u)
        return out[:50]

    async def _auto_log(self, campaign_id: str, batch_id: str | None, phase: str, message: str, *, level: str="info", current: int | None=None, total: int | None=None) -> None:
        now=self._now()
        display_now=datetime.now(VN_TZ).isoformat(timespec="seconds")
        phase=str(phase or 'working'); message=str(message or '').strip()
        if len(message) > 2000:
            # Error cause is normally at the END of FFmpeg stderr. Keep both context + tail.
            message=message[:450] + " ... [tail] ... " + message[-1450:]
        tag=str(campaign_id)[-8:] if campaign_id else '-'
        stamp=datetime.now(VN_TZ).strftime("%H:%M:%S")
        print(f"[AUTO FB][{stamp} +07][{tag}][{phase.upper()}] {message}", flush=True)
        with self._conn() as c:
            c.execute("INSERT INTO parenting_auto_logs(campaign_id,batch_id,level,phase,message,created_at) VALUES(?,?,?,?,?,?)",(campaign_id,batch_id,level,phase,message,display_now))
            c.execute("DELETE FROM parenting_auto_logs WHERE campaign_id=? AND id NOT IN (SELECT id FROM parenting_auto_logs WHERE campaign_id=? ORDER BY id DESC LIMIT 500)",(campaign_id,campaign_id))
            if batch_id:
                fields=["phase=?","phase_message=?","progress_updated_at=?","updated_at=?"]; vals=[phase,message,now,now]
                if current is not None: fields.append("progress_current=?"); vals.append(int(current))
                if total is not None: fields.append("progress_total=?"); vals.append(int(total))
                vals.append(batch_id); c.execute(f"UPDATE parenting_auto_batches SET {','.join(fields)} WHERE id=?",vals)
        try:
            await self.ui_broadcast({"type":"AUTO_FB_PROGRESS","campaignId":campaign_id,"batchId":batch_id,"phase":phase,"message":message,"level":level,"current":current,"total":total,"ts":display_now})
        except Exception:
            pass

    def _campaign_logs(self, campaign_id: str, limit: int=100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows=c.execute("SELECT id,batch_id,level,phase,message,created_at FROM parenting_auto_logs WHERE campaign_id=? ORDER BY id DESC LIMIT ?",(campaign_id,min(max(int(limit),1),300))).fetchall()
        return [dict(r) for r in reversed(rows)]

    def _schedule_campaign_prepare(self, campaign_id: str, resume_batch_id: str | None = None) -> bool:
        if campaign_id in self._campaign_prepare_running: return False
        self._campaign_prepare_running.add(campaign_id)
        async def runner():
            try: await self._prepare_campaign_batch(campaign_id, resume_batch_id=resume_batch_id)
            finally: self._campaign_prepare_running.discard(campaign_id)
        self.spawn(runner()); return True

    def save_auto_page_profile(self, req: AutoFbPageProfileSave) -> dict[str, Any]:
        if int(req.end_hour) <= int(req.start_hour): raise HTTPException(400, "Giờ kết thúc phải lớn hơn giờ bắt đầu")
        page_id=str(req.facebook_page_id).strip(); derived=f"autopage_{hashlib.sha1(page_id.encode('utf-8')).hexdigest()[:12]}"; requested=str(req.profile_id or '').strip(); pid=derived; now=self._now()
        with self._conn() as c:
            if requested:
                old_req=c.execute("SELECT id,facebook_page_id FROM parenting_auto_pages WHERE id=?",(requested,)).fetchone()
                if old_req:
                    pid=requested
                    dup=c.execute("SELECT id FROM parenting_auto_pages WHERE facebook_page_id=? AND id<>?",(page_id,pid)).fetchone()
                    if dup:
                        raise HTTPException(409, f"Page này đã thuộc config {dup['id']}")
            old=c.execute("SELECT created_at FROM parenting_auto_pages WHERE id=?",(pid,)).fetchone(); created=str(old['created_at']) if old else now
            c.execute("INSERT INTO parenting_auto_pages(id,facebook_page_id,name,posts_per_day,start_hour,end_hour,dry_run,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET facebook_page_id=excluded.facebook_page_id,name=excluded.name,posts_per_day=excluded.posts_per_day,start_hour=excluded.start_hour,end_hour=excluded.end_hour,dry_run=excluded.dry_run,enabled=excluded.enabled,updated_at=excluded.updated_at",(pid,page_id,req.name.strip() or 'Page X',int(req.posts_per_day),int(req.start_hour),int(req.end_hour),int(req.dry_run),int(req.enabled),created,now))
        return self.get_auto_page_profile(pid) or {}

    def get_auto_page_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r=c.execute("SELECT * FROM parenting_auto_pages WHERE id=?",(str(profile_id),)).fetchone()
        if not r:
            return None
        d=dict(r); d['dry_run']=bool(d['dry_run']); d['enabled']=bool(d['enabled'])
        return d

    def list_auto_page_profiles(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows=c.execute("SELECT * FROM parenting_auto_pages ORDER BY updated_at DESC").fetchall()
        out=[]
        for r in rows:
            d=dict(r); d['dry_run']=bool(d['dry_run']); d['enabled']=bool(d['enabled']); out.append(d)
        return out

    def _campaign_row(self, campaign_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r=c.execute("SELECT * FROM parenting_auto_campaigns WHERE id=?",(campaign_id,)).fetchone()
        if not r:
            return None
        d=dict(r)
        d['source_links']=self._loads(d.pop('source_links_json'),[])
        d['burn_subtitles']=bool(d['burn_subtitles'])
        d['auto_resume']=bool(d.get('auto_resume', 1))
        d['music_config']=self._loads(d.pop('music_config_json', '{}'), {})
        d['shopee_config']=self._loads(d.pop('shopee_config_json', '{}'), {})
        return d

    def _batch_row(self, batch_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r=c.execute("SELECT * FROM parenting_auto_batches WHERE id=?",(batch_id,)).fetchone()
        if not r:
            return None
        d=dict(r)
        for k,outk,default in [('source_links_json','source_links',[]),('product_ids_json','product_ids',[]),('candidates_json','candidates',[]),('selected_json','selected',[])]:
            d[outk]=self._loads(d.pop(k),default)
        return d

    def _item_rows(self, batch_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows=c.execute("SELECT * FROM parenting_auto_items WHERE batch_id=? ORDER BY rank_no ASC",(batch_id,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d['candidate']=self._loads(d.pop('candidate_json'),{}); d['plan']=self._loads(d.pop('plan_json'),{})
            product=self.get_product(str(d.get('product_id') or ''))
            d['product_title']=(product or {}).get('title') or d.get('product_id')
            # Surface checkpoint state in Auto FB UI. This is intentionally derived
            # from persisted scene checkpoints, not volatile extension RAM.
            jid=str(d.get('flow_job_id') or '')
            if jid:
                try:
                    with self._conn() as c:
                        cps=c.execute("SELECT scene_id,image_status,video_status,video_media_ids_json,video_local_paths_json,video_download_urls_json,last_error FROM flow_scene_checkpoints WHERE job_id=? ORDER BY scene_id",(jid,)).fetchall()
                    scene_total=len(cps); image_ready=0; video_ready=0; generated=0; errors=0; media_ids=[]; signed_urls=0
                    for cp in cps:
                        if str(cp['image_status'] or '').lower()=='ready': image_ready+=1
                        vst=str(cp['video_status'] or '').lower()
                        paths=self._loads(cp['video_local_paths_json'] or '[]',[])
                        valid=[x for x in paths if x and Path(str(x)).exists()]
                        if valid or vst=='ready': video_ready+=1
                        mids=self._loads(cp['video_media_ids_json'] or '[]',[])
                        for mid in mids:
                            mid=str(mid or '').strip()
                            if mid and mid not in media_ids: media_ids.append(mid)
                        urls=self._loads(cp['video_download_urls_json'] or '{}',{})
                        if isinstance(urls,dict): signed_urls+=sum(1 for u in urls.values() if str(u or '').strip())
                        if vst in {'generated','downloading','ready'} or mids: generated+=1
                        if cp['last_error']: errors+=1
                    d['checkpoint']={'scenes':scene_total,'image_ready':image_ready,'video_generated':generated,'video_ready':video_ready,'errors':errors,'media_ids':media_ids,'signed_urls':signed_urls}
                except Exception:
                    d['checkpoint']={}
            out.append(d)
        return out

    def _auto_generation_window(self, campaign: dict[str, Any] | None = None) -> int:
        """Maximum unpublished Auto FB videos allowed to exist/be in-flight at once.

        V4.1 defaults to ONE final video active at a time. A 32s video has 4 scenes,
        so Flow can still parallelize its 4 image/video scenes while the server avoids
        generating a pile of unpublished final videos.
        """
        try:
            raw=int(os.getenv('AUTO_FB_ACTIVE_VIDEO_WINDOW','1') or 1)
        except Exception:
            raw=1
        return max(1,min(4,raw))

    def _enforce_generation_window(self, batch_id: str) -> dict[str, int]:
        """Park excess queued jobs from older V3.7 batches before dispatching after restart.

        We only park jobs that are not currently owned by a live worker (queued/interrupted/held).
        This makes upgrading an existing 10-video generating batch immediately collapses to
        the configured rolling window (default 1 video) instead of redispatching all 10 again.
        """
        window=self._auto_generation_window(None)
        stats={'held':0,'kept_active':0,'buffered':0}
        now=self._now()
        with self._conn() as c:
            rows=c.execute(
                "SELECT i.id item_id,i.status item_status,i.flow_job_id,i.final_path,f.status flow_status "
                "FROM parenting_auto_items i LEFT JOIN flow_jobs f ON f.id=i.flow_job_id "
                "WHERE i.batch_id=? ORDER BY i.rank_no",(batch_id,)
            ).fetchall()
            buffered=sum(1 for r in rows if str(r['item_status'] or '').lower() in {'ready','publishing','publish_failed'} and str(r['final_path'] or '').strip())
            stats['buffered']=buffered
            free=max(0,window-buffered)
            for r in rows:
                ist=str(r['item_status'] or '').lower(); fs=str(r['flow_status'] or '').lower(); jid=str(r['flow_job_id'] or '')
                if ist!='generating' or not jid:
                    continue
                if fs in {'running','dispatching'}:
                    # Already owned by a live worker; do not yank it mid-submit.
                    stats['kept_active']+=1
                    free=max(0,free-1)
                    continue
                if free>0:
                    stats['kept_active']+=1; free-=1; continue
                if fs in {'queued','interrupted','held','planned',''}:
                    c.execute("UPDATE flow_jobs SET status='held',agent_id=NULL,error=NULL,updated_at=? WHERE id=?",(now,jid))
                    c.execute("UPDATE parenting_story_runs SET status='planned',error=NULL,updated_at=? WHERE flow_job_id=?",(now,jid))
                    c.execute("UPDATE parenting_auto_items SET status='planned',error=NULL,updated_at=? WHERE id=?",(now,r['item_id']))
                    stats['held']+=1
        return stats

    async def _activate_planned_items(self, campaign: dict[str, Any], batch: dict[str, Any], items: list[dict[str, Any]] | None = None) -> int:
        """Release held Flow jobs gradually instead of sending all batch videos at once.

        The rolling window counts generating + ready + publishing items. READY items stay
        in the window until they are published, so a 10-video batch is a script/backlog,
        not 10 simultaneously generated videos.
        """
        items=list(items or self._item_rows(str(batch.get('id') or '')))
        window=self._auto_generation_window(campaign)
        live_states={'generating','queued','dispatching','running','rendering','render_retry','ready','publish_failed','publishing'}
        live=sum(1 for x in items if str(x.get('status') or '').lower() in live_states)
        free=max(0,window-live)
        if free<=0:
            return 0
        planned=[x for x in items if str(x.get('status') or '').lower()=='planned'][:free]
        if not planned:
            return 0
        now=self._now(); activated=0
        with self._conn() as c:
            for item in planned:
                jid=str(item.get('flow_job_id') or '')
                if not jid:
                    continue
                # Only release jobs deliberately parked by V3.8. Do not resurrect a genuine failed job here.
                row=c.execute("SELECT status FROM flow_jobs WHERE id=?",(jid,)).fetchone()
                if not row or str(row['status'] or '').lower() not in {'held','planned'}:
                    continue
                c.execute("UPDATE flow_jobs SET status='queued',error=NULL,agent_id=NULL,updated_at=? WHERE id=?",(now,jid))
                c.execute("UPDATE parenting_story_runs SET status='generating',error=NULL,updated_at=? WHERE flow_job_id=?",(now,jid))
                c.execute("UPDATE parenting_auto_items SET status='generating',error=NULL,updated_at=? WHERE id=?",(now,item['id']))
                activated+=1
        if activated:
            await self._auto_log(str(campaign['id']),str(batch['id']),'flow_generation',f'ROLLING QUEUE: kích hoạt {activated} video kế tiếp · cửa sổ active tối đa {window} video. Không queue cả batch cùng lúc.',current=0,total=len(items))
            await self.dispatch_jobs()
        return activated

    def _campaign_next_due(self, campaign: dict[str, Any], page: dict[str, Any] | None = None) -> datetime | None:
        page=page or self.get_auto_page_profile(str(campaign.get('page_profile_id') or ''))
        if not page or not page.get('enabled'):
            return None
        tz=timezone(timedelta(hours=7))
        now=datetime.now(tz)
        start=now.replace(hour=int(page.get('start_hour') or 8),minute=0,second=0,microsecond=0)
        end=now.replace(hour=int(page.get('end_hour') or 22),minute=0,second=0,microsecond=0)
        if end <= start:
            end=start+timedelta(hours=14)
        day_start_utc=start.astimezone(timezone.utc).isoformat(timespec='seconds')
        day_end_utc=(start+timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec='seconds')
        with self._conn() as c:
            n=int(c.execute(
                "SELECT COUNT(*) FROM parenting_auto_items i JOIN parenting_auto_campaigns c ON c.id=i.campaign_id WHERE c.page_profile_id=? AND i.published_at>=? AND i.published_at<?",
                (page['id'],day_start_utc,day_end_utc),
            ).fetchone()[0])
        per=max(1,int(page.get('posts_per_day') or 1))
        if n >= per:
            return (start+timedelta(days=1)).astimezone(timezone.utc)
        if per==1:
            target=start
        else:
            span=(end-start).total_seconds()
            target=start+timedelta(seconds=span*(n/(per-1)))
        if now > end and n < per:
            return (start+timedelta(days=1)).astimezone(timezone.utc)
        return target.astimezone(timezone.utc)

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        c=self._campaign_row(campaign_id)
        if not c:
            return None
        c['page_profile']=self.get_auto_page_profile(str(c.get('page_profile_id') or ''))
        batch=self._batch_row(str(c.get('current_batch_id') or '')) if c.get('current_batch_id') else None
        c['current_batch']=batch
        items=self._item_rows(str(batch['id'])) if batch else []
        c['items']=items
        counts={}
        for x in items:
            counts[x['status']]=counts.get(x['status'],0)+1
        c['counts']=counts
        c['logs']=self._campaign_logs(campaign_id,100); c['prepare_task_running']=campaign_id in self._campaign_prepare_running
        if batch:
            c['links_pending_next_batch']=list(c.get('source_links') or []) != list(batch.get('source_links') or [])
            last=batch.get('progress_updated_at') or batch.get('updated_at')
            try:
                dt=datetime.fromisoformat(str(last).replace('Z','+00:00')) if last else None
                c['progress_age_seconds']=max(0,int((datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())) if dt else None
            except Exception: c['progress_age_seconds']=None
        else:
            c['links_pending_next_batch']=False; c['progress_age_seconds']=None
        due=self._campaign_next_due(c,c.get('page_profile'))
        c['next_publish_at']=due.astimezone(VN_TZ).isoformat(timespec='seconds') if due else None
        return c

    def list_campaigns(self, limit: int=20) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows=c.execute("SELECT id FROM parenting_auto_campaigns ORDER BY updated_at DESC LIMIT ?",(min(max(int(limit),1),100),)).fetchall()
        return [x for x in (self.get_campaign(str(r['id'])) for r in rows) if x]

    async def preview_shopee_search(self, req: ShopeeSearchPreviewRequest) -> dict[str, Any]:
        if self.search_products is None:
            raise HTTPException(503, "Shopee search worker chua san sang")
        want=max(1,min(10,int(req.count or 5)))
        keywords=[]
        for raw in req.keywords:
            kw=_clean_shopee_search_keyword(raw)
            if kw and kw not in keywords:
                keywords.append(kw)
        if not keywords:
            keywords=self._campaign_product_search_topics(
                str(req.content_pillar or 'mixed'),
                want,
                f"preview_{uuid.uuid4().hex[:8]}",
                0,
            )
        items=[]
        seen=set()
        errors=[]
        affiliate_id=str(req.affiliate_id or '').strip()
        for kw in keywords[:10]:
            if len(items)>=want:
                break
            try:
                rows=await self.search_products(kw, min(10, max(4, want-len(items)+2)))
                for row in (rows or []):
                    if len(items)>=want:
                        break
                    if not isinstance(row,dict):
                        continue
                    raw_url=str(row.get('url') or row.get('productUrl') or row.get('product_url') or '').strip()
                    try:
                        url=self._validate_shopee_url(raw_url)
                    except Exception:
                        continue
                    if url in seen:
                        continue
                    seen.add(url)
                    item={
                        "keyword":kw,
                        "url":url,
                        "title":str(row.get('title') or row.get('name') or row.get('productName') or '').strip()[:300],
                        "price":str(row.get('price') or row.get('priceText') or '').strip()[:120],
                        "image":str(row.get('image') or row.get('imageUrl') or row.get('thumbnail') or '').strip()[:2048],
                    }
                    if affiliate_id:
                        item["affiliate_url"]=self._make_shopee_affiliate_link(url, affiliate_id, ["preview", kw])
                    items.append(item)
            except Exception as exc:
                errors.append(f"{kw}: {exc}")
        return {
            "items":items,
            "keywords":keywords[:10],
            "count":len(items),
            "errors":errors[:10],
        }

    def _campaign_candidate_chunk(self, product: dict[str, Any], *, count: int, model: str, seed: str) -> list[dict[str, Any]]:
        facts=self._product_facts(product)
        noun=self._product_noun_from_facts(facts)
        system=(
            "Bạn là biên kịch Facebook Reels chuyên mini-story Mẹ và Bé có tích hợp sản phẩm. "
            "Mỗi ý tưởng phải có hook đời thường, vấn đề cảm xúc rõ, product reveal hợp lý, payoff và resolution. "
            "Không viết quảng cáo ngay từ đầu, không dùng tên Shopee/shop trong thoại, không dùng câu AI chung chung. "
            "Hành động hình ảnh phải cụ thể cho Veo: ai đứng ở đâu, tay làm gì, di chuyển bao nhiêu bước; tránh từ mơ hồ như 'lùi lại', 'xem thử', 'một chút'. "
            "Trả JSON duy nhất {candidates:[...]}."
        )
        user=(
            f"Sản phẩm: {self._dumps(facts)}\nTên gọi ngắn: {noun}\nSeed: {seed}\n"
            f"Hãy tạo đúng {count} KỊCH BẢN COMPACT KHÁC NHAU. Mỗi candidate keys: hook, angle, setup, reveal, payoff, resolution, ad_angle, dialogue_preview. "
            "GIỮ OUTPUT NGẮN: hook/angle/setup/reveal/payoff/resolution/ad_angle mỗi field chỉ 1 câu, tối đa 18 từ. "
            "dialogue_preview đúng 4 câu thoại theo thứ tự, mỗi phần tử {speaker:'mother'|'child',text:'...'}, mỗi text tối đa 12 từ. "
            "Hook/angle phải khác nhau thật, nhưng mọi claim sản phẩm chỉ lấy từ facts. Không markdown."
        )
        out=self.router9_chat_json(model=model.strip(),system_prompt=system,user_prompt=user,temperature=0.95,timeout_seconds=max(210,min(300,int(os.getenv('AUTO_FB_CANDIDATE_CHUNK_TIMEOUT','210') or 210))),max_tokens=max(700,min(1200,500+count*260)),allow_model_fallback=not bool(model.strip()))
        raw=out.get('candidates') if isinstance(out,dict) else None
        if not isinstance(raw,list):
            raise RuntimeError('9Router không trả candidates')
        result=[]
        for row in raw[:count]:
            if not isinstance(row,dict): continue
            clean={k:re.sub(r'\s+',' ',str(row.get(k) or '')).strip()[:500] for k in ('hook','angle','setup','reveal','payoff','resolution','ad_angle')}
            preview=[]
            for t in (row.get('dialogue_preview') or [])[:6]:
                if isinstance(t,dict):
                    sp=str(t.get('speaker') or '').strip().lower(); tx=re.sub(r'\s+',' ',str(t.get('text') or '')).strip()[:220]
                    if sp in {'mother','child'} and tx: preview.append({'speaker':sp,'text':tx})
                elif str(t or '').strip():
                    preview.append({'speaker':'child' if len(preview)%2==0 else 'mother','text':re.sub(r'\s+',' ',str(t)).strip()[:220]})
            clean['dialogue_preview']=preview
            if not clean['hook'] or not clean['angle'] or len(preview)<3: continue
            clean['product_id']=product['id']; clean['candidate_id']=f"cand_{uuid.uuid4().hex[:12]}"; result.append(clean)
        return result

    def _selector_balance(self, candidates: list[dict[str, Any]], ai_selected: list[str], product_ids: list[str], target: int) -> list[dict[str, Any]]:
        by_id={str(x.get('candidate_id')):x for x in candidates}
        order=[]
        for cid in ai_selected:
            if cid in by_id and cid not in order: order.append(cid)
        for x in candidates:
            cid=str(x.get('candidate_id'))
            if cid and cid not in order: order.append(cid)
        selected=[]; used=set(); counts={pid:0 for pid in product_ids}
        # Coverage first: each product gets at least one when possible.
        if len(product_ids) <= target:
            for pid in product_ids:
                cid=next((x for x in order if x not in used and str(by_id[x].get('product_id'))==pid),None)
                if cid:
                    selected.append(by_id[cid]); used.add(cid); counts[pid]+=1
        # Then prefer max two per product.
        for cid in order:
            if len(selected)>=target: break
            if cid in used: continue
            row=by_id[cid]; pid=str(row.get('product_id') or '')
            if counts.get(pid,0)>=2: continue
            selected.append(row); used.add(cid); counts[pid]=counts.get(pid,0)+1
        # If product count is small, fill remaining after everyone already has up to two.
        for cid in order:
            if len(selected)>=target: break
            if cid in used: continue
            selected.append(by_id[cid]); used.add(cid)
        return selected[:target]

    def _select_campaign_candidates_once(self, candidates: list[dict[str, Any]], *, target: int, model: str) -> list[str]:
        compact=[{
            'id':x.get('candidate_id'),'product_id':x.get('product_id'),'hook':x.get('hook'),'angle':x.get('angle'),
            'reveal':x.get('reveal'),'payoff':x.get('payoff'),'resolution':x.get('resolution'),'dialogue_preview':x.get('dialogue_preview')
        } for x in candidates]
        system=(
            "Bạn là biên tập trưởng chọn kịch bản Facebook parenting commerce. Chọn kịch bản có hook tự nhiên, hội thoại có thể phát triển thành 30-32s, "
            "sản phẩm xuất hiện như giải pháp sau setup, có payoff rõ, không gượng ép, không quảng cáo sớm, dễ dựng bằng Veo. "
            "Loại ý tưởng chung chung, cụt, vô lý hoặc giống nhau. Trả JSON duy nhất."
        )
        user=(
            f"Cần chọn {target} từ danh sách sau. Chấm nội bộ: hook 25, tự nhiên 25, tích hợp sản phẩm 20, continuity 15, payoff 10, dễ dựng 5. "
            f"Candidates: {self._dumps(compact)}\nTrả {{selected:[{{id:'...',score:0-100,reason:'...'}}]}} theo thứ tự tốt nhất."
        )
        out=self.router9_chat_json(
            model=model.strip(),system_prompt=system,user_prompt=user,temperature=0.25,
            timeout_seconds=max(60,int(os.getenv('AUTO_FB_EDITOR_TIMEOUT','210') or 210)),
            max_tokens=max(700,min(1600,500+target*90)),allow_model_fallback=not bool(model.strip())
        )
        raw=out.get('selected') if isinstance(out,dict) else []
        ids=[]
        valid={str(x.get('candidate_id') or '') for x in candidates}
        for x in raw if isinstance(raw,list) else []:
            cid=str((x or {}).get('id') if isinstance(x,dict) else x)
            if cid and cid in valid and cid not in ids: ids.append(cid)
        return ids

    async def _select_campaign_candidates_resilient(self, campaign_id: str, batch_id: str, candidates: list[dict[str, Any]], product_ids: list[str], *, target: int, model: str) -> list[dict[str, Any]]:
        chunk_size=max(8,min(10,int(os.getenv('AUTO_FB_EDITOR_CHUNK_SIZE','10') or 10)))
        retries=max(1,min(2,int(os.getenv('AUTO_FB_CANDIDATE_CHUNK_RETRIES','2') or 2)))
        # Hierarchical editor: 100 -> shortlists per 20 -> final top 10. Avoid one giant 9Router request.
        if len(candidates) <= chunk_size:
            ids=[]
            last=None
            for attempt in range(1,retries+1):
                try:
                    ids=await asyncio.to_thread(self._select_campaign_candidates_once,candidates,target=target,model=model)
                    if ids: break
                    raise RuntimeError('Editor không trả selected id')
                except Exception as exc:
                    last=exc
                    await self._auto_log(campaign_id,batch_id,'editor_selection',f'Editor retry {attempt}/{retries} · {exc}',level='warning',current=0,total=target)
                    if attempt<retries: await asyncio.sleep(min(8,2*attempt))
            if not ids: raise RuntimeError(f'Editor 9Router lỗi sau {retries} lần: {last}')
            return self._selector_balance(candidates,ids,product_ids,target)

        shortlist=[]
        chunks=[candidates[i:i+chunk_size] for i in range(0,len(candidates),chunk_size)]
        per_chunk=max(3,min(5,math.ceil(target*2/len(chunks))))
        for ci,group in enumerate(chunks,1):
            ids=[]; last=None
            for attempt in range(1,retries+1):
                try:
                    ids=await asyncio.to_thread(self._select_campaign_candidates_once,group,target=min(per_chunk,len(group)),model=model)
                    if ids: break
                    raise RuntimeError('Editor shortlist rỗng')
                except Exception as exc:
                    last=exc
                    await self._auto_log(campaign_id,batch_id,'editor_selection',f'Shortlist {ci}/{len(chunks)} retry {attempt}/{retries} · {exc}',level='warning',current=ci-1,total=len(chunks)+1)
                    if attempt<retries: await asyncio.sleep(min(8,2*attempt))
            if not ids:
                raise RuntimeError(f'Editor shortlist {ci}/{len(chunks)} lỗi sau {retries} lần: {last}')
            by={str(x.get('candidate_id')):x for x in group}
            shortlist.extend([by[x] for x in ids if x in by])
            await self._auto_log(campaign_id,batch_id,'editor_selection',f'Shortlist {ci}/{len(chunks)} OK · giữ {len(ids)} · tổng shortlist {len(shortlist)}',current=ci,total=len(chunks)+1)

        # Deduplicate shortlist then final editor.
        uniq=[]; seen=set()
        for row in shortlist:
            cid=str(row.get('candidate_id') or '')
            if cid and cid not in seen: uniq.append(row); seen.add(cid)
        ids=[]; last=None
        final_target=min(target,len(uniq))
        for attempt in range(1,retries+1):
            try:
                ids=await asyncio.to_thread(self._select_campaign_candidates_once,uniq,target=final_target,model=model)
                if ids: break
                raise RuntimeError('Final editor rỗng')
            except Exception as exc:
                last=exc
                await self._auto_log(campaign_id,batch_id,'editor_selection',f'Final editor retry {attempt}/{retries} · {exc}',level='warning',current=len(chunks),total=len(chunks)+1)
                if attempt<retries: await asyncio.sleep(min(8,2*attempt))
        if not ids: raise RuntimeError(f'Final editor lỗi sau {retries} lần: {last}')
        await self._auto_log(campaign_id,batch_id,'editor_selection',f'Final editor OK · shortlist {len(uniq)} → top {len(ids)}',current=len(chunks)+1,total=len(chunks)+1)
        return self._selector_balance(candidates,ids,product_ids,target)

    def _campaign_plan_quality(self, plan: dict[str, Any], output_duration: str) -> tuple[bool,list[str]]:
        errors=[]; scenes=plan.get('scenes') if isinstance(plan.get('scenes'),list) else []
        dialogue=[]
        for s in scenes:
            dialogue.extend(self._dialogue_turns(s if isinstance(s,dict) else {}))
            action=str((s or {}).get('action') or '').lower() if isinstance(s,dict) else ''
            if any(x in action for x in ('lùi lại','nhìn một chút','xem thử','đi lại một chút')):
                errors.append('vague_flow_action')
        profile=self._product_output_profile(output_duration)
        wc=self._dialogue_word_count(dialogue); turns=len(dialogue)
        if turns < int(profile['turn_min']) or turns > int(profile['turn_max']): errors.append('turn_budget')
        if wc < int(profile['story_words_min']) or wc > int(profile['story_words_max'])+12: errors.append('word_budget')
        errors.extend(self._script_quality_flags(dialogue))
        if not bool(plan.get('ai_used')): errors.append('not_ai_pass')
        ad=str((plan.get('product_ad') or {}).get('text') or '')
        adwc=self._vi_word_count(ad)
        if adwc < int(profile['ad_words_min']) or adwc > int(profile['ad_words_max'])+5: errors.append('ad_budget')
        if len(scenes) != int(profile['veo_clips']): errors.append('clip_count')
        return (len(errors)==0,errors)

    async def _campaign_inspect_product(self, url: str, model: str) -> dict[str, Any]:
        cached=self.get_product_by_url(url)
        if cached and cached.get('image_ready') and str(cached.get('description') or '').strip():
            return cached
        if self.inspect_product_url is None:
            if cached: return cached
            raise RuntimeError('Shopee inspector chưa sẵn sàng')
        capture=await self.inspect_product_url(url)
        return await asyncio.to_thread(self._save_product_capture,url,capture,model)

    def _campaign_random_topics(self, count: int, campaign_id: str, batch_no: int, pillar: str="mixed") -> list[str]:
        library = [
            "Bé không chịu đánh răng trước khi đi ngủ",
            "Bé ăn vạ khi mẹ tắt điện thoại",
            "Bé không muốn tự cất đồ chơi sau khi chơi xong",
            "Bé sợ bóng tối và cứ gọi mẹ giữa đêm",
            "Bé không chịu mặc quần áo mẹ đã chuẩn bị",
            "Bé giành đồ chơi với bạn rồi khóc",
            "Bé nói con không thích mẹ khi bị nhắc nhở",
            "Bé không muốn đi học vào buổi sáng",
            "Bé cứ đòi mẹ bế dù đã lớn hơn",
            "Bé không chịu ngồi ăn và chạy quanh bàn",
            "Bé làm đổ nước rồi sợ mẹ mắng",
            "Bé không muốn chào người lớn khi gặp khách",
            "Bé nổi nóng khi xếp hình mãi không được",
            "Bé muốn mua đồ chơi nhưng mẹ nói không",
            "Bé khóc khi mẹ rời khỏi phòng vài phút",
            "Bé không chịu chia sẻ bánh với anh chị em",
            "Bé nói dối vì sợ bị phạt",
            "Bé không muốn tự đi vệ sinh vào ban đêm",
            "Bé xem hoạt hình quá giờ và không chịu tắt",
            "Bé quăng đồ khi không được làm theo ý mình",
            "Bé không chịu thử món ăn mới",
            "Bé sợ cắt tóc dù tóc đã dài",
            "Bé ngại xin lỗi sau khi làm bạn buồn",
            "Bé cứ chen ngang khi mẹ đang nói chuyện",
            "Bé thức dậy quá sớm và đòi mẹ chơi cùng",
            "Bé không muốn rời khu vui chơi để về nhà",
            "Bé làm bài tập chậm vì cứ mất tập trung",
            "Bé ngại ngủ trưa dù buổi chiều rất mệt",
            "Bé muốn tự làm mọi thứ nhưng dễ cáu khi chưa làm được",
            "Bé hay so sánh đồ của mình với bạn khác",
            "Bé không muốn tắm vì đang chơi dở",
            "Bé hỏi vì sao mẹ phải đi làm mỗi ngày",
            "Bé sợ bác sĩ và không chịu vào phòng khám",
            "Bé cứ đòi ngủ cùng mẹ sau một cơn ác mộng",
            "Bé không chịu dọn bàn sau khi ăn",
            "Bé thích một món đồ nhưng em bé cũng đang cầm",
            "Bé nói con chán vì mẹ không cho xem màn hình",
            "Bé khó chịu khi kế hoạch cuối tuần bị thay đổi",
            "Bé không muốn tự mang giày dù đã biết làm",
            "Bé buồn vì không được chọn làm người đầu tiên",
        ]
        teach_more = [
            "Mẹ dạy con tự chuẩn bị quần áo cho ngày mai",
            "Mẹ dạy con tự xếp sách lên kệ sau khi đọc",
            "Mẹ dạy con chờ đến lượt khi chơi cùng bạn",
            "Mẹ dạy con nói cảm ơn khi được giúp đỡ",
            "Mẹ dạy con xin lỗi và sửa lỗi sau khi làm bạn buồn",
            "Mẹ dạy con tự rót nước mà không làm đổ",
            "Mẹ dạy con phân loại đồ chơi trước khi cất",
            "Mẹ dạy con tự đánh răng đủ thời gian",
            "Mẹ dạy con chuẩn bị ba lô trước khi đi học",
            "Mẹ dạy con tự mang giày và cất giày đúng chỗ",
            "Mẹ dạy con nhận biết khi mình đang tức giận",
            "Mẹ dạy con hít thở chậm khi sắp khóc vì bực",
            "Mẹ dạy con không chen ngang khi người khác đang nói",
            "Mẹ dạy con chia tiền lì xì thành tiêu dùng và tiết kiệm",
            "Mẹ dạy con biết từ chối người lạ một cách an toàn",
            "Mẹ dạy con nhớ số điện thoại của bố mẹ",
            "Mẹ dạy con tự dọn chỗ ngồi sau bữa ăn",
            "Mẹ dạy con chọn một việc nhỏ để giúp mẹ mỗi ngày",
            "Mẹ dạy con đọc sách 10 phút trước khi ngủ",
            "Mẹ dạy con tự chọn hai món đồ chơi mang theo thay vì ôm hết",
            "Mẹ dạy con nói rõ điều mình muốn thay vì ăn vạ",
            "Mẹ dạy con biết dừng màn hình khi hết giờ",
            "Mẹ dạy con tự chuẩn bị góc học tập gọn gàng",
            "Mẹ dạy con không so sánh đồ của mình với bạn",
            "Mẹ dạy con làm việc khó từng bước thay vì bỏ cuộc",
            "Mẹ dạy con tự kiểm tra bình nước trước khi ra ngoài",
            "Mẹ dạy con giữ lời hứa nhỏ trong ngày",
            "Mẹ dạy con cách nhờ người lớn giúp khi bị lạc",
            "Mẹ dạy con biết chia sẻ nhưng vẫn giữ đồ cá nhân cần thiết",
            "Mẹ dạy con đi ngủ đúng giờ mà không cần nhắc nhiều lần",
        ]
        if pillar == "mother_teaches":
            library = teach_more
        elif pillar == "mixed":
            library = library + teach_more
        count=max(0,int(count or 0))
        if not count:
            return []
        recent=set()
        try:
            with self._conn() as c:
                rows=c.execute("SELECT candidate_json FROM parenting_auto_items WHERE campaign_id=? AND product_id='__random_parenting__' ORDER BY created_at DESC LIMIT 80",(campaign_id,)).fetchall()
            for r in rows:
                d=self._loads(r['candidate_json'],{})
                t=str((d or {}).get('topic') or '').strip().lower()
                if t: recent.add(t)
        except Exception:
            pass
        fresh=[x for x in library if x.lower() not in recent]
        rng=random.Random(f"{campaign_id}:{batch_no}:{datetime.now().date().isoformat()}")
        rng.shuffle(fresh)
        fallback=list(library); rng.shuffle(fallback)
        out=[]
        for x in fresh+fallback:
            if x not in out:
                out.append(x)
            if len(out)>=count: break
        while len(out)<count:
            base=rng.choice(library)
            out.append(f"{base} — tình huống khác lần {len(out)+1}")
        return out[:count]

    async def _prepare_campaign_batch(self, campaign_id: str, resume_batch_id: str | None = None) -> None:
        campaign=self._campaign_row(campaign_id)
        if not campaign or campaign.get('status') in {'paused','stopped'}:
            return

        resume_batch=self._batch_row(str(resume_batch_id)) if resume_batch_id else None
        if resume_batch and str(resume_batch.get('campaign_id') or '') != str(campaign_id):
            resume_batch=None
        all_links=self._clean_shopee_urls(list((resume_batch or {}).get('source_links') or campaign.get('source_links') or []))
        shopee_cfg=campaign.get('shopee_config') or {}
        target=max(1,min(30,int(campaign.get('selected_per_batch') or 10)))
        batch_no=int((resume_batch or {}).get('batch_no') or (int(campaign.get('batch_no') or 0)+1))

        # V4.1: when restarting a PREPARING/CANDIDATE/EDITOR batch, reuse the exact
        # persisted batch/candidates instead of silently creating a fresh pool.
        # V3.1: optional Shopee browser search, no Affiliate app_id/secret required.
        # Search only uses the connected browser worker; each found product later gets exactly one 9Router request for 5 candidates.
        if not resume_batch and bool(shopee_cfg.get('auto_search')) and self.search_products is not None:
            raw_topics=[str(x).strip() for x in (shopee_cfg.get('search_topics') or []) if str(x).strip()]
            want=max(0,min(10,int(shopee_cfg.get('search_count') or 5)))
            topics=raw_topics[:want] if raw_topics else self._campaign_product_search_topics(str(shopee_cfg.get('content_pillar') or 'mixed'),want,campaign_id,batch_no)
            for kw in topics:
                if len(all_links)>=10: break
                try:
                    rows=await self.search_products(kw,6)
                    chosen=None
                    for row in (rows or []):
                        u=str((row or {}).get('url') or (row or {}).get('productUrl') or '').strip()
                        try: u=self._validate_shopee_url(u)
                        except Exception: continue
                        if u not in all_links:
                            chosen=u; break
                    if chosen:
                        all_links.append(chosen)
                        await self._auto_log(campaign_id,None,'shopee_search',f'Tìm SP: {kw} → {chosen[:100]}',current=len(all_links),total=min(10,len(topics)))
                    else:
                        await self._auto_log(campaign_id,None,'shopee_search',f'Tìm SP: {kw} → không có kết quả hợp lệ',level='warning')
                except Exception as exc:
                    await self._auto_log(campaign_id,None,'shopee_search',f'Tìm SP lỗi: {kw} · {exc}',level='warning')

        # Up to 10 products per batch = at most 10 product-candidate requests.
        links=list(all_links[:10])
        now=self._now()
        if resume_batch:
            bid=str(resume_batch['id'])
            with self._conn() as c:
                c.execute("UPDATE parenting_auto_batches SET status='preparing',error=NULL,phase_message=?,progress_updated_at=?,updated_at=? WHERE id=?",('Resume persisted batch; giữ candidate/script/artifact cũ',now,now,bid))
                c.execute("UPDATE parenting_auto_campaigns SET status='preparing',current_batch_id=?,last_error=NULL,updated_at=? WHERE id=?",(bid,now,campaign_id))
            await self._auto_log(campaign_id,bid,'resume_prepare',f'Resume batch #{batch_no} tại phase {resume_batch.get("phase") or resume_batch.get("status")} · giữ {len(resume_batch.get("candidates") or [])} candidate + {len(resume_batch.get("selected") or [])} selected.',level='warning',current=int(resume_batch.get('progress_current') or 0),total=target)
        else:
            bid=f"autobatch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            with self._conn() as c:
                c.execute(
                    "INSERT INTO parenting_auto_batches(id,campaign_id,batch_no,source_links_json,status,ai_model,phase,phase_message,progress_current,progress_total,progress_updated_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (bid,campaign_id,batch_no,self._dumps(all_links),'preparing',campaign.get('script_model') or '','starting','Khởi tạo batch',0,target,now,now,now),
                )
                c.execute("UPDATE parenting_auto_campaigns SET status='preparing',current_batch_id=?,batch_no=?,last_error=NULL,updated_at=? WHERE id=?",(bid,batch_no,now,campaign_id))
            await self.ui_broadcast({'type':'AUTO_FB_BATCH_PREPARING','campaignId':campaign_id,'batchId':bid,'batchNo':batch_no,'links':len(all_links)})
            mode_msg=(f'{len(links)} SP đang dùng trong batch' if links else 'RANDOM ONLY · không cần Shopee')
            await self._auto_log(campaign_id,bid,'starting',f'Batch #{batch_no} START · mục tiêu {target} video · {mode_msg}',current=0,total=target)

        try:
            products=[]; inspect_errors=[]
            if resume_batch and (resume_batch.get('product_ids') or []):
                for pid in (resume_batch.get('product_ids') or []):
                    prod=self.get_product(str(pid))
                    if prod: products.append(prod)
                if products:
                    await self._auto_log(campaign_id,bid,'resume_prepare',f'REUSE PRODUCT SNAPSHOT · {len(products)} sản phẩm đã đọc trước restart; không đọc lại Shopee.',level='warning',current=len(products),total=len(products))
            if links and not products:
                for idx,u in enumerate(links,1):
                    await self._auto_log(campaign_id,bid,'reading_products',f'Đọc sản phẩm {idx}/{len(links)} · {u[:90]}',current=idx-1,total=len(links))
                    last_exc=None; product=None
                    for attempt in range(1,5):
                        try:
                            product=await self._campaign_inspect_product(self._validate_shopee_url(u),str(campaign.get('script_model') or ''))
                            break
                        except Exception as exc:
                            last_exc=exc; msg=str(exc)
                            if 'Flow Extension' in msg or '8787' in msg or 'nối ws' in msg:
                                await self._auto_log(campaign_id,bid,'waiting_extension',f'Chờ Flow Extension tự kết nối · lần {attempt}/4 · {msg}',level='warning',current=idx-1,total=len(links))
                                await asyncio.sleep(5)
                                continue
                            break
                    if product:
                        products.append(product)
                        with self._conn() as c:
                            c.execute("UPDATE parenting_auto_batches SET product_ids_json=?,updated_at=? WHERE id=?",(self._dumps([str(x['id']) for x in products]),self._now(),bid))
                        await self._auto_log(campaign_id,bid,'reading_products',f'OK sản phẩm {idx}/{len(links)} · {str(product.get("title") or product.get("id"))[:120]}',current=idx,total=len(links))
                    else:
                        inspect_errors.append(f"{u[:80]}: {last_exc}")
                        await self._auto_log(campaign_id,bid,'reading_products',f'LỖI sản phẩm {idx}/{len(links)} · bỏ qua SP này, slot sẽ chuyển thành random · {last_exc}',level='warning',current=idx,total=len(links))
            elif not links:
                await self._auto_log(campaign_id,bid,'random_fill','Không có link Shopee · bỏ qua đọc SP/candidate/editor · fill toàn bộ batch bằng parenting random',current=0,total=target)

            product_ids=[str(x['id']) for x in products]
            candidates=list((resume_batch or {}).get('candidates') or [])
            selected=list((resume_batch or {}).get('selected') or [])
            product_slot_target=min(target,len(products)*2)
            if candidates:
                await self._auto_log(campaign_id,bid,'resume_prepare',f'REUSE CANDIDATE POOL · {len(candidates)} candidate persisted; không clear khi restart.',level='warning',current=len(candidates),total=max(len(candidates),len(products)*5))
            if selected:
                await self._auto_log(campaign_id,bid,'resume_prepare',f'REUSE EDITOR SELECTED · {len(selected)} candidate đã chọn; bỏ qua editor nếu đủ dữ liệu.',level='warning',current=len(selected),total=max(1,product_slot_target))

            # Each product only gets five compact candidates. This replaces the old 100-candidate pool.
            if products and not selected:
                if not self.router9_enabled():
                    await self._auto_log(campaign_id,bid,'candidate_generation','9Router chưa sẵn sàng · bỏ phần video sản phẩm và chuyển toàn bộ slot sang random parenting',level='warning',current=0,total=target)
                    products=[]; product_ids=[]; product_slot_target=0
                else:
                    per_product=5
                    pool=len(products)*per_product
                    await self._auto_log(campaign_id,bid,'candidate_generation',f'1 SP = 1 request = 5 candidate · {len(products)} SP = {len(products)} request tối đa',current=0,total=pool)
                    for pi,product in enumerate(products):
                        existing_for_product=[x for x in candidates if str((x or {}).get('product_id') or '')==str(product.get('id') or '')]
                        if len(existing_for_product)>=5:
                            await self._auto_log(campaign_id,bid,'candidate_generation',f'SP {pi+1}/{len(products)} · đã có {len(existing_for_product)}/5 candidate persisted → SKIP request 9Router',level='warning',current=len(candidates),total=pool)
                            continue
                        missing_count=max(1,5-len(existing_for_product))
                        await self._auto_log(campaign_id,bid,'candidate_generation',f'SP {pi+1}/{len(products)} · resume pool · xin phần thiếu {missing_count} candidate (đã giữ {len(existing_for_product)})',current=len(candidates),total=pool)
                        try:
                            rows=await asyncio.to_thread(self._campaign_candidate_chunk,product,count=missing_count,model=str(campaign.get('script_model') or ''),seed=f"{bid}_{pi}_{random.random()}")
                            got=[]; seen={(str(x.get('hook')).lower(),str(x.get('angle')).lower()) for x in existing_for_product}
                            for row in rows or []:
                                key=(str(row.get('hook')).lower(),str(row.get('angle')).lower())
                                if key in seen: continue
                                seen.add(key); got.append(row)
                                if len(got)>=missing_count: break
                            candidates.extend(got)
                            with self._conn() as c:
                                c.execute("UPDATE parenting_auto_batches SET candidates_json=?,updated_at=? WHERE id=?",(self._dumps(candidates),self._now(),bid))
                            await self._auto_log(campaign_id,bid,'candidate_generation',f'SP {pi+1}: nhận {len(got)}/5 · tổng pool {len(candidates)}/{pool}',current=len(candidates),total=pool)
                        except Exception as exc:
                            await self._auto_log(campaign_id,bid,'candidate_generation',f'SP {pi+1}: request 5 candidate lỗi · bỏ SP này, slot chuyển random · {exc}',level='warning',current=len(candidates),total=pool)

                    if candidates and product_slot_target>0 and not selected:
                        await self._auto_log(campaign_id,bid,'editor_selection',f'Editor chọn tối đa {product_slot_target} video SP từ {len(candidates)} candidate · ưu tiên 1–2/SP',current=0,total=product_slot_target)
                        selected=await self._select_campaign_candidates_resilient(campaign_id,bid,candidates,product_ids,target=min(product_slot_target,len(candidates)),model=str(campaign.get('script_model') or ''))
                        with self._conn() as c:
                            c.execute("UPDATE parenting_auto_batches SET product_ids_json=?,candidates_json=?,selected_json=?,status='selected',updated_at=? WHERE id=?",(self._dumps(product_ids),self._dumps(candidates),self._dumps(selected),self._now(),bid))
                        await self._auto_log(campaign_id,bid,'editor_selection',f'Editor product xong · chọn {len(selected)} candidate; bắt đầu full script',current=len(selected),total=product_slot_target)

            queued=0; rank_no=0
            generation_window=self._auto_generation_window(campaign)
            music_cfg=dict(campaign.get('music_config') or {})

            # Queue ONLY editor winners. V3.3 safety rule: never walk the whole 5-candidate
            # pool trying to force a product video through the quality gate. That old behavior
            # could spend many minutes repeating "Product full script 1/2" when AI returned
            # fallback/not_ai_pass. Each selected winner gets ONE full-script attempt; if it
            # fails, the slot is immediately handed to random parenting content.
            if selected:
                candidate_queue=list(selected)[:max(0,product_slot_target)]
                used=set(); product_counts={pid:0 for pid in product_ids}; examined=0
                selected_total=len(candidate_queue)
                for cand in candidate_queue:
                    if queued>=product_slot_target: break
                    cid=str(cand.get('candidate_id') or '')
                    if not cid or cid in used: continue
                    used.add(cid); examined+=1
                    pid=str(cand.get('product_id') or '')
                    if product_counts.get(pid,0)>=2: continue
                    product=self.get_product(pid)
                    if not product: continue
                    await self._auto_log(campaign_id,bid,'quality_gate',f'Product full script candidate {examined}/{selected_total} · video SP đã pass {queued}/{product_slot_target} · {str(product.get("title") or "")[:90]}',current=examined-1,total=max(1,selected_total))
                    spine=(f"9Router candidate đã được editor chọn. HOOK={cand.get('hook')}; ANGLE={cand.get('angle')}; SETUP={cand.get('setup')}; REVEAL={cand.get('reveal')}; PAYOFF={cand.get('payoff')}; RESOLUTION={cand.get('resolution')}; AD={cand.get('ad_angle')}; PREVIEW={self._dumps(cand.get('dialogue_preview') or [])}. Giữ logic này nhưng viết hội thoại Việt Nam tự nhiên, đủ đầu-cuối. Action phải là hành động vật lý cụ thể cho Veo.")[:500]
                    preq=ProductPlanRequest(product_id=product['id'],character_set_id=campaign['character_set_id'],story_scene_count=0,total_dialogue_turns=0,output_duration=campaign['output_duration'],angle_hint=spine,model=str(campaign.get('script_model') or ''),product_reveal_scene=1,variation_seed=f"{bid}_{cid}_{random.random()}")
                    plan=await asyncio.to_thread(self.generate_product_plan,preq)
                    ok,errs=self._campaign_plan_quality(plan,campaign['output_duration'])
                    if not ok:
                        await self._auto_log(campaign_id,bid,'quality_gate',f'BỎ candidate {examined}/{selected_total} · {", ".join(errs[:6])} · slot chuyển parenting random ngay, không retry vòng lặp',level='warning',current=examined,total=max(1,selected_total))
                        continue
                    good_plan=plan
                    greq=ProductGenerateRequest(product_id=product['id'],character_set_id=campaign['character_set_id'],story_scene_count=0,total_dialogue_turns=0,output_duration=campaign['output_duration'],angle_hint=spine,model=str(campaign.get('script_model') or ''),product_reveal_scene=1,image_model=campaign['image_model'],video_model=campaign['video_model'],video_duration='8s',burn_subtitles=bool(campaign['burn_subtitles']),auto_publish=False,facebook_page_id=None,facebook_dry_run=True)
                    good_plan['music_config']=music_cfg
                    plan,scenes,flow=self.build_product_story(greq,prebuilt_plan=good_plan)
                    plan['music_config']=music_cfg
                    aff_id=str((campaign.get('shopee_config') or {}).get('affiliate_id') or '').strip()
                    origin=str(product.get('final_url') or product.get('source_url') or '')
                    if aff_id and origin:
                        plan['affiliate_url']=self._make_shopee_affiliate_link(origin,aff_id,[str(campaign.get('page_profile_id') or 'fb'),f'b{batch_no}',f'v{queued+1}',pid[-12:]])
                        plan['affiliate_enabled']=True
                    else:
                        plan['affiliate_url']=origin
                        plan['affiliate_enabled']=False
                    jid=self.create_flow_job('parenting_story',scenes,flow)
                    rid=self._save_product_run(greq,plan,jid)
                    iid=f"autoitem_{uuid.uuid4().hex[:12]}"; rank_no+=1; queued+=1; product_counts[pid]=product_counts.get(pid,0)+1; now2=self._now()
                    item_status='generating' if rank_no<=generation_window else 'planned'
                    if item_status=='planned':
                        self.update_flow_job(jid,status='held',error=None)
                    with self._conn() as c:
                        c.execute("INSERT INTO parenting_auto_items(id,campaign_id,batch_id,rank_no,product_id,candidate_json,plan_json,run_id,flow_job_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(iid,campaign_id,bid,rank_no,product['id'],self._dumps(cand),self._dumps(plan),rid,jid,item_status,now2,now2))
                        c.execute("UPDATE parenting_story_runs SET campaign_id=?,campaign_batch_id=?,campaign_item_id=?,status=?,auto_publish=0,facebook_page_id=NULL,updated_at=? WHERE id=?",(campaign_id,bid,iid,('generating' if item_status=='generating' else 'planned'),now2,rid))
                    await self.ui_broadcast({'type':'AUTO_FB_VIDEO_QUEUED','campaignId':campaign_id,'batchId':bid,'itemId':iid,'jobId':jid,'rank':rank_no,'productId':product['id'],'held':item_status=='planned'})
                    if item_status=='generating':
                        await self._auto_log(campaign_id,bid,'queue_flow',f'Video SP {queued}/{target} → Flow ACTIVE · job {jid}',current=queued,total=target)
                        await self.dispatch_jobs()
                    else:
                        await self._auto_log(campaign_id,bid,'queue_flow',f'Video SP {queued}/{target} → PLANNED/HOLD · chờ 1 video trước được đăng rồi mới chạy',current=queued,total=target)

            # Fill every remaining slot with normal parenting content. No Shopee link is required.
            random_needed=max(0,target-queued)
            random_topics=self._campaign_random_topics(random_needed,campaign_id,batch_no,str((campaign.get("shopee_config") or {}).get("content_pillar") or "mixed"))
            if random_needed:
                await self._auto_log(campaign_id,bid,'random_fill',f'Fill {random_needed} slot còn lại bằng parenting random · không cần Shopee',current=0,total=random_needed)
            profile=self._product_output_profile(str(campaign.get('output_duration') or '32s'))
            random_scene_count=max(1,int(profile['veo_clips']))
            random_turns_per_scene=2
            for ri,topic in enumerate(random_topics,1):
                await self._auto_log(campaign_id,bid,'random_fill',f'Random {ri}/{random_needed} · {topic}',current=ri-1,total=random_needed)
                sreq=StoryGenerateRequest(
                    character_set_id=campaign['character_set_id'],topic=topic,scene_count=random_scene_count,
                    dialogue_turns_per_scene=random_turns_per_scene,model=str(campaign.get('script_model') or ''),
                    image_model=campaign['image_model'],video_model=campaign['video_model'],video_duration='8s',
                    burn_subtitles=bool(campaign['burn_subtitles']),auto_publish=False,facebook_page_id=None,
                    facebook_dry_run=True,continuation_mode='off',
                )
                plan,scenes,flow=self.build_story(sreq)
                plan['music_config']=music_cfg
                plan['auto_fb_source']='random_parenting'
                jid=self.create_flow_job('parenting_story',scenes,flow)
                rid=self._save_run(sreq,plan,jid)
                iid=f"autoitem_{uuid.uuid4().hex[:12]}"; rank_no+=1; queued+=1; now2=self._now()
                cand={'candidate_id':f'random_{uuid.uuid4().hex[:10]}','type':'random_parenting','topic':topic,'hook':plan.get('hook') or topic,'angle':'random parenting'}
                item_status='generating' if rank_no<=generation_window else 'planned'
                if item_status=='planned':
                    self.update_flow_job(jid,status='held',error=None)
                with self._conn() as c:
                    c.execute("INSERT INTO parenting_auto_items(id,campaign_id,batch_id,rank_no,product_id,candidate_json,plan_json,run_id,flow_job_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(iid,campaign_id,bid,rank_no,'__random_parenting__',self._dumps(cand),self._dumps(plan),rid,jid,item_status,now2,now2))
                    c.execute("UPDATE parenting_story_runs SET campaign_id=?,campaign_batch_id=?,campaign_item_id=?,status=?,auto_publish=0,facebook_page_id=NULL,updated_at=? WHERE id=?",(campaign_id,bid,iid,('generating' if item_status=='generating' else 'planned'),now2,rid))
                await self.ui_broadcast({'type':'AUTO_FB_VIDEO_QUEUED','campaignId':campaign_id,'batchId':bid,'itemId':iid,'jobId':jid,'rank':rank_no,'productId':None,'randomTopic':topic,'held':item_status=='planned'})
                if item_status=='generating':
                    await self._auto_log(campaign_id,bid,'queue_flow',f'Random video {queued}/{target} → Flow ACTIVE · job {jid}',current=queued,total=target)
                    await self.dispatch_jobs()
                else:
                    await self._auto_log(campaign_id,bid,'queue_flow',f'Random video {queued}/{target} → PLANNED/HOLD · chờ slot rolling',current=queued,total=target)

            if queued<target:
                raise RuntimeError(f'Chỉ queue được {queued}/{target} video')

            with self._conn() as c:
                c.execute("UPDATE parenting_auto_batches SET status='generating',phase='flow_generation',phase_message=?,progress_current=?,progress_total=?,progress_updated_at=?,updated_at=? WHERE id=?",(f'Đã chuẩn bị {queued}/{target}; chỉ chạy rolling tối đa {generation_window} video cùng lúc',min(generation_window,queued),target,self._now(),self._now(),bid))
                c.execute("UPDATE parenting_auto_campaigns SET status='running',last_error=?,updated_at=? WHERE id=?",((' | '.join(inspect_errors[:2]) if inspect_errors else None),self._now(),campaign_id))
            await self.dispatch_jobs()
            await self._auto_log(campaign_id,bid,'flow_generation',f'Batch có {queued}/{target} script/video plan nhưng chỉ ACTIVE {min(generation_window,queued)}; phần còn lại HOLD. SP {sum(1 for x in self._item_rows(bid) if x.get("product_id")!="__random_parenting__")} + random {sum(1 for x in self._item_rows(bid) if x.get("product_id")=="__random_parenting__")}',current=min(generation_window,queued),total=target)
            await self.ui_broadcast({'type':'AUTO_FB_BATCH_QUEUED','campaignId':campaign_id,'batchId':bid,'candidates':len(candidates),'selected':queued})
        except Exception as exc:
            with self._conn() as c:
                c.execute("UPDATE parenting_auto_batches SET status='error',phase='error',phase_message=?,error=?,progress_updated_at=?,updated_at=? WHERE id=?",(str(exc),str(exc),self._now(),self._now(),bid))
                c.execute("UPDATE parenting_auto_campaigns SET status='error',last_error=?,updated_at=? WHERE id=?",(str(exc),self._now(),campaign_id))
            await self._auto_log(campaign_id,bid,'error',str(exc),level='error')
            await self.ui_broadcast({'type':'AUTO_FB_BATCH_ERROR','campaignId':campaign_id,'batchId':bid,'error':str(exc)})

    async def start_campaign(self, req: AutoFbCampaignStartRequest) -> dict[str, Any]:
        self._require_ready_set(req.character_set_id)
        links=self._clean_shopee_urls(req.shopee_urls)
        for u in links: self._validate_shopee_url(u)
        page=self.get_auto_page_profile(req.page_profile_id)
        if not page or not page.get('enabled'): raise HTTPException(400,'Page X chưa được lưu/đang tắt')
        profile=self._product_output_profile(req.output_duration)
        cid=str(req.campaign_id or '').strip() or f"autofb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        now=self._now(); existing=self._campaign_row(cid)
        current_batch=(existing or {}).get('current_batch_id')
        resume_existing_after_save=False
        if current_batch and cid not in self._campaign_prepare_running:
            old_batch=self._batch_row(str(current_batch))
            if old_batch:
                bst=str(old_batch.get('status') or '').strip().lower()
                phase=str(old_batch.get('phase') or '').strip().lower()
                items=self._item_rows(str(current_batch))
                age=0
                try:
                    last=old_batch.get('progress_updated_at') or old_batch.get('updated_at')
                    dt=datetime.fromisoformat(str(last).replace('Z','+00:00')) if last else None
                    age=max(0,int((datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())) if dt else 999999
                except Exception:
                    age=999999
                orphan = bst in {'queued','preparing','selected','error','abandoned'}
                orphan = orphan or (not items and age >= 60 and phase in {'','queued','starting','reading_products','candidate_generation','editor_selection','quality_gate','random_fill','queue_flow'})
                orphan = orphan or (bst=='generating' and not items and age >= 180)
                if orphan:
                    reason=f'WATCHDOG START: batch cũ {bst or "?"}/{phase or "?"}, task IDLE, age={age}s, items={len(items)} → RESUME SAME BATCH; giữ candidate/script/artifact.'
                    with self._conn() as c:
                        c.execute("UPDATE parenting_auto_batches SET status='preparing',phase='resume_prepare',phase_message=?,error=NULL,progress_updated_at=?,updated_at=? WHERE id=?",(reason,now,now,current_batch)); c.execute("UPDATE parenting_auto_campaigns SET current_batch_id=?,status='preparing',updated_at=? WHERE id=?",(current_batch,now,cid))
                    await self._auto_log(cid,str(current_batch),'watchdog_recovery',reason,level='warning')
                    resume_existing_after_save=True
        status='preparing' if resume_existing_after_save else ('running' if current_batch else 'preparing')
        music_cfg={"enabled":bool(req.music_enabled),"provider":req.music_provider,"style":req.music_style.strip() or "dynamic electronic playful upbeat","intensity":req.music_intensity,"volume":float(req.music_volume),"ducking":bool(req.music_ducking),"required":False}
        search_topics=[]
        for x in req.shopee_search_topics:
            t=re.sub(r'\s+',' ',str(x or '')).strip()[:100]
            if t and t not in search_topics: search_topics.append(t)
        affiliate_id=re.sub(r'[^A-Za-z0-9_-]','',str(req.shopee_affiliate_id or '').strip())[:80]
        shopee_cfg={"auto_search":bool(req.shopee_auto_search),"search_topics":search_topics[:20],"search_count":int(req.shopee_search_count),"affiliate_id":affiliate_id,"content_pillar":req.content_pillar}
        with self._conn() as c:
            if existing:
                c.execute("UPDATE parenting_auto_campaigns SET name=?,character_set_id=?,source_links_json=?,page_profile_id=?,script_model=?,image_model=?,video_model=?,output_duration=?,candidate_pool_size=?,selected_per_batch=?,burn_subtitles=?,music_config_json=?,shopee_config_json=?,status=?,auto_resume=1,last_error=NULL,updated_at=? WHERE id=?",(req.name.strip() or 'Auto FB Hybrid',req.character_set_id,self._dumps(links),req.page_profile_id,req.script_model.strip(),req.image_model,req.video_model,profile['key'],len(links)*5,int(req.selected_per_batch),int(req.burn_subtitles),self._dumps(music_cfg),self._dumps(shopee_cfg),status,now,cid))
            else:
                c.execute("INSERT INTO parenting_auto_campaigns(id,name,character_set_id,source_links_json,page_profile_id,script_model,image_model,video_model,output_duration,candidate_pool_size,selected_per_batch,burn_subtitles,music_config_json,shopee_config_json,status,auto_resume,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,req.name.strip() or 'Auto FB Hybrid',req.character_set_id,self._dumps(links),req.page_profile_id,req.script_model.strip(),req.image_model,req.video_model,profile['key'],len(links)*5,int(req.selected_per_batch),int(req.burn_subtitles),self._dumps(music_cfg),self._dumps(shopee_cfg),status,1,now,now))
        if resume_existing_after_save and current_batch:
            self._schedule_campaign_prepare(cid,resume_batch_id=str(current_batch))
        elif not current_batch:
            self._schedule_campaign_prepare(cid)
        await self.ui_broadcast({'type':'AUTO_FB_CAMPAIGN_STARTED','campaignId':cid,'links':len(links),'currentBatchPreserved':bool(current_batch)})
        return self.get_campaign(cid) or {'id':cid}

    def _resume_generating_batch_jobs(self, batch_id: str) -> dict[str, int]:
        """Repair persisted Auto FB Flow jobs after server/bridge interruption.

        V3.4 also repairs legacy V3.1–V3.3 rows that were collapsed to the generic
        item error ``Flow failed`` during shutdown. Those legacy failures are retried
        exactly once via ``resume_retry_count`` so a genuine bad Flow job cannot loop
        forever across restarts.
        """
        retry_tokens = (
            'server restart', 'server đang dừng', 'server mất kết nối',
            'flow extension mất kết nối', 'agent interrupted', 'extension service worker restarted',
            'đã dừng theo server', 'server shutdown', 'parenting server',
            'websocket disconnected', 'ws disconnected', 'controlled stop', 'server offline',
            # Flow/UI/network failures are transient for an artifact-checkpoint pipeline.
            # A settings/picker timeout must not destroy 7 clips that already succeeded.
            'timeout', 'network', 'settings', 'cài đặt', 'picker', 'bộ chọn tệp',
            'mediaid', 'không thấy video post', 'không thấy image post', 'debugger',
            'not attached', 'connection reset', 'winerror 10054', 'asset chưa index',
            'không phản hồi', 'không tìm thấy nút'
        )
        legacy_item_errors = {'flow failed', 'agent interrupted', 'flow interrupted'}
        stats={'requeued':0,'legacy_requeued':0,'kept':0,'done':0,'genuine_failed':0}
        now=self._now()
        with self._conn() as c:
            rows=c.execute(
                "SELECT i.id item_id,i.status item_status,i.error item_error,i.flow_job_id,i.final_path,"
                "COALESCE(i.resume_retry_count,0) resume_retry_count,COALESCE(i.checkpoint_recovery_count,0) checkpoint_recovery_count,"
                "i.last_failure_class,r.status run_status,r.error run_error,"
                "f.status flow_status,f.error flow_error,COALESCE(f.retry_count,0) flow_retry_count,COALESCE(f.max_retries,5) flow_max_retries "
                "FROM parenting_auto_items i "
                "LEFT JOIN parenting_story_runs r ON r.flow_job_id=i.flow_job_id "
                "LEFT JOIN flow_jobs f ON f.id=i.flow_job_id WHERE i.batch_id=? ORDER BY i.rank_no",
                (batch_id,),
            ).fetchall()
            for row in rows:
                d=dict(row); jid=str(d.get('flow_job_id') or '')
                if not jid:
                    stats['kept']+=1; continue
                final=str(d.get('final_path') or '')
                # Artifact truth beats stale item status/path after crash. Recover a final
                # from story_runs/assets if the item row missed the last DB update.
                if not (final and Path(final).exists()):
                    rr=c.execute("SELECT final_path FROM parenting_story_runs WHERE flow_job_id=?",(jid,)).fetchone()
                    cand=str((rr['final_path'] if rr else '') or '')
                    if cand and Path(cand).exists():
                        final=cand
                    else:
                        ar=c.execute("SELECT local_path FROM assets WHERE job_id=? AND kind='final_video' ORDER BY created_at DESC LIMIT 1",(jid,)).fetchone()
                        cand=str((ar['local_path'] if ar else '') or '')
                        if cand and Path(cand).exists():
                            final=cand
                    if final:
                        c.execute("UPDATE parenting_auto_items SET final_path=?,updated_at=? WHERE id=?",(final,now,d['item_id']))
                        c.execute("UPDATE parenting_story_runs SET final_path=?,status='done',error=NULL,updated_at=? WHERE flow_job_id=?",(final,now,jid))
                if final and Path(final).exists():
                    item_state=str(d.get('item_status') or '').lower().strip()
                    # Never regress an already-published/publishing item back to READY on restart.
                    # That old behavior could cause the same Reel to be published twice.
                    if item_state not in {'ready','publishing','published'}:
                        c.execute("UPDATE parenting_auto_items SET status='ready',error=NULL,updated_at=? WHERE id=?",(now,d['item_id']))
                    stats['done']+=1; continue
                fs=str(d.get('flow_status') or '').lower().strip()
                ferr=str(d.get('flow_error') or '').lower().strip()
                ierr=str(d.get('item_error') or '').lower().strip()
                rerr=str(d.get('run_error') or '').lower().strip()
                retry_count=int(d.get('resume_retry_count') or 0)
                checkpoint_recovery_count=int(d.get('checkpoint_recovery_count') or 0)
                failure_class=str(d.get('last_failure_class') or '').lower().strip()
                permanent_by_error=(ferr.startswith('permanent:') or ierr.startswith('permanent:') or rerr.startswith('permanent:'))
                if permanent_by_error and failure_class!='flow_permanent':
                    failure_class='flow_permanent'
                    c.execute("UPDATE parenting_auto_items SET last_failure_class='flow_permanent',updated_at=? WHERE id=?",(now,d['item_id']))

                resume_budget=max(1,int(os.getenv('AUTO_FB_RESUME_MAX_RETRIES','8') or 8))
                retryable = failure_class not in {'flow_permanent','render_permanent','publish_permanent'} and fs in {'queued','dispatching','running','interrupted','checkpointing','downloading'} and retry_count < resume_budget
                retryable = retryable or (failure_class not in {'flow_permanent','render_permanent','publish_permanent'} and fs in {'failed','partial_failed','qc_failed'} and retry_count < resume_budget and any(t in (ferr+' '+ierr+' '+rerr) for t in retry_tokens))

                # Legacy bug: on_flow_complete(False) overwrote Auto FB item/run errors with
                # the generic text "Flow failed". If the batch was interrupted around a
                # server restart we no longer have enough provenance in the row. Retry that
                # ambiguous legacy row once, then never again.
                legacy_generic = (
                    not retryable and retry_count < 1 and
                    fs in {'failed','partial_failed','qc_failed',''} and
                    (ierr in legacy_item_errors or rerr in legacy_item_errors)
                )
                if legacy_generic:
                    retryable=True
                    stats['legacy_requeued']+=1

                # V4.1 hard recovery: old V3.x/V4.0 retry counters were inflated by
                # server restarts/requeues, not by 8 genuine scene failures. Recover such
                # rows from scene checkpoints instead of declaring the whole video dead.
                hard_limit=max(1,int(os.getenv('AUTO_FB_CHECKPOINT_RECOVERY_CYCLES','3') or 3))
                hard_recovery=(
                    not retryable and checkpoint_recovery_count < hard_limit and
                    fs in {'failed','partial_failed','qc_failed',''} and
                    failure_class not in {'flow_permanent','render_permanent','publish_permanent'}
                )
                if hard_recovery:
                    retryable=True

                if not retryable:
                    if fs in {'failed','partial_failed','qc_failed'} or str(d.get('item_status') or '').lower()=='failed':
                        stats['genuine_failed']+=1
                    else:
                        stats['kept']+=1
                    continue

                # V4.0 NON-DESTRUCTIVE recovery.
                # Never delete image/video/final assets here. The app dispatcher builds a
                # scene-level checkpoint plan and retries only missing scenes. Existing
                # generated clips are valuable work and must survive restart/reconnect.
                if hard_recovery:
                    c.execute(
                        "UPDATE flow_jobs SET status='queued',error=NULL,agent_id=NULL,retry_count=0,max_retries=MAX(COALESCE(max_retries,5),12),next_retry_at=NULL,retry_reason='checkpoint_hard_recovery',last_stage='checkpoint_hard_recovery',updated_at=? WHERE id=?",
                        (now,jid)
                    )
                    c.execute(
                        "UPDATE parenting_auto_items SET status='generating',error=NULL,resume_retry_count=0,"
                        "checkpoint_recovery_count=checkpoint_recovery_count+1,last_failure_class='checkpoint_hard_recovery',next_retry_at=NULL,updated_at=? WHERE id=?",
                        (now,d['item_id'])
                    )
                    stats.setdefault('hard_recovered',0); stats['hard_recovered']+=1
                else:
                    c.execute(
                        "UPDATE flow_jobs SET status='queued',error=NULL,agent_id=NULL,retry_reason='auto_fb_resume',updated_at=? WHERE id=?",
                        (now,jid)
                    )
                    c.execute(
                        "UPDATE parenting_auto_items SET status='generating',error=NULL,"
                        "resume_retry_count=resume_retry_count+1,updated_at=? WHERE id=?",
                        (now,d['item_id'])
                    )
                c.execute(
                    "UPDATE parenting_story_runs SET status='generating',error=NULL,updated_at=? WHERE flow_job_id=?",
                    (now,jid)
                )
                stats['requeued']+=1
        return stats

    async def sync_auto_flow_jobs_on_agent_online(self) -> dict[str, int]:
        """Immediately reconcile generating Auto FB batches when a compatible Flow agent connects."""
        summary={'campaigns':0,'requeued':0,'done':0,'genuine_failed':0}
        with self._conn() as c:
            rows=c.execute(
                "SELECT id,current_batch_id FROM parenting_auto_campaigns "
                "WHERE COALESCE(auto_resume,1)=1 AND status IN ('running','preparing','error') AND current_batch_id IS NOT NULL"
            ).fetchall()
        for row in rows:
            cid=str(row['id']); bid=str(row['current_batch_id'] or '')
            if not bid:
                continue
            batch=self._batch_row(bid)
            items=self._item_rows(bid)
            if not batch or not items or str(batch.get('status') or '').lower()!='generating':
                continue
            stats=self._resume_generating_batch_jobs(bid)
            window_stats=self._enforce_generation_window(bid)
            summary['campaigns']+=1
            summary['requeued']+=stats['requeued']; summary['done']+=stats['done']; summary['genuine_failed']+=stats['genuine_failed']
            if stats['requeued']:
                with self._conn() as c:
                    c.execute("UPDATE parenting_auto_campaigns SET status='running',last_error=NULL,updated_at=? WHERE id=?",(self._now(),cid))
                    c.execute(
                        "UPDATE parenting_auto_batches SET phase='flow_generation',phase_message=?,progress_current=?,progress_total=?,progress_updated_at=?,updated_at=? WHERE id=?",
                        (f'Extension ONLINE → requeue {stats["requeued"]}/{len(items)} Flow job; đang dispatch lại.', stats['done'], len(items), self._now(), self._now(), bid)
                    )
                extra=f' · legacy retry {stats["legacy_requeued"]}' if stats.get('legacy_requeued') else ''
                await self._auto_log(cid,bid,'flow_generation',f'AGENT SYNC: extension ONLINE · recovery {stats["requeued"]}/{len(items)}{extra} · HOLD rolling {window_stats["held"]} · done {stats["done"]} · lỗi thật còn {stats["genuine_failed"]}.',level='warning',current=stats['done'],total=len(items))
        if summary['requeued']:
            await self.dispatch_jobs()
        return summary

    def _salvage_previous_auto_batch(self, campaign_id: str, current_batch_id: str | None) -> str | None:
        """Undo the V4.0 auto-skip loop when a newer batch was started before old Flow work was salvaged.

        If current batch has no video items yet and the immediately previous batch was auto-skipped
        only because transient Flow retries were exhausted, preserve the new candidate batch and
        restore the old batch as current. No candidate/script/artifact rows are deleted.
        """
        current=self._batch_row(str(current_batch_id)) if current_batch_id else None
        if not current:
            return None
        if self._item_rows(str(current['id'])):
            return None
        with self._conn() as c:
            prev=c.execute(
                "SELECT * FROM parenting_auto_batches WHERE campaign_id=? AND batch_no<? ORDER BY batch_no DESC LIMIT 1",
                (campaign_id,int(current.get('batch_no') or 0)),
            ).fetchone()
        if not prev:
            return None
        prev=dict(prev); prev_id=str(prev['id']); items=self._item_rows(prev_id)
        if not items:
            return None
        if any(str(x.get('status') or '').lower()=='published' for x in items):
            return None
        recoverable=[x for x in items if str(x.get('status') or '').lower() in {'skipped','failed'} and str(x.get('last_failure_class') or '').lower() not in {'flow_permanent','render_permanent','publish_permanent'}]
        if len(recoverable) != len(items):
            return None
        now=self._now()
        # Preserve current/new candidate pool for later rather than deleting/overwriting it.
        with self._conn() as c:
            c.execute("UPDATE parenting_auto_batches SET status='deferred_preserved',phase='deferred_preserved',phase_message=?,error=NULL,updated_at=? WHERE id=?",('Preserved while salvaging previous batch; candidates remain in DB',now,str(current['id'])))
            for x in items:
                jid=str(x.get('flow_job_id') or '')
                if not jid: continue
                c.execute("UPDATE flow_jobs SET status='queued',error=NULL,agent_id=NULL,retry_count=0,max_retries=MAX(COALESCE(max_retries,5),12),next_retry_at=NULL,retry_reason='v41_salvage_previous',last_stage='checkpoint_hard_recovery',updated_at=? WHERE id=?",(now,jid))
                c.execute("UPDATE parenting_story_runs SET status='generating',error=NULL,updated_at=? WHERE flow_job_id=?",(now,jid))
                c.execute("UPDATE parenting_auto_items SET status='generating',error=NULL,resume_retry_count=0,checkpoint_recovery_count=checkpoint_recovery_count+1,last_failure_class='checkpoint_hard_recovery',next_retry_at=NULL,updated_at=? WHERE id=?",(now,x['id']))
            c.execute("UPDATE parenting_auto_batches SET status='generating',phase='flow_generation',phase_message=?,error=NULL,progress_updated_at=?,updated_at=? WHERE id=?",('V4.1 SALVAGE: phục hồi batch cũ; giữ toàn bộ checkpoint/artifact',now,now,prev_id))
            c.execute("UPDATE parenting_auto_campaigns SET current_batch_id=?,status='running',last_error=NULL,updated_at=? WHERE id=?",(prev_id,now,campaign_id))
        self._enforce_generation_window(prev_id)
        return prev_id

    async def campaign_resume_on_startup(self) -> None:
        """Restore Auto FB campaigns after a server restart. START is persistent until PAUSE/STOP."""
        with self._conn() as c:
            rows=c.execute("SELECT id,status,current_batch_id,updated_at,auto_resume FROM parenting_auto_campaigns WHERE COALESCE(auto_resume,1)=1 AND status NOT IN ('paused','stopped') ORDER BY updated_at ASC").fetchall()
        if not rows:
            print('[AUTO FB][STARTUP] Không có campaign cần auto-resume', flush=True)
            return
        print(f'[AUTO FB][STARTUP] Auto-resume {len(rows)} campaign', flush=True)
        for row in rows:
            cid=str(row['id']); status=str(row['status'] or '').lower(); bid=str(row['current_batch_id'] or '')
            try:
                salvaged=self._salvage_previous_auto_batch(cid,bid)
                if salvaged:
                    await self._auto_log(cid,salvaged,'checkpoint_salvage',f'V4.1 SALVAGE: phục hồi batch cũ {salvaged}; batch mới {bid} được PRESERVE, không clear candidate.',level='warning')
                    bid=salvaged
                batch=self._batch_row(bid) if bid else None
                items=self._item_rows(bid) if bid else []
                # If Flow jobs/items already exist, preserve the batch and let extension reconnect + dispatch persisted jobs.
                if batch and str(batch.get('status') or '').lower()=='generating' and items:
                    stats=self._resume_generating_batch_jobs(bid)
                    window_stats=self._enforce_generation_window(bid)
                    with self._conn() as c:
                        c.execute("UPDATE parenting_auto_campaigns SET status='running',last_error=NULL,updated_at=? WHERE id=?",(self._now(),cid))
                        c.execute("UPDATE parenting_auto_batches SET phase='flow_generation',phase_message=?,progress_current=?,progress_total=?,progress_updated_at=?,updated_at=? WHERE id=?",(f'Auto-resume rolling: active tối đa {self._auto_generation_window(None)} · HOLD {window_stats["held"]}; done {stats["done"]}.',stats['done'],len(items),self._now(),self._now(),bid))
                    await self._auto_log(cid,bid,'flow_generation',f'STARTUP SYNC: batch #{batch.get("batch_no")} có {len(items)} item · recovery {stats["requeued"]} · HOLD rolling {window_stats["held"]} · done {stats["done"]} · lỗi Flow thật {stats["genuine_failed"]}.',level='warning',current=stats['done'],total=len(items))
                    await self.dispatch_jobs()
                    continue
                # V4.1: preparation/candidate/editor state is persisted in the batch row.
                # Resume the SAME batch so candidates/selected snapshots never disappear on restart.
                if batch:
                    reason=f'Server restart → RESUME SAME batch #{batch.get("batch_no")} {batch.get("status")}/{batch.get("phase")} · giữ product/candidate/selected cũ.'
                    with self._conn() as c:
                        c.execute("UPDATE parenting_auto_batches SET status='preparing',phase='resume_prepare',phase_message=?,error=NULL,progress_updated_at=?,updated_at=? WHERE id=?",(reason,self._now(),self._now(),bid))
                        c.execute("UPDATE parenting_auto_campaigns SET current_batch_id=?,status='preparing',last_error=NULL,updated_at=? WHERE id=?",(bid,self._now(),cid))
                    await self._auto_log(cid,bid,'resume_prepare',reason,level='warning')
                    scheduled=self._schedule_campaign_prepare(cid,resume_batch_id=bid)
                else:
                    with self._conn() as c:
                        c.execute("UPDATE parenting_auto_campaigns SET current_batch_id=NULL,status='preparing',last_error=NULL,updated_at=? WHERE id=?",(self._now(),cid))
                    scheduled=self._schedule_campaign_prepare(cid)
                await self._auto_log(cid,bid or None,'startup_resume',f'AUTO RESUME sau restart: prepare task {"STARTED" if scheduled else "ALREADY ACTIVE"}. Không tạo candidate pool mới nếu batch cũ đã có.',level='warning')
            except Exception as exc:
                await self._auto_log(cid,bid or None,'startup_resume',f'Auto-resume startup lỗi: {exc}',level='error')

    async def campaign_tick(self) -> None:
        # 1) synchronize publish jobs back into campaign items.
        with self._conn() as c:
            rows=c.execute("SELECT id,publish_job_id,status FROM parenting_auto_items WHERE publish_job_id IS NOT NULL AND status='publishing'").fetchall()
        for r in rows:
            with self._conn() as c:
                pub=c.execute("SELECT status,error FROM publish_jobs WHERE id=?",(r['publish_job_id'],)).fetchone()
            if not pub: continue
            st=str(pub['status'] or '')
            if st in {'submitted','dry_run_ok'}:
                with self._conn() as c: c.execute("UPDATE parenting_auto_items SET status='published',published_at=?,error=NULL,updated_at=? WHERE id=?",(self._now(),self._now(),r['id']))
            elif st=='failed':
                err=str(pub['error'] or 'Facebook publish failed')[:1000]
                with self._conn() as c:
                    row=c.execute("SELECT publish_retry_count FROM parenting_auto_items WHERE id=?",(r['id'],)).fetchone()
                    retry=int(row[0] or 0)+1 if row else 1
                    max_retry=max(1,int(os.getenv('AUTO_FB_PUBLISH_MAX_RETRIES','3') or 3))
                    if retry <= max_retry:
                        delay=min(300,15*(2**max(0,retry-1)))
                        next_at=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(timespec='seconds')
                        c.execute("UPDATE parenting_auto_items SET status='publish_failed',publish_retry_count=?,next_retry_at=?,last_failure_class='publish_transient',error=?,updated_at=? WHERE id=?",(retry,next_at,err,self._now(),r['id']))
                    else:
                        c.execute("UPDATE parenting_auto_items SET status='failed',last_failure_class='publish_permanent',error=?,updated_at=? WHERE id=?",(err,self._now(),r['id']))

        # Retry render/publish stages independently. Never regenerate Flow because FFmpeg/Facebook failed.
        now_utc=datetime.now(timezone.utc)
        with self._conn() as c:
            retry_rows=c.execute("SELECT id,flow_job_id,status,next_retry_at,publish_retry_count FROM parenting_auto_items WHERE status IN ('render_retry','publish_failed') ORDER BY updated_at ASC LIMIT 50").fetchall()
        for rr in retry_rows:
            due=True
            if rr['next_retry_at']:
                try:
                    due=datetime.fromisoformat(str(rr['next_retry_at']).replace('Z','+00:00')).astimezone(timezone.utc) <= now_utc
                except Exception:
                    due=True
            if not due:
                continue
            if str(rr['status'])=='render_retry' and rr['flow_job_id']:
                with self._conn() as c:
                    c.execute("UPDATE parenting_auto_items SET status='rendering',next_retry_at=NULL,updated_at=? WHERE id=?",(self._now(),rr['id']))
                self.spawn(self.render_story(str(rr['flow_job_id'])))
            elif str(rr['status'])=='publish_failed':
                # Reuse the already-rendered final video, but create a NEW publisher attempt only
                # after the backoff. Old failed publish_job_id is retained in publish_jobs history.
                with self._conn() as c:
                    c.execute("UPDATE parenting_auto_items SET status='ready',publish_job_id=NULL,next_retry_at=NULL,error=NULL,updated_at=? WHERE id=?",(self._now(),rr['id']))

        # 2) schedule one due publish per running campaign/page.
        campaigns=[x for x in self.list_campaigns(100) if x.get('status') in {'running','preparing'} or (x.get('status')=='error' and x.get('auto_resume'))]
        for camp in campaigns:
            batch=camp.get('current_batch'); items=camp.get('items') or []
            # A single permanently bad item must never freeze an always-on campaign.
            # It is archived as SKIPPED after all stage retry budgets are exhausted; the
            # rolling window immediately continues with the next planned item/batch.
            failed_items=[x for x in items if str(x.get('status') or '').lower()=='failed']
            if failed_items and batch:
                # First salvage transient/checkpoint failures. Retry exhaustion alone is NOT permanent.
                stats=self._resume_generating_batch_jobs(str(batch['id']))
                if stats.get('requeued'):
                    window_stats=self._enforce_generation_window(str(batch['id']))
                    await self._auto_log(camp['id'],batch['id'],'checkpoint_recovery',f'SELF HEAL failed items → recovery {stats["requeued"]} · hard {stats.get("hard_recovered",0)} · HOLD {window_stats["held"]}. Không skip chỉ vì retry counter cũ.',level='warning')
                    await self.dispatch_jobs()
                    items=self._item_rows(str(batch['id']))
                    failed_items=[x for x in items if str(x.get('status') or '').lower()=='failed']
                permanent=[x for x in failed_items if str(x.get('last_failure_class') or '').lower() in {'flow_permanent','render_permanent','publish_permanent'}]
                if permanent:
                    with self._conn() as c:
                        for x in permanent:
                            c.execute("UPDATE parenting_auto_items SET status='skipped',updated_at=? WHERE id=?",(self._now(),x['id']))
                    await self._auto_log(camp['id'],batch['id'],'skip_permanent',f'Chỉ bỏ qua {len(permanent)} item lỗi VĨNH VIỄN (policy/permission/render/publish permanent). Lỗi timeout/settings/picker không bị skip.',level='warning')
                    items=self._item_rows(str(batch['id']))
            if not batch:
                # Persistent runner retries ERROR campaigns, but back off so a dead 9Router/extension is not hammered every scheduler tick.
                if camp.get('status')=='error':
                    retry_sec=max(30,int(os.getenv('AUTO_FB_ERROR_RETRY_SECONDS','60') or 60))
                    try:
                        dt=datetime.fromisoformat(str(camp.get('updated_at') or '').replace('Z','+00:00'))
                        err_age=max(0,int((datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()))
                    except Exception:
                        err_age=retry_sec
                    if err_age < retry_sec:
                        continue
                with self._conn() as c: c.execute("UPDATE parenting_auto_campaigns SET status='preparing',updated_at=? WHERE id=?",(self._now(),camp['id']))
                await self._auto_log(camp['id'],None,'auto_resume',f'Campaign {camp.get("status")} không có current batch → tự schedule batch mới.',level='warning')
                self._schedule_campaign_prepare(camp['id']); continue

            # V2.6 watchdog: old V2.3/V2.4 batches can be status=queued/phase=queued forever.
            # If no in-memory prepare task owns the batch and there is no real generated item progress, recover it.
            bst=str(batch.get('status') or '').strip().lower(); phase=str(batch.get('phase') or '').strip().lower()
            task_active=camp['id'] in self._campaign_prepare_running
            age=int(camp.get('progress_age_seconds') or 0); item_count=len(items)
            error_retry=max(30,int(os.getenv('AUTO_FB_ERROR_RETRY_SECONDS','60') or 60))
            recoverable_status=bst in {'queued','preparing','selected'} or (bst in {'error','abandoned'} and age>=error_retry)
            stale_phase=(phase in {'','queued','starting','reading_products','candidate_generation','editor_selection','quality_gate','random_fill','queue_flow'} and age>=60)
            generating_without_items=(bst=='generating' and item_count==0 and age>=180)
            if not task_active and (recoverable_status or stale_phase or generating_without_items):
                reason=f'WATCHDOG: batch #{batch.get("batch_no")} {bst or "?"}/{phase or "?"} · task IDLE · age {age}s · items {item_count} → RESUME SAME BATCH.'
                await self._auto_log(camp['id'],batch['id'],'watchdog_recovery',reason,level='warning')
                with self._conn() as c:
                    c.execute("UPDATE parenting_auto_batches SET status='preparing',phase='resume_prepare',phase_message=?,error=NULL,progress_updated_at=?,updated_at=? WHERE id=?",(reason,self._now(),self._now(),batch['id']))
                    c.execute("UPDATE parenting_auto_campaigns SET current_batch_id=?,status='preparing',last_error=NULL,updated_at=? WHERE id=?",(batch['id'],self._now(),camp['id']))
                scheduled=self._schedule_campaign_prepare(camp['id'],resume_batch_id=str(batch['id']))
                await self._auto_log(camp['id'],batch['id'],'watchdog_recovery',f'Resume task {"STARTED" if scheduled else "SKIPPED"}; giữ candidate/product snapshot cũ.',level='warning')
                continue

            if camp.get('status')=='preparing':
                if task_active:
                    continue
                if bst=='generating':
                    with self._conn() as c: c.execute("UPDATE parenting_auto_campaigns SET status='running',updated_at=? WHERE id=?",(self._now(),camp['id']))
                continue
            if items and all(str(x.get('status') or '').lower() in {'published','skipped'} for x in items):
                with self._conn() as c:
                    c.execute("UPDATE parenting_auto_batches SET status='done',updated_at=? WHERE id=?",(self._now(),batch['id']))
                    c.execute("UPDATE parenting_auto_campaigns SET current_batch_id=NULL,status='preparing',updated_at=? WHERE id=?",(self._now(),camp['id']))
                await self._auto_log(camp['id'],batch['id'],'batch_done','Batch đã hoàn tất thật (published hoặc lỗi permanent đã skip).',current=len(items),total=len(items))
                await self.ui_broadcast({'type':'AUTO_FB_BATCH_DONE','campaignId':camp['id'],'batchId':batch['id'],'message':'Batch hoàn tất.'})
                with self._conn() as c:
                    deferred=c.execute("SELECT id FROM parenting_auto_batches WHERE campaign_id=? AND status='deferred_preserved' ORDER BY batch_no ASC LIMIT 1",(camp['id'],)).fetchone()
                    if deferred:
                        dbid=str(deferred['id'])
                        c.execute("UPDATE parenting_auto_campaigns SET current_batch_id=?,status='preparing',updated_at=? WHERE id=?",(dbid,self._now(),camp['id']))
                        c.execute("UPDATE parenting_auto_batches SET status='preparing',phase='resume_prepare',error=NULL,updated_at=? WHERE id=?",(self._now(),dbid))
                    else:
                        dbid=''
                if dbid:
                    await self._auto_log(camp['id'],dbid,'resume_prepare','Resume batch candidate đã preserve trước đó; KHÔNG sinh candidate mới.',level='warning')
                    self._schedule_campaign_prepare(camp['id'],resume_batch_id=dbid)
                else:
                    self._schedule_campaign_prepare(camp['id'])
                continue
            # V3.4 self-heal: if a generating batch has no live/ready work but persisted failed rows,
            # reconcile them once instead of sitting forever at FLOW_GENERATION.
            if bst=='generating' and items:
                live_states={'generating','queued','dispatching','running','rendering','render_retry','ready','publish_failed','publishing','published'}
                if not any(str(x.get('status') or '').lower() in live_states for x in items):
                    stats=self._resume_generating_batch_jobs(str(batch['id']))
                    window_stats=self._enforce_generation_window(str(batch['id']))
                    if stats['requeued']:
                        await self._auto_log(camp['id'],batch['id'],'flow_generation',f'SELF HEAL: recovery {stats["requeued"]}/{len(items)} · HOLD rolling {window_stats["held"]}; dispatch phần active.',level='warning',current=stats['done'],total=len(items))
                        await self.dispatch_jobs()
                        continue
            # V3.8 rolling generation: a 10-item batch is backlog, not 10 simultaneous videos.
            # Keep at most N unpublished videos (generating + ready + publishing) active.
            await self._activate_planned_items(camp,batch)
            items=self._item_rows(str(batch['id']))
            if any(x.get('status')=='publishing' for x in items): continue
            ready=next((x for x in items if x.get('status')=='ready' and x.get('final_path') and Path(str(x.get('final_path'))).exists()),None)
            if not ready: continue
            page=camp.get('page_profile') or {}
            due=self._campaign_next_due(camp,page)
            if not due or due > datetime.now(timezone.utc): continue
            if not self.create_publish_job or not self.facebook_publish_request_cls: continue
            plan=ready.get('plan') or {}; product=self.get_product(str(ready.get('product_id') or '')) or {}
            title=str(plan.get('title') or product.get('title') or 'Parenting product story')[:120]
            caption=(str(plan.get('hook') or title).strip()+"\n\n"+str((plan.get('product_ad') or {}).get('text') or '').strip()).strip()
            aff=str(plan.get('affiliate_url') or '').strip()
            if ready.get('product_id')!='__random_parenting__' and aff:
                caption=(caption+"\n\nXem sản phẩm: "+aff).strip()
            caption=caption[:1800]
            req=self.facebook_publish_request_cls(page_id=page['facebook_page_id'],video_path=ready['final_path'],title=title,description=caption,dry_run=bool(page.get('dry_run',True)))
            pub_id=self.create_publish_job(req)
            with self._conn() as c: c.execute("UPDATE parenting_auto_items SET status='publishing',publish_job_id=?,scheduled_at=?,updated_at=? WHERE id=?",(pub_id,due.isoformat(timespec='seconds'),self._now(),ready['id']))
            published=sum(1 for x in items if x.get('status')=='published')
            await self._auto_log(camp['id'],batch['id'],'publishing',f'Đưa video #{ready.get("rank_no")} vào Facebook publisher · {"DRY RUN" if page.get("dry_run",True) else "REAL"}',current=published,total=len(items))
            await self.ui_broadcast({'type':'AUTO_FB_PUBLISH_QUEUED','campaignId':camp['id'],'itemId':ready['id'],'publishJobId':pub_id,'dryRun':bool(page.get('dry_run',True))})


    def _register_routes(self) -> None:
        r=self.router

        @r.get("/summary")
        def summary():
            with self._conn() as c:
                chars=int(c.execute("SELECT COUNT(*) FROM parenting_characters WHERE enabled=1").fetchone()[0])
                sets=int(c.execute("SELECT COUNT(*) FROM parenting_character_sets WHERE enabled=1").fetchone()[0])
                runs=int(c.execute("SELECT COUNT(*) FROM parenting_story_runs").fetchone()[0])
                ready=int(c.execute("SELECT COUNT(*) FROM parenting_story_runs WHERE status='done'").fetchone()[0])
            return {"ok":True,"characters":chars,"character_sets":sets,"runs":runs,"ready":ready}

        @r.get("/characters")
        def characters():
            return self.list_characters()

        @r.post("/characters")
        def save_character_api(req: CharacterSave):
            return {"ok":True,"character":self.save_character(req)}

        @r.get("/characters/{character_id}/reference")
        def character_reference(character_id: str):
            ch=self.get_character(character_id)
            if not ch:
                raise HTTPException(404,"Không tìm thấy character")
            p=Path(str(ch.get("reference_path") or ""))
            if not p.exists():
                raise HTTPException(404,"Character chưa có reference")
            return FileResponse(p)

        @r.delete("/characters/{character_id}")
        def delete_character(character_id: str):
            with self._conn() as c:
                used=int(c.execute("SELECT COUNT(*) FROM parenting_character_sets WHERE mother_character_id=? OR child_character_id=? OR father_character_id=?", (character_id,character_id,character_id)).fetchone()[0])
                if used:
                    raise HTTPException(409, "Character đang được Character Set sử dụng")
                c.execute("DELETE FROM parenting_characters WHERE id=?", (character_id,))
            return {"ok":True}

        @r.post("/characters/generate")
        async def generate_character(req: CharacterGenerateRequest):
            ch=self.get_character(req.character_id)
            if not ch:
                raise HTTPException(404, "Không tìm thấy character")
            prompt=(
                "Create one clean premium 3D animated character master reference, vertical 9:16. "
                f"Character: {ch['visual_prompt']}. "
                "Single character only. Warm neutral studio background. Face clearly visible. Natural expression. Consistent anatomy. "
                "No text, no watermark, no dramatic pose, no blur, no occlusion."
            )
            scene={"sceneId":1,"imagePrompt":prompt,"videoPrompt":"","inputImages":[],"metadata":{"parenting":True,"parentingMode":"character_master","characterId":ch["id"],"referenceTitle":self._reference_title(ch['id']),"referenceFileName":self._reference_filename(ch['id'])}}
            flow=self.default_flow_config(imageModel=req.image_model,videoModel="NONE",imageConcurrency=1,aspectRatio=req.aspect_ratio,imageOutputs="x1",maxSubmitsPerMinute=3)
            jid=self.create_flow_job("parenting_character_master",[scene],flow)
            await self.ui_broadcast({"type":"PARENTING_JOB_QUEUED","jobId":jid,"kind":"parenting_character_master","characterId":ch["id"]})
            with self._conn() as c:
                c.execute("UPDATE parenting_characters SET generated_job_id=?,updated_at=? WHERE id=?", (jid,self._now(),ch["id"]))
            await self.dispatch_jobs()
            return {"ok":True,"job_id":jid,"character_id":ch["id"]}

        @r.get("/character-sets")
        def character_sets():
            return self.list_sets()

        @r.post("/character-sets")
        def save_set_api(req: CharacterSetSave):
            return {"ok":True,"character_set":self.save_set(req)}

        @r.post("/plan")
        def plan(req: PlanRequest):
            return {"ok":True,"plan":self.generate_plan(req)}

        @r.post("/test-scene")
        async def test_scene(req: TestSceneRequest):
            scenes,flow=self.build_test_scene(req)
            jid=self.create_flow_job("parenting_test_scene",scenes,flow)
            await self.ui_broadcast({"type":"PARENTING_JOB_QUEUED","jobId":jid,"kind":"parenting_test_scene","sceneCount":len(scenes)})
            await self.dispatch_jobs()
            return {"ok":True,"job_id":jid,"scenes":scenes,"flow":flow}

        @r.post("/generate")
        async def generate(req: StoryGenerateRequest):
            plan,scenes,flow=self.build_story(req)
            jid=self.create_flow_job("parenting_story",scenes,flow)
            rid=self._save_run(req,plan,jid)
            await self.ui_broadcast({"type":"PARENTING_JOB_QUEUED","jobId":jid,"kind":"parenting_story","runId":rid,"sceneCount":len(scenes)})
            await self.dispatch_jobs()
            return {"ok":True,"run_id":rid,"job_id":jid,"plan":plan,"scene_count":len(scenes)}

        @r.post("/shopee/search-preview")
        async def shopee_search_preview(req: ShopeeSearchPreviewRequest):
            return {"ok":True, **(await self.preview_shopee_search(req))}

        @r.post("/product/inspect")
        async def product_inspect(req: ProductInspectRequest):
            url=self._validate_shopee_url(req.url)
            cached=self.get_product_by_url(url)
            # Normal script generation should reuse the last successful snapshot. Only the
            # explicit READ/REFRESH button forces a browser inspection again.
            if cached and not req.force_refresh:
                return {"ok":True,"product":cached,"cached":True,"inspected":False}
            if self.inspect_product_url is None:
                if cached:
                    return {"ok":True,"product":cached,"cached":True,"inspected":False,"warning":"Shopee inspector chưa sẵn sàng; đang dùng snapshot đã lưu."}
                raise HTTPException(503, "Server chưa có Shopee inspector")
            try:
                capture=await self.inspect_product_url(url)
                product=await asyncio.to_thread(self._save_product_capture,url,capture,req.model)
                await self.ui_broadcast({"type":"SHOPEE_PRODUCT_SAVED","productId":product.get("id"),"title":product.get("title"),"imageReady":product.get("image_ready")})
                return {"ok":True,"product":product,"cached":False,"inspected":True}
            except HTTPException:
                raise
            except Exception as exc:
                # Do not destroy a usable workflow because Shopee/Chrome failed to refresh.
                # If we have a previous snapshot, return it with a visible warning instead
                # of HTTP 502.
                if cached:
                    await self.ui_broadcast({"type":"SHOPEE_INSPECT_FALLBACK_CACHE","productId":cached.get("id"),"error":str(exc)})
                    return {"ok":True,"product":cached,"cached":True,"inspected":False,"warning":f"Refresh Shopee lỗi, đang dùng snapshot cũ: {exc}"}
                raise HTTPException(502, f"Đọc Shopee lỗi: {exc}. Kiểm tra extension v14.6.0+ đang nối 8787; mở link Shopee bằng Chrome một lần nếu Shopee yêu cầu đăng nhập/xác minh rồi thử lại.")

        @r.get("/products")
        def products(limit: int=30):
            return self.list_products(limit)

        @r.get("/products/{product_id}")
        def product_detail(product_id: str):
            product=self.get_product(product_id)
            if not product:
                raise HTTPException(404,"Không tìm thấy sản phẩm")
            return product

        @r.get("/products/{product_id}/image")
        def product_image(product_id: str):
            product=self.get_product(product_id)
            if not product:
                raise HTTPException(404,"Không tìm thấy sản phẩm")
            p=Path(str(product.get("local_image_path") or ""))
            if not p.exists():
                raise HTTPException(404,"Sản phẩm chưa có ảnh local")
            return FileResponse(p)

        @r.post("/product/plan")
        def product_plan(req: ProductPlanRequest):
            return {"ok":True,"plan":self.generate_product_plan(req)}

        @r.post("/product/generate")
        async def product_generate(req: ProductGenerateRequest):
            plan,scenes,flow=self.build_product_story(req)
            jid=self.create_flow_job("parenting_story",scenes,flow)
            rid=self._save_product_run(req,plan,jid)
            await self.ui_broadcast({"type":"PARENTING_JOB_QUEUED","jobId":jid,"kind":"parenting_product_story","runId":rid,"sceneCount":len(scenes),"productId":req.product_id})
            await self.dispatch_jobs()
            return {"ok":True,"run_id":rid,"job_id":jid,"plan":plan,"scene_count":len(scenes)}

        @r.get("/music/status")
        def music_status_api():
            return {"ok":True,"status":self.music_status()}

        @r.get("/auto-fb/page-profiles")
        def auto_fb_page_profiles():
            return {"ok":True,"items":self.list_auto_page_profiles()}

        @r.post("/auto-fb/page-profiles")
        def auto_fb_page_profile_save(req: AutoFbPageProfileSave):
            return {"ok":True,"item":self.save_auto_page_profile(req)}

        @r.get("/auto-fb/campaigns")
        def auto_fb_campaigns(limit: int=20):
            return {"ok":True,"items":self.list_campaigns(limit)}

        @r.get("/auto-fb/campaigns/{campaign_id}")
        def auto_fb_campaign_detail(campaign_id: str):
            item=self.get_campaign(campaign_id)
            if not item: raise HTTPException(404,"Không tìm thấy Auto FB campaign")
            return {"ok":True,"item":item}

        @r.post("/auto-fb/campaigns/start")
        async def auto_fb_campaign_start(req: AutoFbCampaignStartRequest):
            item=await self.start_campaign(req)
            return {"ok":True,"item":item}

        @r.put("/auto-fb/campaigns/{campaign_id}/links")
        async def auto_fb_campaign_links(campaign_id: str, req: AutoFbCampaignLinksUpdate):
            campaign=self._campaign_row(campaign_id)
            if not campaign: raise HTTPException(404,"Không tìm thấy Auto FB campaign")
            links=self._clean_shopee_urls(req.shopee_urls)
            for u in links: self._validate_shopee_url(u)
            with self._conn() as c:
                c.execute("UPDATE parenting_auto_campaigns SET source_links_json=?,updated_at=? WHERE id=?",(self._dumps(links),self._now(),campaign_id))
            await self.ui_broadcast({"type":"AUTO_FB_LINKS_UPDATED","campaignId":campaign_id,"links":len(links),"message":"Link mới chỉ áp dụng từ batch kế tiếp; có thể để rỗng để chạy RANDOM ONLY."})
            return {"ok":True,"item":self.get_campaign(campaign_id)}

        @r.post("/auto-fb/campaigns/{campaign_id}/state")
        async def auto_fb_campaign_state(campaign_id: str, req: AutoFbCampaignStateUpdate):
            campaign=self._campaign_row(campaign_id)
            if not campaign: raise HTTPException(404,"Không tìm thấy Auto FB campaign")
            action=req.action
            status={"pause":"paused","stop":"stopped","resume":"running","start":"running"}[action]
            current=campaign.get('current_batch_id')
            if action in {'resume','start'} and not current:
                status='preparing'
            auto_resume=1 if action in {'resume','start'} else 0
            with self._conn() as c:
                c.execute("UPDATE parenting_auto_campaigns SET status=?,auto_resume=?,last_error=NULL,updated_at=? WHERE id=?",(status,auto_resume,self._now(),campaign_id))
            if status=='preparing': self._schedule_campaign_prepare(campaign_id)
            await self.ui_broadcast({"type":"AUTO_FB_CAMPAIGN_STATE","campaignId":campaign_id,"status":status})
            return {"ok":True,"item":self.get_campaign(campaign_id)}

        @r.post("/auto-fb/campaigns/{campaign_id}/kick")
        async def auto_fb_campaign_kick(campaign_id: str):
            if not self._campaign_row(campaign_id): raise HTTPException(404,"Không tìm thấy Auto FB campaign")
            await self._auto_log(campaign_id,str((self._campaign_row(campaign_id) or {}).get('current_batch_id') or '') or None,'watchdog','User bấm KICK WATCHDOG · kiểm tra batch ngay.',level='warning')
            await self.campaign_tick()
            item=self.get_campaign(campaign_id)
            return {"ok":True,"item":item}

        @r.post("/auto-fb/campaigns/tick")
        async def auto_fb_campaign_tick_api():
            await self.campaign_tick()
            return {"ok":True}

        @r.post("/auto-fb/topics")
        def auto_fb_topics(req: AutoFbTopicsRequest):
            result=self.generate_auto_topics(req)
            return {"ok":True, **result}

        @r.post("/auto-fb/queue")
        async def auto_fb_queue(req: AutoFbQueueRequest):
            result=self.queue_auto_topics(req)
            for item in result.get("items", []):
                await self.ui_broadcast({"type":"PARENTING_JOB_QUEUED","jobId":item["job_id"],"kind":"parenting_story","runId":item["run_id"],"sceneCount":item.get("scene_count",0),"topic":item["topic"]})
            await self.dispatch_jobs()
            return {"ok":True, **result}

        @r.get("/runs")
        def runs(limit: int=50):
            return self.list_runs(limit)

        @r.get("/runs/{run_id}")
        def run_detail(run_id: str):
            with self._conn() as c:
                row=c.execute("SELECT * FROM parenting_story_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(404,"Không tìm thấy run")
            d=dict(row); d["plan"]=self._loads(d.pop("plan_json"),{})
            return d


def register_parenting_routes(app: Any, **deps: Any) -> ParentingHandler:
    handler=ParentingHandler(**deps)
    app.include_router(handler.router)
    return handler
