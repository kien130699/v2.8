from __future__ import annotations

import asyncio
import json
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from facebook_app.config import settings
from facebook_app.db import get_setting, now_iso, set_setting
from facebook_app.events import events
from facebook_app.repository import (
    add_log,
    claim_next_publish_job,
    claim_next_render_job,
    create_test_job,
    dashboard,
    ensure_job,
    factory_state,
    get_job,
    get_page,
    list_pages,
    retry_job,
    set_factory_state,
    update_job,
)
from facebook_app.services.content import build_script
from facebook_app.services.facebook import facebook
from facebook_app.services.video_engine import video_engine


class FactoryManager:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []
        self.started = False
        self._stop_event = asyncio.Event()
        self.test_tasks: set[asyncio.Task] = set()

    async def boot(self) -> None:
        if self.started:
            return
        self.started = True
        self._stop_event.clear()
        self.tasks = [
            asyncio.create_task(self.scheduler_loop(), name="factory-scheduler"),
            asyncio.create_task(self.render_loop(), name="factory-render"),
            asyncio.create_task(self.publish_loop(), name="factory-publish"),
        ]
        if settings.auto_start:
            await self.start()

    async def shutdown(self) -> None:
        self._stop_event.set()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        for t in list(self.test_tasks):
            t.cancel()
        if self.test_tasks:
            await asyncio.gather(*self.test_tasks, return_exceptions=True)
        self.test_tasks.clear()
        self.started = False

    async def _emit(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        await events.publish({"type": kind, "ts": now_iso(), **(payload or {})})

    async def _log(self, job_id: str, level: str, message: str) -> None:
        add_log(job_id, message, level)
        await self._emit("job_log", {"job_id": job_id, "level": level, "message": message})

    async def start(self) -> None:
        set_factory_state("RUNNING")
        await self.ensure_today_plan()
        await self._emit("factory_state", {"state": "RUNNING"})

    async def pause(self) -> None:
        set_factory_state("PAUSED")
        await self._emit("factory_state", {"state": "PAUSED"})

    async def stop(self) -> None:
        set_factory_state("STOPPED")
        await self._emit("factory_state", {"state": "STOPPED"})

    async def ensure_today_plan(self) -> int:
        today = datetime.now(settings.tz).date()
        count = 0
        for p in list_pages():
            if not p.get("enabled"):
                continue
            if not p.get("page_id") and not settings.allow_render_without_fb_page:
                continue
            posts = max(1, min(int(p.get("posts_per_day") or 2), 4))
            slots = [p.get("slot1") or "10:00", p.get("slot2") or "19:00"]
            while len(slots) < posts:
                slots.append("21:00")
            for idx in range(posts):
                hh, mm = [int(x) for x in slots[idx].split(":")[:2]]
                scheduled = datetime.combine(today, dtime(hh, mm), tzinfo=settings.tz)
                ensure_job(p, today, idx + 1, scheduled)
                count += 1
        await self._emit("plan_ready", {"count": count, "date": today.isoformat()})
        return count

    async def scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if factory_state() == "RUNNING":
                    await self.ensure_today_plan()
            except Exception as exc:
                await self._emit("system_error", {"where": "scheduler", "message": str(exc)})
            await asyncio.sleep(settings.scheduler_interval_sec)

    async def render_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if factory_state() != "RUNNING":
                    await asyncio.sleep(1)
                    continue
                job = claim_next_render_job()
                if not job:
                    await asyncio.sleep(1)
                    continue
                await self.process_render(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._emit("system_error", {"where": "render_loop", "message": str(exc)})
                await asyncio.sleep(2)

    async def process_render(self, job: dict[str, Any], model_override: str | None = None, test_mode: bool = False, v28_config: dict[str, Any] | None = None) -> None:
        job_id = job["id"]
        try:
            page = get_page(int(job["page_row_id"]))
            if not page:
                raise RuntimeError("Page không còn tồn tại")
            await self._log(job_id, "INFO", f"SCRIPT → page={page['name']} theme={page['theme']}")
            script = await asyncio.to_thread(build_script, page, model_override, v28_config)
            script.setdefault("factory_meta", {})["business_date"] = job["business_date"]
            script["factory_meta"]["job_id"] = job_id
            script["factory_meta"]["page_name"] = page["name"]
            topic = script.get("factory_meta", {}).get("topic", script.get("title", ""))
            update_job(job_id, topic=topic, title=script.get("title", ""), script_json=script, status="RENDERING", step="VIDEO_ENGINE", progress=20)
            await self._emit("job_update", {"job_id": job_id})

            async def engine_log(level: str, message: str) -> None:
                await self._log(job_id, level, message)

            final, sources = await video_engine.render(job_id, script, engine_log)
            update_job(
                job_id,
                status="TEST_READY" if test_mode else "READY",
                step="READY",
                progress=85,
                output_path=str(final),
                sources_path=str(sources) if sources else "",
                error="",
            )
            await self._log(job_id, "INFO", "TEST READY → preview only, không publish" if test_mode else "READY → chờ lịch Facebook")
            await self._emit("job_update", {"job_id": job_id})
        except Exception as exc:
            update_job(job_id, status="TEST_FAILED" if test_mode else "FAILED", step="FAILED", error=str(exc), progress=0)
            await self._log(job_id, "ERROR", str(exc))
            await self._emit("job_update", {"job_id": job_id})

    async def create_test_video(self, page_row_id: int | None = None, model: str | None = None, v28_config: dict[str, Any] | None = None) -> dict[str, Any]:
        pages = [p for p in list_pages() if p.get("enabled")]
        if not pages:
            raise RuntimeError("Không có Page nào đang bật để lấy profile test")
        page = None
        if page_row_id is not None:
            page = get_page(int(page_row_id))
            if not page:
                raise RuntimeError("Page test không tồn tại")
        else:
            page = pages[0]
        job_id = create_test_job(int(page["id"]))
        job = get_job(job_id)
        if not job:
            raise RuntimeError("Không tạo được test job")
        add_log(job_id, f"TEST VIDEO → page={page['name']} model={model or 'selected/default'}", "INFO")
        task = asyncio.create_task(self.process_render(job, model_override=model, test_mode=True, v28_config=v28_config), name=f"test-video-{job_id}")
        self.test_tasks.add(task)
        task.add_done_callback(self.test_tasks.discard)
        await self._emit("job_update", {"job_id": job_id})
        return {"ok": True, "job_id": job_id, "page": page["name"], "model": model or ""}

    async def publish_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if factory_state() != "RUNNING" or get_setting("auto_publish", "0") != "1":
                    await asyncio.sleep(2)
                    continue
                job = claim_next_publish_job(datetime.now(settings.tz).isoformat(timespec="seconds"))
                if not job:
                    await asyncio.sleep(5)
                    continue
                await self.process_publish(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._emit("system_error", {"where": "publish_loop", "message": str(exc)})
                await asyncio.sleep(5)

    async def process_publish(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        try:
            page = get_page(int(job["page_row_id"]))
            if not page:
                raise RuntimeError("Page không còn tồn tại")
            if not page.get("page_id"):
                raise RuntimeError("Page chưa có Facebook Page ID")
            video = Path(job.get("output_path") or "")
            script = json.loads(job.get("script_json") or "{}")
            caption = script.get("factory_meta", {}).get("caption_vi") or job.get("topic") or ""

            async def fb_log(level: str, message: str) -> None:
                await self._log(job_id, level, message)

            result = await facebook.publish_reel(page, video, job.get("title") or "", caption, fb_log)
            update_job(
                job_id,
                status="PUBLISHED",
                step="DONE",
                progress=100,
                facebook_video_id=str(result.get("video_id") or ""),
                facebook_status=json.dumps(result.get("status") or {}, ensure_ascii=False)[:4000],
                error="",
            )
            await self._log(job_id, "INFO", f"PUBLISHED → video_id={result.get('video_id')}")
            await self._emit("job_update", {"job_id": job_id})
        except Exception as exc:
            # Keep rendered file; retry publishing can be added without rerender.
            update_job(job_id, status="READY", step="PUBLISH_FAILED", error=str(exc), progress=85)
            await self._log(job_id, "ERROR", f"Facebook publish lỗi: {exc}")
            await self._emit("job_update", {"job_id": job_id})


factory = FactoryManager()
