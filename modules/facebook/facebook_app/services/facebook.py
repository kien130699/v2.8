from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import requests

from facebook_app.config import settings

LogFn = Callable[[str, str], Awaitable[None]]


class FacebookPublisher:
    def _graph(self, path: str) -> str:
        base = "https://graph.facebook.com"
        if settings.fb_graph_version:
            return f"{base}/{settings.fb_graph_version}/{path.lstrip('/')}"
        return f"{base}/{path.lstrip('/')}"

    def token_for(self, page: dict[str, Any]) -> str:
        key = page.get("token_env_key") or ""
        return os.getenv(key, "") if key else ""

    def test_page(self, page: dict[str, Any]) -> dict[str, Any]:
        token = self.token_for(page)
        if not token:
            return {"ok": False, "error": f"Chưa cấu hình env {page.get('token_env_key','')}"}
        r = requests.get(self._graph("me"), params={"fields": "id,name", "access_token": token}, timeout=30)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text[:500]}
        return {"ok": r.ok, "status_code": r.status_code, "data": data}

    async def publish_reel(self, page: dict[str, Any], video_path: Path, title: str, description: str, log: LogFn) -> dict[str, Any]:
        token = self.token_for(page)
        if not token:
            raise RuntimeError(f"Thiếu Facebook token env: {page.get('token_env_key')}")
        if not video_path.exists():
            raise RuntimeError(f"Không thấy video: {video_path}")

        await log("INFO", "Facebook: create Reel upload session")
        start = requests.post(
            self._graph("me/video_reels"),
            params={"access_token": token, "upload_phase": "start"},
            timeout=60,
        )
        start.raise_for_status()
        start_data = start.json()
        video_id = str(start_data.get("video_id") or "")
        if not video_id:
            raise RuntimeError(f"Facebook start không trả video_id: {start_data}")

        upload_url = start_data.get("upload_url")
        if not upload_url:
            version = f"/{settings.fb_graph_version}" if settings.fb_graph_version else ""
            upload_url = f"https://rupload.facebook.com/video-upload{version}/{video_id}"

        size = video_path.stat().st_size
        await log("INFO", f"Facebook: upload binary {size/1024/1024:.1f} MB")
        with video_path.open("rb") as f:
            up = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(size),
                    "Content-Type": "application/octet-stream",
                },
                data=f,
                timeout=(30, 900),
            )
        up.raise_for_status()

        await log("INFO", "Facebook: publish Reel")
        finish = requests.post(
            self._graph("me/video_reels"),
            params={
                "access_token": token,
                "video_id": video_id,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "title": title[:255],
                "description": description[:5000],
            },
            timeout=60,
        )
        finish.raise_for_status()

        # Poll status with a bounded wait. If status schema changes, keep raw data.
        last_status: dict[str, Any] = {}
        for _ in range(18):
            await asyncio_sleep(10)
            r = requests.get(
                self._graph(video_id),
                params={"fields": "status", "access_token": token},
                timeout=30,
            )
            if not r.ok:
                continue
            last_status = r.json()
            status = last_status.get("status") or {}
            video_status = str(status.get("video_status") or status.get("processing_phase", {}).get("status") or "").lower()
            publishing = str(status.get("publishing_phase", {}).get("status") or "").lower()
            if video_status in {"ready", "complete", "completed"} or publishing in {"complete", "completed", "published"}:
                break
            if video_status in {"error", "failed"} or publishing in {"error", "failed"}:
                raise RuntimeError(f"Facebook processing failed: {last_status}")
        return {"video_id": video_id, "start": start_data, "finish": finish.json(), "status": last_status}


async def asyncio_sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


facebook = FacebookPublisher()
