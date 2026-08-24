from __future__ import annotations

import asyncio
import json
import sqlite3
import os
import random
import shutil
import importlib.util
import uuid
from pathlib import Path
from typing import Any

import httpx

from . import db
from . import server_features
from .env_loader import load_project_env, env_status, ENV_PATH, get_env, resolve_env_snapshot

ROOT = Path(__file__).resolve().parents[1]


class EngineError(RuntimeError):
    pass


def _global_flow() -> dict[str, Any]:
    # V2.8 owns one Flow worker; these values are defaults/fallbacks only.
    from .broker import load_settings
    return load_settings()


def _flow_choice(config: dict[str, Any], flow: dict[str, Any], config_key: str, flow_key: str, default: Any) -> Any:
    value = config.get(config_key)
    if value not in (None, ""):
        return value
    return flow.get(flow_key) or default


def _flow_int(config: dict[str, Any], flow: dict[str, Any], config_key: str, flow_key: str, default: int, lo: int, hi: int) -> int:
    raw = _flow_choice(config, flow, config_key, flow_key, default)
    try:
        return max(lo, min(hi, int(raw)))
    except Exception:
        return default


def _script_model(flow: dict[str, Any]) -> str:
    return str(flow.get("scriptAiModel") or "ag/gemini-3.1-pro-high").strip()


def _output_duration(value: Any) -> str:
    text = str(value or "random_30_35s").strip()
    if text == "random_30_35s":
        return f"{random.randint(30, 35)}s"
    return text or "32s"


def _pick_video_mode(value: Any) -> str:
    if isinstance(value, list):
        modes = [str(x).strip().upper() for x in value if str(x).strip()]
        return random.choice(modes) if modes else "AUTO"
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    return "AUTO"
PROMPT_VI_EN = {
    "mother_visual_prompt": (
        "Mẹ trẻ châu Á khoảng 30 tuổi, gương mặt dịu dàng hình oval, mắt nâu ấm, tóc nâu đậm dài hơi gợn sóng, áo blouse màu kem, quần hoặc váy xanh sage, biểu cảm kiên nhẫn và trấn an, phong cách phim hoạt hình gia đình 3D cao cấp, ánh sáng điện ảnh ấm áp.",
        "Young Asian mother around 30 years old, oval gentle face, warm brown eyes, long dark brown slightly wavy hair, cream blouse, sage green pants or skirt, patient and reassuring expression, premium 3D family animation film style, warm cinematic lighting.",
    ),
    "mother_voice_prompt": (
        "Giọng nữ người lớn tiếng Việt, ấm áp, dịu dàng, bình tĩnh, kiên nhẫn, nhịp nói hội thoại tự nhiên.",
        "Vietnamese adult female voice, warm, gentle, calm, patient, natural conversational pacing.",
    ),
    "child_visual_prompt": (
        "Bé gái châu Á 4 tuổi, gương mặt tròn đáng yêu, mắt nâu to, tóc bob ngắn màu đen, đồ ngủ pastel hồng, ôm gấu bông màu be, hơi nhút nhát và giàu cảm xúc nhưng dễ thương, phong cách phim hoạt hình gia đình 3D cao cấp, ánh sáng điện ảnh ấm áp.",
        "4-year-old Asian girl, round cute face, large brown eyes, short dark bob hair, pink pastel pajamas, holding a beige teddy bear, shy and emotional but lovable, premium 3D family animation film style, warm cinematic lighting.",
    ),
    "child_voice_prompt": (
        "Giọng bé gái tiếng Việt, dễ thương, mềm, hơi cao, giàu cảm xúc nhưng rõ lời, nhịp nói tự nhiên như trẻ nhỏ.",
        "Vietnamese little girl voice, cute, soft, slightly higher pitch, emotional but clear, natural childlike pacing.",
    ),
}

def _looks_vietnamese(value: str) -> bool:
    return any("\u00c0" <= ch <= "\u1ef9" for ch in str(value or ""))

def _prompt_to_english(key: str, value: str) -> str:
    text = str(value or "").strip()
    pair = PROMPT_VI_EN.get(key)
    if not pair:
        return text
    vi, en = pair
    if not text or text == vi:
        return en
    if text == en:
        return en
    if _looks_vietnamese(text):
        return f"{en} Additional user note translated intent: {text.replace(chr(10), ' ')[:500]}"
    return text

class EngineFacade:
    def __init__(self, apps: dict[str, Any]) -> None:
        self.apps = apps
        self.parenting_multi_runs: dict[str, dict[str, Any]] = {}

    async def call(self, engine: str, method: str, path: str, body: Any = None, timeout: float = 120.0) -> Any:
        app = self.apps.get(engine)
        if not app:
            raise EngineError(f"Engine không tồn tại: {engine}")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://v28.local", timeout=timeout) as client:
            r = await client.request(method.upper(), path, json=body)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise EngineError(f"{engine} {method} {path} -> HTTP {r.status_code}: {str(detail)[:4000]}")
        ct = r.headers.get("content-type", "")
        return r.json() if "json" in ct else r.text

    async def sync_fb_page_to_legacy(self, page_id: str, name: str, access_token: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
        payload = {"page_id": page_id, "name": name, "access_token": access_token}
        for engine in ("beauty", "parenting"):
            try:
                results[engine] = await self.call(engine, "POST", "/api/facebook/pages", payload)
            except Exception as exc:
                results[engine] = {"ok": False, "error": str(exc)}
        return results

    async def sync_all_fb_pages_to_legacy(self, pages: list[dict[str, Any]]) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for p in pages:
            secret = db.row("SELECT access_token FROM facebook_pages WHERE id=?", (p["id"],))
            if not secret:
                continue
            results[p["id"]] = await self.sync_fb_page_to_legacy(p["id"], p["name"], secret["access_token"])
        return results

    async def remove_fb_page_from_legacy(self, page_id: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for engine in ("beauty", "parenting"):
            try:
                # ignore=false means a later re-import is allowed; V2.8 owns the source of truth.
                results[engine] = await self.call(engine, "DELETE", f"/api/facebook/pages/{page_id}?ignore=false", timeout=30)
            except Exception as exc:
                results[engine] = {"ok": False, "error": str(exc)}
        return results

    # ---------------- celebrity / BROLL ----------------
    def _celebrity_db_path(self) -> Path:
        return ROOT / "modules" / "facebook" / "data" / "factory.db"

    def ensure_celebrity_profile(self, instance_id: str, name: str, config: dict[str, Any], engine_ref: str | None) -> str:
        path = self._celebrity_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            row = None
            if engine_ref:
                try:
                    row = c.execute("SELECT id FROM pages WHERE id=?", (int(engine_ref),)).fetchone()
                except Exception:
                    row = None
            pool = [str(x).strip() for x in (config.get("celebrity_pool") or ["Warren Buffett", "Jack Ma"]) if str(x).strip()]
            # Use the same source-of-truth resolver as preflight/subprocess. 2.8.5.7
            # accidentally referenced an undefined local named `resolved` here, causing
            # every Job 1 run to fail before preflight even started.
            resolved_env = resolve_env_snapshot(("SERPER_API_KEY",))
            # Without Serper, never randomly select an uncached celebrity and fail later.
            if not resolved_env.get("SERPER_API_KEY"):
                slug_map = {
                    "Warren Buffett": "warren_buffett", "Jack Ma": "jack_ma",
                    "Steve Jobs": "steve_jobs", "Charlie Munger": "charlie_munger",
                    "Elon Musk": "elon_musk", "Bill Gates": "bill_gates",
                    "Jackie Chan": "jackie_chan",
                }
                cache_root = ROOT / "modules" / "facebook" / "engine_v27" / "input" / "celebrity_verified_v26"
                video_exts = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
                cached = []
                for celeb_name in pool:
                    slug = slug_map.get(celeb_name)
                    folder = cache_root / slug if slug else None
                    if folder and folder.is_dir() and any(x.is_file() and x.suffix.lower() in video_exts for x in folder.iterdir()):
                        cached.append(celeb_name)
                if cached:
                    pool = cached
            payload = {
                "name": name,
                "theme": str(config.get("theme") or "life"),
                "celebrity_pool": json.dumps(pool, ensure_ascii=False),
                "posts_per_day": max(1, min(8, int(config.get("posts_per_day") or 2))),
                "slot1": str(config.get("slot1") or "10:00"),
                "slot2": str(config.get("slot2") or "19:00"),
                "output_mode": "reel_9_16",
            }
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if row:
                c.execute(
                    "UPDATE pages SET name=?,theme=?,celebrity_pool=?,posts_per_day=?,slot1=?,slot2=?,output_mode=?,enabled=1,page_id='',token_env_key='',updated_at=? WHERE id=?",
                    (payload["name"], payload["theme"], payload["celebrity_pool"], payload["posts_per_day"], payload["slot1"], payload["slot2"], payload["output_mode"], ts, int(engine_ref)),
                )
                c.commit()
                return str(engine_ref)
            cur = c.execute(
                """INSERT INTO pages(name,page_id,token_env_key,enabled,posts_per_day,slot1,slot2,theme,celebrity_pool,output_mode,created_at,updated_at)
                   VALUES(?, '', '', 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["name"], payload["posts_per_day"], payload["slot1"], payload["slot2"], payload["theme"], payload["celebrity_pool"], payload["output_mode"], ts, ts),
            )
            c.commit()
            return str(cur.lastrowid)
        finally:
            c.close()

    def _celebrity_preflight(self, config: dict[str, Any]) -> None:
        # Re-read root .env before every manual/scheduled run so an inherited blank
        # Windows variable cannot mask a valid key in the project .env.
        load_project_env()
        engine = ROOT / "modules" / "facebook" / "engine_v27"
        if not (engine / "app.py").is_file():
            raise EngineError(f"Job 1 thiếu BROLL engine: {engine / 'app.py'}")
        missing_bin = [x for x in ("ffmpeg", "ffprobe") if not shutil.which(x)]
        if missing_bin:
            raise EngineError("Job 1 thiếu binary trong PATH: " + ", ".join(missing_bin))
        resolved = resolve_env_snapshot(("PEXELS_API_KEY", "PIXABAY_API_KEY", "SERPER_API_KEY", "9ROUTER_API_KEY"))
        if not (resolved.get("PEXELS_API_KEY") or resolved.get("PIXABAY_API_KEY")):
            st = env_status("PEXELS_API_KEY", "PIXABAY_API_KEY")
            raise EngineError(
                "Job 1 thiếu B-roll API key: cần PEXELS_API_KEY hoặc PIXABAY_API_KEY. "
                f"Resolver: PEXELS={st['PEXELS_API_KEY']} · PIXABAY={st['PIXABAY_API_KEY']}. "
                "Chạy ENV_CHECK.bat để xem file nguồn (không lộ secret)."
            )
        if importlib.util.find_spec("edge_tts") is None:
            raise EngineError("Job 1 thiếu package edge-tts; chạy START.bat để cài dependency")

        # Celebrity search may run entirely from cache. If SERPER is absent, only cached
        # celebrities are safe; fail early instead of letting app.py exit with code 1.
        if not resolved.get("SERPER_API_KEY"):
            slug_map = {
                "Warren Buffett": "warren_buffett", "Jack Ma": "jack_ma",
                "Steve Jobs": "steve_jobs", "Charlie Munger": "charlie_munger",
                "Elon Musk": "elon_musk", "Bill Gates": "bill_gates",
                "Jackie Chan": "jackie_chan",
            }
            pool = [str(x).strip() for x in (config.get("celebrity_pool") or []) if str(x).strip()]
            video_exts = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
            cache_root = engine / "input" / "celebrity_verified_v26"
            cached: list[str] = []
            for name in pool:
                slug = slug_map.get(name)
                folder = cache_root / slug if slug else None
                if folder and folder.is_dir() and any(x.is_file() and x.suffix.lower() in video_exts for x in folder.iterdir()):
                    cached.append(name)
            if pool and not cached:
                raise EngineError(
                    "Job 1 thiếu SERPER_API_KEY và không có celebrity cache cho pool hiện tại. "
                    f"Điền SERPER_API_KEY trong {ENV_PATH} hoặc import/cache video celebrity trước. "
                    f"Trạng thái SERPER={env_status('SERPER_API_KEY')['SERPER_API_KEY']}"
                )

    async def run_celebrity(self, engine_ref: str, config: dict[str, Any]) -> dict[str, Any]:
        self._celebrity_preflight(config)
        body: dict[str, Any] = {"page_row_id": int(engine_ref), "v28_config": config}
        model = str(config.get("model") or "").strip()
        if model:
            body["model"] = model
        start = await self.call("celebrity", "POST", "/api/test-video", body, timeout=60)
        jid = str(start.get("job_id") or "")
        if not jid:
            raise EngineError(f"Celebrity không trả job_id: {start}")
        return {"engine_run_id": jid, "engine_job_ids": [jid]}

    async def wait_celebrity(self, job_id: str, timeout_sec: int = 2400) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            job = await self.call("celebrity", "GET", f"/api/jobs/{job_id}", timeout=30)
            status = str(job.get("status") or "").upper()
            if status in {"TEST_READY", "READY", "PUBLISHED"}:
                path = str(job.get("output_path") or "")
                if not path:
                    raise EngineError("Celebrity READY nhưng thiếu output_path")
                path = str(Path(path).resolve())
                if not Path(path).is_file() or Path(path).stat().st_size < 1024:
                    raise EngineError(f"Celebrity READY nhưng file output không tồn tại/rỗng: {path}")
                return {
                    "status": "done",
                    "video_paths": [path],
                    "title": str(job.get("title") or ""),
                    "caption": str(job.get("topic") or ""),
                    "raw": job,
                }
            if status in {"TEST_FAILED", "FAILED"}:
                err = str(job.get("error") or "").strip()
                logs = job.get("logs") or []
                if (not err or err.lower().startswith("video engine exit code")) and isinstance(logs, list):
                    # Prefer the last ERROR, otherwise last ENGINE line. This exposes the
                    # real BROLL/FFmpeg/TTS/API failure in the master Runs/Logs UI.
                    candidates = [x for x in logs if str(x.get("level") or "").upper() == "ERROR"]
                    if not candidates:
                        candidates = [x for x in logs if str(x.get("level") or "").upper() == "ENGINE"]
                    if candidates:
                        err = str(candidates[-1].get("message") or err).strip()
                raise EngineError(err or f"Celebrity job {status}")
            await asyncio.sleep(2)
        raise EngineError("Celebrity render timeout")

    # ---------------- beauty / Flow Content ----------------
    async def ensure_beauty_profile(self, instance_id: str, name: str, config: dict[str, Any], engine_ref: str | None) -> str:
        profile_id = engine_ref or f"v28_{instance_id.replace('.', '_')}"
        flow = _global_flow()
        payload = {
            "id": profile_id,
            "name": name,
            "theme": config.get("theme") or "adult glamour lifestyle in Vietnam",
            "persona_path": config.get("persona_path"),
            "persona_left_path": config.get("persona_left_path"),
            "persona_right_path": config.get("persona_right_path"),
            "persona_back_path": config.get("persona_back_path"),
            "body_preset": config.get("body_preset") or "curvy_fit",
            "sexiness_level": int(config.get("sexiness_level") if config.get("sexiness_level") is not None else 85),
            "outfit_prompts": config.get("outfit_prompts") or [],
            "outfit_paths": config.get("outfit_paths") or [],
            "backgrounds": config.get("backgrounds") or [],
            "poses": config.get("poses") or [],
            "music_paths": config.get("music_paths") or [],
            "default_video_mode": _pick_video_mode(config.get("mode")),
            "image_to_video_ratio": int(config.get("image_to_video_ratio") or 0),
            "image_model": _flow_choice(config, flow, "image_model", "imageModel", "Nano Banana 2"),
            "video_model": _flow_choice(config, flow, "video_model", "videoModel", "Veo 3.1 - Fast"),
            "facebook_page_id": None,
            "title_hint": config.get("title_hint") or "",
            "caption_style": config.get("caption_style") or "engaging_short",
            "ai_model": config.get("ai_model") or _script_model(flow),
            "ai_provider": config.get("ai_provider") or "router9",
            "enabled": True,
        }
        result = await self.call("beauty", "POST", "/api/page-profiles", payload, timeout=120)
        profile = result.get("profile") or {}
        pid = str(profile.get("id") or profile_id)
        # V2.8 is the only scheduler for plugin instances. Even if an Advanced legacy UI
        # previously enabled this profile scheduler, force it OFF whenever V2.8 uses it.
        beauty_db = ROOT / "modules" / "flow_content" / "data" / "factory.sqlite3"
        if beauty_db.exists():
            try:
                with sqlite3.connect(beauty_db, timeout=30) as c:
                    c.row_factory = sqlite3.Row
                    row = c.execute("SELECT scheduler_config_json FROM page_profiles WHERE id=?", (pid,)).fetchone()
                    try:
                        sched_cfg = json.loads((row["scheduler_config_json"] if row else None) or "{}")
                    except Exception:
                        sched_cfg = {}
                    sched_cfg.update({
                        "scene_mode": str(config.get("scene_mode") or "VIETNAM").upper(),
                        "scene_mix": config.get("scene_mix") or ["GYM", "BEACH"],
                        "beat_image_count": int(config.get("beat_image_count") or 7),
                        "beat_duration_sec": float(config.get("beat_duration_sec") or 10.0),
                        "beat_motion_preset": str(config.get("beat_motion_preset") or "chaos_mix"),
                        "i2v_clip_count": int(config.get("i2v_clip_count") or 3),
                        "i2v_clip_duration": str(_flow_choice(config, flow, "video_duration", "videoDuration", "8s")),
                        "mode": _pick_video_mode(config.get("mode")),
                        "image_concurrency": _flow_int(config, flow, "image_concurrency", "imageConcurrency", 1, 1, 9),
                        "video_concurrency": _flow_int(config, flow, "video_concurrency", "videoConcurrency", 1, 1, 4),
                    })
                    c.execute(
                        "UPDATE page_profiles SET scheduler_enabled=0,next_publish_at=NULL,scheduler_config_json=? WHERE id=?",
                        (json.dumps(sched_cfg, ensure_ascii=False), pid),
                    )
                    c.commit()
            except Exception as exc:
                db.log_event(f"Beauty legacy config sync {pid}: {exc}", level="WARNING", kind="beauty")
        if bool(config.get("auto_generate_angles", False)):
            try:
                await self.call("beauty", "POST", f"/api/page-profiles/{pid}/angles/generate-missing", timeout=60)
                deadline = asyncio.get_running_loop().time() + float(config.get("angle_ready_timeout_sec") or 900)
                while asyncio.get_running_loop().time() < deadline:
                    status = await self.call("beauty", "GET", f"/api/page-profiles/{pid}/persona-pack-status", timeout=30)
                    profile_status = status.get("profile") or {}
                    if profile_status.get("persona_pack_ready"):
                        break
                    active = status.get("active_jobs") or {}
                    if not active and int(profile_status.get("persona_angle_count") or 0) >= 3:
                        break
                    await asyncio.sleep(5)
            except Exception as exc:
                db.log_event(f"Beauty persona angle bootstrap {pid}: {exc}", level="WARNING", kind="beauty")
        return pid

    async def run_beauty(self, engine_ref: str, config: dict[str, Any]) -> dict[str, Any]:
        flow = _global_flow()
        payload = {
            "page_profile_id": engine_ref,
            "videos": max(1, min(20, int(config.get("videos_per_run") or 1))),
            "mode": _pick_video_mode(config.get("mode")),
            "beat_image_count": int(config.get("beat_image_count") or 7),
            "beat_duration_sec": float(config.get("beat_duration_sec") or 10.0),
            "beat_motion_preset": config.get("beat_motion_preset") or "chaos_mix",
            "i2v_clip_count": int(config.get("i2v_clip_count") or 3),
            "i2v_clip_duration": _flow_choice(config, flow, "video_duration", "videoDuration", "8s"),
            "image_concurrency": _flow_int(config, flow, "image_concurrency", "imageConcurrency", 1, 1, 9),
            "video_concurrency": _flow_int(config, flow, "video_concurrency", "videoConcurrency", 1, 1, 4),
            "location_strategy": str(config.get("location_strategy") or "distinct_vietnam_locations"),
            "location_anchor_strength": int(config.get("location_anchor_strength") or 85),
            "auto_publish": False,
            "facebook_dry_run": True,
        }
        start = await self.call("beauty", "POST", "/api/factory/v2/generate", payload, timeout=120)
        jobs = [str(x.get("job_id")) for x in start.get("jobs") or [] if x.get("job_id")]
        if not jobs:
            raise EngineError(f"Beauty không tạo được Flow job: {start}")
        return {"engine_run_id": str(start.get("run_id") or jobs[0]), "engine_job_ids": jobs}

    async def _beauty_final_asset(self, job_id: str) -> str | None:
        try:
            assets = await self.call("beauty", "GET", f"/api/assets?job_id={job_id}&limit=200", timeout=30)
        except Exception:
            return None
        for a in reversed(assets if isinstance(assets, list) else []):
            if str(a.get("kind") or "") == "final_video" and a.get("local_path"):
                p = Path(str(a["local_path"])).resolve()
                if p.is_file() and p.stat().st_size >= 1024:
                    return str(p)
        return None

    async def wait_beauty(self, job_ids: list[str], timeout_sec: int = 3600) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        remaining = set(job_ids)
        videos: list[str] = []
        raw: dict[str, Any] = {}
        terminal_ok = {"qc_passed", "done", "published", "dry_run_ok"}
        terminal_fail = {"failed", "qc_failed", "partial_failed", "interrupted"}
        while remaining and asyncio.get_running_loop().time() < deadline:
            for jid in list(remaining):
                job = await self.call("beauty", "GET", f"/api/flow/jobs/{jid}", timeout=30)
                raw[jid] = job
                status = str(job.get("status") or "").lower()
                if status in terminal_fail:
                    raise EngineError(str(job.get("error") or f"Beauty {jid} -> {status}"))
                if status in terminal_ok:
                    path = await self._beauty_final_asset(jid)
                    if path:
                        videos.append(path)
                        remaining.remove(jid)
                elif status == "qc":
                    path = await self._beauty_final_asset(jid)
                    if path:
                        # final exists; let QC settle, but do not block forever if engine marks old status
                        pass
            if remaining:
                await asyncio.sleep(2)
        if remaining:
            # last chance: final video asset may exist even if status lagged after a restart
            for jid in list(remaining):
                p = await self._beauty_final_asset(jid)
                if p:
                    videos.append(p)
                    remaining.remove(jid)
        if remaining:
            raise EngineError(f"Beauty timeout, chưa xong: {sorted(remaining)}")
        return {"status": "done", "video_paths": videos, "title": "", "caption": "", "raw": raw}

    # ---------------- parenting ----------------
    async def ensure_parenting_ready(self, config: dict[str, Any], instance_id: str, instance_name: str, timeout_sec: int = 1800) -> str:
        """Create an instance-owned character set so cloned 3.x jobs cannot overwrite each other's prompts/references."""
        flow = _global_flow()
        source_set_id = str(config.get("character_set_id") or "mother_girl_01")
        sets = await self.call("parenting", "GET", "/api/parenting/character-sets", timeout=30)
        source = next((x for x in sets if str(x.get("id")) == source_set_id), None)
        if not source:
            raise EngineError(f"Parenting không có Character Set: {source_set_id}")

        slug = "".join(ch if ch.isalnum() else "_" for ch in instance_id.lower()).strip("_") or "job"
        owned_set_id = f"v28_{slug}_set"
        owned_mother_id = f"v28_{slug}_mother"
        owned_child_id = f"v28_{slug}_child"
        existing_set = next((x for x in sets if str(x.get("id")) == owned_set_id), None) or {}

        roles = [
            ("mother", owned_mother_id, "mother_visual_prompt", "mother_voice_prompt"),
            ("child", owned_child_id, "child_visual_prompt", "child_voice_prompt"),
        ]
        for role, cid, visual_key, voice_key in roles:
            src = source.get(role) or {}
            old = existing_set.get(role) or {}
            visual = _prompt_to_english(visual_key, str(config.get(visual_key) or src.get("visual_prompt") or old.get("visual_prompt") or "").strip())
            voice = _prompt_to_english(voice_key, str(config.get(voice_key) or src.get("voice_prompt") or old.get("voice_prompt") or "").strip())
            if not visual or not voice:
                raise EngineError(f"Parenting thiếu prompt {role}")
            old_visual = str(old.get("visual_prompt") or "").strip()
            src_visual = str(src.get("visual_prompt") or "").strip()
            # A reference generated from an older visual prompt must never survive a prompt edit.
            # Keep an owned reference only when its prompt still matches. On first bootstrap we may
            # reuse the source reference only when the requested prompt is exactly the source prompt.
            if old_visual and old_visual == visual:
                ref = str(old.get("reference_path") or "").strip() or None
            elif not old_visual and src_visual == visual:
                ref = str(src.get("reference_path") or "").strip() or None
            else:
                ref = None
            await self.call(
                "parenting", "POST", "/api/parenting/characters",
                {
                    "id": cid,
                    "name": f"{instance_name} · {'Mẹ' if role == 'mother' else 'Bé'}",
                    "role": src.get("role") or role,
                    "age_label": str(src.get("age_label") or ("30" if role == "mother" else "4")),
                    "visual_prompt": visual,
                    "voice_prompt": voice,
                    "reference_path": ref,
                    "enabled": True,
                },
                timeout=30,
            )

        await self.call(
            "parenting", "POST", "/api/parenting/character-sets",
            {
                "id": owned_set_id,
                "name": f"V2.8 · {instance_name}",
                "mother_character_id": owned_mother_id,
                "child_character_id": owned_child_id,
                "father_character_id": None,
                "enabled": True,
            },
            timeout=30,
        )

        sets = await self.call("parenting", "GET", "/api/parenting/character-sets", timeout=30)
        cset = next((x for x in sets if str(x.get("id")) == owned_set_id), None)
        if not cset:
            raise EngineError(f"Không tạo được Parenting Character Set riêng cho {instance_id}")
        if cset.get("ready"):
            return owned_set_id

        # First run: generate missing per-instance references through the same global Flow worker.
        for role in ("mother", "child"):
            ch = cset.get(role) or {}
            cid = str(ch.get("id") or "")
            if cid and not ch.get("reference_ready"):
                try:
                    await self.call(
                        "parenting", "POST", "/api/parenting/characters/generate",
                        {"character_id": cid, "image_model": _flow_choice(config, flow, "image_model", "imageModel", "Nano Banana 2"),
                         "aspect_ratio": flow.get("aspectRatio") or "9:16"},
                        timeout=60,
                    )
                except Exception as exc:
                    db.log_event(f"Parenting ref bootstrap {cid}: {exc}", level="WARNING", kind="parenting")

        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            sets = await self.call("parenting", "GET", "/api/parenting/character-sets", timeout=30)
            cset = next((x for x in sets if str(x.get("id")) == owned_set_id), None)
            if cset and cset.get("ready"):
                return owned_set_id
            await asyncio.sleep(3)
        raise EngineError(f"Parenting Character Set {owned_set_id} chưa READY")

    async def run_parenting(self, config: dict[str, Any], instance_id: str, instance_name: str) -> dict[str, Any]:
        flow = _global_flow()
        set_id = await self.ensure_parenting_ready(config, instance_id, instance_name)
        job_type = str(config.get("job_type") or config.get("template_mode") or "parenting").strip().lower()
        product_url = str(config.get("product_url") or config.get("shopee_url") or "").strip()
        raw_products = config.get("shopee_products") or []
        if isinstance(raw_products, str):
            try:
                raw_products = json.loads(raw_products)
            except Exception:
                raw_products = []
        product_items = [x for x in raw_products if isinstance(x, dict) and str(x.get("origin_url") or x.get("url") or x.get("product_url") or "").strip()]
        if not product_items and product_url:
            product_items = [{"origin_url": product_url, "affiliate_url": str(config.get("affiliate_url") or "").strip(), "title": ""}]
        product_url = str((product_items[0].get("origin_url") or product_items[0].get("url") or product_items[0].get("product_url")) if product_items else "").strip()
        use_product_pipeline = job_type in {"shopee", "hybrid"} or (product_url and job_type not in {"parenting", "english_context", "mother_teaches_ai"})
        image_model = _flow_choice(config, flow, "image_model", "imageModel", "Nano Banana 2")
        video_model = _flow_choice(config, flow, "video_model", "videoModel", "Veo 3.1 - Fast")
        video_duration = _flow_choice(config, flow, "video_duration", "videoDuration", "8s")
        script_model = str(config.get("model") or _script_model(flow))
        output_duration = _output_duration(config.get("output_duration"))
        if use_product_pipeline and product_items and str(config.get("product_video_mode") or "one_product_per_video") == "one_product_per_video" and len(product_items) > 1:
            run_ids: list[str] = []
            job_ids: list[str] = []
            aff_map: dict[str, str] = {}
            titles: list[str] = []
            for idx, item in enumerate(product_items[:5], 1):
                purl = str(item.get("origin_url") or item.get("url") or item.get("product_url") or "").strip()
                affiliate = str(item.get("affiliate_url") or "").strip()
                if not purl:
                    continue
                if not affiliate:
                    affiliate = str(server_features.get_affiliate(purl) or "").strip()
                if not affiliate:
                    affiliate = purl
                    if bool(config.get("affiliate_required", False)):
                        db.log_event(f"SP #{idx} dùng link gốc {purl} (chưa đổi affiliate)", level="INFO", kind="parenting")
                inspected = await self.call(
                    "parenting", "POST", "/api/parenting/product/inspect",
                    {"url": purl, "model": script_model, "force_refresh": bool(config.get("force_refresh_product", False))},
                    timeout=240,
                )
                product = inspected.get("product") or {}
                product_id = str(product.get("id") or "")
                if not product_id:
                    raise EngineError(f"SP #{idx} inspect không trả product_id")
                titles.append(str(product.get("title") or item.get("title") or product_id)[:90])
                payload = {
                    "product_id": product_id, "character_set_id": set_id,
                    "story_scene_count": int(config.get("story_scene_count") or 0),
                    "total_dialogue_turns": int(config.get("total_dialogue_turns") or 0),
                    "output_duration": output_duration,
                    "angle_hint": str(config.get("angle_hint") or ""), "model": script_model,
                    "product_reveal_scene": int(config.get("product_reveal_scene") or 1),
                    "story_template_id": str(config.get("story_template_id") or "auto"),
                    "image_model": image_model, "video_model": video_model, "video_duration": video_duration,
                    "burn_subtitles": bool(config.get("burn_subtitles", True)),
                    "auto_publish": False, "facebook_page_id": None, "facebook_dry_run": True,
                    "affiliate_url": affiliate,
                }
                start = await self.call("parenting", "POST", "/api/parenting/product/generate", payload, timeout=240)
                rid = str(start.get("run_id") or "")
                jid = str(start.get("job_id") or "")
                if not rid or not jid:
                    raise EngineError(f"Parenting không trả run/job id cho SP #{idx}: {start}")
                run_ids.append(rid); job_ids.append(jid); aff_map[rid] = affiliate
            mid = "multi_parenting_" + uuid.uuid4().hex[:12]
            self.parenting_multi_runs[mid] = {"run_ids": run_ids, "job_ids": job_ids, "affiliate_by_run": aff_map, "titles": titles, "mode": "one_product_per_video"}
            return {"engine_run_id": mid, "engine_run_ids": run_ids, "engine_job_ids": job_ids}
        if use_product_pipeline and product_items and str(config.get("product_video_mode") or "") == "multi_product_one_video" and len(product_items) > 1:
            for x in product_items[:5]:
                purl = str(x.get("origin_url") or x.get("url") or x.get("product_url") or "").strip()
                if not str(x.get("affiliate_url") or "").strip() and purl:
                    cached = server_features.get_affiliate(purl)
                    if cached:
                        x["affiliate_url"] = cached
                    else:
                        x["affiliate_url"] = purl
            names = "; ".join(str(x.get("title") or x.get("origin_url") or x.get("url") or "")[:80] for x in product_items[:5])
            config = dict(config)
            config["angle_hint"] = (str(config.get("angle_hint") or "") + f" Showcase these Shopee products consistently in one connected mini story: {names}. Use one coherent mother-and-child story, reveal each product naturally, avoid duplicate dialogue.").strip()
            config["affiliate_url"] = "\n".join(str(x.get("affiliate_url") or x.get("origin_url") or x.get("url") or "").strip() for x in product_items[:5] if str(x.get("affiliate_url") or x.get("origin_url") or x.get("url") or "").strip())
        if use_product_pipeline and product_url:
            try:
                inspected = await self.call(
                    "parenting", "POST", "/api/parenting/product/inspect",
                    {"url": product_url, "model": script_model, "force_refresh": bool(config.get("force_refresh_product", False))},
                    timeout=240,
                )
                product = inspected.get("product") or {}
                product_id = str(product.get("id") or "")
            except Exception as exc:
                product_id = str(config.get("product_id") or "shopee_dino_car_track")
                db.log_event(f"Parenting product inspect fallback {product_id}: {exc}", level="WARNING", kind="parenting")
            if not product_id:
                raise EngineError("Parenting product inspect không trả product id")
            single_affiliate = str(config.get("affiliate_url") or ((product_items[0].get("affiliate_url") if product_items else "") or "")).strip()
            if not single_affiliate and product_url:
                single_affiliate = str(server_features.get_affiliate(product_url) or "").strip()
            if not single_affiliate:
                single_affiliate = product_url
                if bool(config.get("affiliate_required", False)):
                    db.log_event(f"SP dùng link gốc {product_url} (chưa đổi affiliate)", level="INFO", kind="parenting")
            payload = {
                "product_id": product_id,
                "character_set_id": set_id,
                "story_scene_count": int(config.get("story_scene_count") or 0),
                "total_dialogue_turns": int(config.get("total_dialogue_turns") or 0),
                "output_duration": output_duration,
                "angle_hint": str(config.get("angle_hint") or ""),
                "model": script_model,
                "product_reveal_scene": int(config.get("product_reveal_scene") or 1),
                "story_template_id": str(config.get("story_template_id") or "auto"),
                "image_model": image_model,
                "video_model": video_model,
                "video_duration": video_duration,
                "burn_subtitles": bool(config.get("burn_subtitles", True)),
                "auto_publish": False,
                "facebook_page_id": None,
                "facebook_dry_run": True,
                "affiliate_url": single_affiliate,
            }
            start = await self.call("parenting", "POST", "/api/parenting/product/generate", payload, timeout=240)
        else:
            payload = {
                "character_set_id": set_id,
                "topic": config.get("topic") or "Một tình huống đời thường giúp cha mẹ hiểu cảm xúc của con nhỏ",
                "scene_count": max(1, min(8, int(config.get("scene_count") or 4))),
                "dialogue_turns_per_scene": max(1, min(12, int(config.get("dialogue_turns_per_scene") or 4))),
                "model": script_model,
                "tone": config.get("tone") or "ấm áp, tự nhiên, hữu ích, không phán xét",
                "template_mode": job_type if job_type in {"parenting", "english_context", "mother_teaches_ai"} else "parenting",
                "image_model": image_model,
                "video_model": video_model,
                "video_duration": video_duration,
                "burn_subtitles": bool(config.get("burn_subtitles", True)),
                "auto_publish": False,
                "facebook_page_id": None,
                "facebook_dry_run": True,
                "continuation_mode": ("off" if str(flow.get("videoExtendFactor") or "x1") == "x1" else str(flow.get("videoExtendFactor") or "x1")),
            }
            start = await self.call("parenting", "POST", "/api/parenting/generate", payload, timeout=180)
        rid = str(start.get("run_id") or "")
        jid = str(start.get("job_id") or "")
        if not rid or not jid:
            raise EngineError(f"Parenting không trả run/job id: {start}")
        return {"engine_run_id": rid, "engine_job_ids": [jid]}

    async def wait_parenting(self, run_id: str, timeout_sec: int = 4200) -> dict[str, Any]:
        if run_id in self.parenting_multi_runs:
            meta = self.parenting_multi_runs.get(run_id) or {}
            videos: list[str] = []
            captions: list[str] = []
            raws: list[Any] = []
            for rid in meta.get("run_ids") or []:
                one = await self.wait_parenting(str(rid), timeout_sec=timeout_sec)
                videos.extend([str(x) for x in one.get("video_paths") or [] if x])
                aff = str((meta.get("affiliate_by_run") or {}).get(str(rid)) or "").strip()
                cap = str(one.get("caption") or "").strip()
                if aff:
                    cap = (cap + "\n\nXem sản phẩm: " + aff).strip()
                captions.append(cap)
                raws.append(one.get("raw") or {})
            return {"status": "done", "video_paths": videos, "title": "Shopee Product Basket", "caption": "\n\n---\n\n".join([x for x in captions if x])[:1800], "raw": {"multi": meta, "runs": raws}}
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            run = await self.call("parenting", "GET", f"/api/parenting/runs/{run_id}", timeout=30)
            status = str(run.get("status") or "").lower()
            if status == "done":
                path = str(run.get("final_path") or "")
                if not path:
                    raise EngineError("Parenting DONE nhưng thiếu final_path")
                path = str(Path(path).resolve())
                if not Path(path).is_file() or Path(path).stat().st_size < 1024:
                    raise EngineError(f"Parenting DONE nhưng file output không tồn tại/rỗng: {path}")
                plan = run.get("plan") or {}
                return {
                    "status": "done", "video_paths": [path],
                    "title": str(run.get("title") or plan.get("title") or ""),
                    "caption": (str(plan.get("hook") or run.get("topic") or "") + (("\n\nXem sản phẩm: " + str((plan.get("affiliate_url") or "")).strip()) if str((plan.get("affiliate_url") or "")).strip() else "")),
                    "raw": run,
                }
            if status == "failed":
                raise EngineError(str(run.get("error") or "Parenting run failed"))
            await asyncio.sleep(3)
        raise EngineError("Parenting render timeout")
