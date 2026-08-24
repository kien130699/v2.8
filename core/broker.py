from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import websockets
from fastapi import HTTPException, WebSocket

from . import db

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "data" / "downloads"
DOWNLOADS.mkdir(parents=True, exist_ok=True)

IMAGE_MODELS = [
    "Nano Banana 2",
    "Nano Banana 2 Lite",
    "Nano Banana Pro",
]
VIDEO_MODELS = [
    "Veo 3.1 - Fast",
    "Veo 3.1 - Lite",
    "Veo 3.1 - Lite [Lower Priority]",
    "Veo 3.1 - Quality",
    "Gemini Omni Flash",
]

MIN_EXTENSION_VERSION = "14.7.37"

DURABLE_SOURCE_TYPES = {
    "FLOW_JOB_RESULT", "FLOW_JOB_REJECTED", "FLOW_JOB_INTERRUPTED",
    "VIDEO_FILE_READY", "VIDEO_FILE_ERROR", "VIDEO_DOWNLOAD_SUMMARY",
    "IMAGE_FILE_READY", "IMAGE_FILE_ERROR", "REFERENCE_MEDIA_REPLACED",
}

DEFAULT_SETTINGS = {
    "prioritySource": "fifo",
    # ONE Flow extension = these execution settings are GLOBAL, never per Job.
    "imageModel": "Nano Banana 2",
    "videoModel": "Veo 3.1 - Fast",
    "imageConcurrency": 9,
    "videoConcurrency": 4,
    "aspectRatio": "9:16",
    "imageOutputs": "x1",
    "videoDuration": "8s",
    "videoOutputs": "x1",
    "videoExtendFactor": "x1",
    "videoExtendPrompt": "Continue naturally from the previous shot. Keep the same person, product, lighting, location and camera style. Add new natural product-review motion and angles. Do not repeat the opening shot or redesign the product.",
    "scriptAiProvider": "9router",
    "scriptAiModel": "ag/gemini-3.1-pro-high",
    "scriptFallbackModel": "cx/gpt-5.5",
    "submitPolicy": "VIDEO_LIGHT",
    "maxSubmitsPerMinute": 6,
    "submitGapMs": 1000,
    "imageTimeoutSec": 300,
    "videoTimeoutSec": 900,
    "systemicFailureLimit": 3,
    "autoDownloadVideo": True,
    "clearComposerBeforeRun": True,
    "clearPromptBeforeRun": True,
    "clearImagesBeforeRun": True,
}


def deep_merge(a: dict[str, Any], b: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _to_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    return default


def _sanitize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    s = deep_merge(DEFAULT_SETTINGS, raw or {})
    if s.get("prioritySource") not in {"fifo", "beauty", "parenting"}:
        s["prioritySource"] = "fifo"
    if s.get("imageModel") not in IMAGE_MODELS:
        s["imageModel"] = DEFAULT_SETTINGS["imageModel"]
    if s.get("videoModel") not in VIDEO_MODELS:
        s["videoModel"] = DEFAULT_SETTINGS["videoModel"]
    s["imageConcurrency"] = _to_int(s.get("imageConcurrency"), 9, 1, 10)
    s["videoConcurrency"] = _to_int(s.get("videoConcurrency"), 4, 1, 10)
    if s.get("aspectRatio") not in {"9:16", "16:9", "1:1"}:
        s["aspectRatio"] = "9:16"
    if s.get("imageOutputs") not in {"x1", "x2", "x3", "x4"}:
        s["imageOutputs"] = "x1"
    if s.get("videoDuration") not in {"4s", "6s", "8s"}:
        s["videoDuration"] = "8s"
    if s.get("videoOutputs") not in {"x1", "x2", "x3", "x4"}:
        s["videoOutputs"] = "x1"
    if s.get("videoExtendFactor") not in {"x1", "x2", "x3", "x4"}:
        s["videoExtendFactor"] = "x1"
    s["videoExtendPrompt"] = str(s.get("videoExtendPrompt") or DEFAULT_SETTINGS["videoExtendPrompt"]).strip()[:4000]
    s["scriptAiProvider"] = "9router"
    for key in ("scriptAiModel", "scriptFallbackModel"):
        value = str(s.get(key) or DEFAULT_SETTINGS[key]).strip()
        s[key] = value[:160] or DEFAULT_SETTINGS[key]
    if s.get("submitPolicy") not in {"VIDEO_LIGHT", "GLOBAL_FIFO"}:
        s["submitPolicy"] = "VIDEO_LIGHT"
    s["maxSubmitsPerMinute"] = _to_int(s.get("maxSubmitsPerMinute"), 6, 0, 60)
    s["submitGapMs"] = _to_int(s.get("submitGapMs"), 1000, 0, 60000)
    s["imageTimeoutSec"] = _to_int(s.get("imageTimeoutSec"), 300, 30, 3600)
    s["videoTimeoutSec"] = _to_int(s.get("videoTimeoutSec"), 900, 60, 7200)
    s["systemicFailureLimit"] = _to_int(s.get("systemicFailureLimit"), 3, 2, 5)
    for key in ("autoDownloadVideo", "clearComposerBeforeRun", "clearPromptBeforeRun", "clearImagesBeforeRun"):
        s[key] = _to_bool(s.get(key), bool(DEFAULT_SETTINGS[key]))
    return s


def load_settings() -> dict[str, Any]:
    raw = db.get_setting("flow_settings", {}, json_value=True)
    return _sanitize_settings(raw if isinstance(raw, dict) else {})


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    s = _sanitize_settings(deep_merge(load_settings(), patch))
    db.set_setting("flow_settings", s)
    return s


def version_key(v: str | None) -> tuple[int, ...]:
    parts: list[int] = []
    for x in str(v or "").split("."):
        digits = "".join(c for c in x if c.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0, 0])[:4])


@dataclass
class SourceBridge:
    name: str
    url: str
    version: str = "14.7.0"
    ws: Any = None
    connected: bool = False
    last_error: str | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: Any) -> bool:
        if not self.ws:
            return False
        try:
            raw = payload if isinstance(payload, (str, bytes, bytearray)) else json.dumps(payload, ensure_ascii=False)
            async with self.send_lock:
                await self.ws.send(raw)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False


@dataclass
class QueueItem:
    queue_id: str
    source: str
    message: dict[str, Any]
    enqueued_at: float
    status: str = "WAITING"
    started_at: float | None = None
    finished_at: float | None = None
    result: str | None = None

    @property
    def job_id(self) -> str:
        return str(self.message.get("jobId") or "")


class FlowBroker:
    """One central queue in front of exactly one Chrome Flow extension."""

    def __init__(self, port: int = 3000) -> None:
        self.port = port
        base = f"ws://127.0.0.1:{port}"
        self.sources: dict[str, SourceBridge] = {
            "beauty": SourceBridge("beauty", base + "/engine/beauty/ws"),
            "parenting": SourceBridge("parenting", base + "/engine/parenting/ws"),
        }
        self.pending: deque[QueueItem] = deque()
        self.recent: deque[QueueItem] = deque(maxlen=300)
        self.active: QueueItem | None = None
        self.extension: WebSocket | None = None
        self.extension_meta: dict[str, Any] = {}
        self.extension_last_seen: float = 0.0
        self.extension_last_ping: float = 0.0
        self.event = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.job_routes: dict[str, str] = {}
        self.request_routes: dict[str, str] = {}
        self.control_waiters: dict[str, asyncio.Future] = {}
        self.rpc_waiters: dict[str, asyncio.Future] = {}
        self.job_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.download_tasks: set[asyncio.Task] = set()
        self.duplicate_rejections = 0
        self.last_duplicate: dict[str, Any] | None = None
        self.active_stale_resets = 0
        self.last_active_stale: dict[str, Any] | None = None

    @staticmethod
    def _snap(x: QueueItem) -> dict[str, Any]:
        return {
            "queueId": x.queue_id, "jobId": x.job_id, "source": x.source, "status": x.status,
            "enqueuedAt": x.enqueued_at, "startedAt": x.started_at, "finishedAt": x.finished_at,
            "result": x.result,
        }

    def extension_ready(self, max_age: float = 35.0) -> bool:
        ws = self.extension
        if not ws or not self.extension_meta or not self.extension_last_seen:
            return False
        try:
            if getattr(getattr(ws, "client_state", None), "name", "CONNECTED") != "CONNECTED":
                return False
            if getattr(getattr(ws, "application_state", None), "name", "CONNECTED") != "CONNECTED":
                return False
        except Exception:
            return False
        if version_key(str(self.extension_meta.get("version") or "0")) < version_key(MIN_EXTENSION_VERSION):
            return False
        return (time.time() - self.extension_last_seen) <= max_age

    def snapshot(self) -> dict[str, Any]:
        version = str(self.extension_meta.get("version") or "")
        compatible = bool(version and version_key(version) >= version_key(MIN_EXTENSION_VERSION))
        # Do not expose/store a giant runtime cache in status polling; it can contain scene data.
        ext = dict(self.extension_meta)
        runtime = ext.pop("runtime", None)
        if isinstance(runtime, dict):
            ext["runtime"] = {
                "running": bool(runtime.get("running")),
                "serverJobId": runtime.get("serverJobId"),
                "progressLabel": runtime.get("progressLabel"),
            }
        return {
            "extensionConnected": self.extension_ready(),
            "extensionCompatible": compatible,
            "minimumExtensionVersion": MIN_EXTENSION_VERSION,
            "extension": {**ext, "lastSeen": self.extension_last_seen or None},
            "duplicateRejections": self.duplicate_rejections,
            "lastDuplicate": self.last_duplicate,
            "activeStaleResets": self.active_stale_resets,
            "lastActiveStale": self.last_active_stale,
            "active": self._snap(self.active) if self.active else None,
            "pending": [self._snap(x) for x in self.pending],
            "recent": [self._snap(x) for x in list(self.recent)[-50:]][::-1],
            "sources": {k: {"connected": v.connected, "error": v.last_error, "protocolVersion": v.version} for k, v in self.sources.items()},
        }

    def enqueue(self, source: str, msg: dict[str, Any]) -> QueueItem:
        jid = str(msg.get("jobId") or "")
        for x in list(self.pending) + ([self.active] if self.active else []):
            if x and x.source == source and jid and x.job_id == jid:
                return x
        x = QueueItem(uuid.uuid4().hex[:12], source, dict(msg), time.time())
        self.pending.append(x)
        if jid:
            self.job_routes[jid] = source
        db.log_event(f"Flow enqueue {source}/{jid}", kind="flow", payload={"source": source, "job_id": jid})
        self.event.set()
        return x

    def _choose(self) -> QueueItem | None:
        if not self.pending:
            return None
        pref = load_settings().get("prioritySource", "fifo")
        if pref != "fifo":
            for i, x in enumerate(self.pending):
                if x.source == pref:
                    self.pending.rotate(-i)
                    y = self.pending.popleft()
                    self.pending.rotate(i)
                    return y
        return self.pending.popleft()

    def _apply_globals(self, msg: dict[str, Any], source: str) -> dict[str, Any]:
        s = load_settings()
        m = json.loads(json.dumps(msg))
        over = {
            "imageModel": s["imageModel"],
            "videoModel": s["videoModel"],
            "imageConcurrency": s["imageConcurrency"],
            "videoConcurrency": s["videoConcurrency"],
            "aspectRatio": s["aspectRatio"],
            "imageOutputs": s["imageOutputs"],
            "videoDuration": s["videoDuration"],
            "videoOutputs": s["videoOutputs"],
            "videoExtendFactor": s["videoExtendFactor"],
            "videoExtendPrompt": s["videoExtendPrompt"],
            "submitPolicy": s["submitPolicy"],
            "maxSubmitsPerMinute": s["maxSubmitsPerMinute"],
            "submitGapMs": s["submitGapMs"],
            "imageTimeoutSec": s["imageTimeoutSec"],
            "videoTimeoutSec": s["videoTimeoutSec"],
            "systemicFailureLimit": s["systemicFailureLimit"],
            "autoDownloadVideo": bool(s["autoDownloadVideo"]),
            "clearComposerBeforeRun": bool(s["clearComposerBeforeRun"]),
            "clearPromptBeforeRun": bool(s["clearPromptBeforeRun"]),
            "clearImagesBeforeRun": bool(s["clearImagesBeforeRun"]),
        }
        existing_flow = m.get("flow") if isinstance(m.get("flow"), dict) else {}
        merged_flow = dict(existing_flow)
        for key, value in over.items():
            if merged_flow.get(key) in (None, ""):
                merged_flow[key] = value
        m["flow"] = merged_flow
        if isinstance(m.get("resolvedSettings"), dict):
            existing_resolved = m["resolvedSettings"].get("flow") if isinstance(m["resolvedSettings"].get("flow"), dict) else {}
            merged_resolved = dict(existing_resolved)
            for key, value in over.items():
                if merged_resolved.get(key) in (None, ""):
                    merged_resolved[key] = value
            m["resolvedSettings"]["flow"] = merged_resolved
        m["queueMeta"] = {"source": source, "masterPort": self.port, "v28": True}
        return m

    def _active_stale_reason(self, now: float | None = None) -> str | None:
        x = self.active
        if not x or not x.started_at:
            return None
        now = now or time.time()
        age = now - x.started_at
        settings = load_settings()
        hard_limit = max(float(settings.get("videoTimeoutSec") or 1200) * 1.5, 1800.0)
        runtime = self.extension_meta.get("runtime") if isinstance(self.extension_meta, dict) else {}
        ext_running = bool(runtime.get("running")) if isinstance(runtime, dict) else False
        ext_job = str(runtime.get("serverJobId") or "") if isinstance(runtime, dict) else ""
        if age >= hard_limit:
            return f"active stale {int(age)}s > {int(hard_limit)}s"
        # If extension is reporting running for an OLD / DIFFERENT job than current active:
        if ext_job and ext_job != x.job_id and age >= 15:
            return f"active stale mismatch: extension running old job {ext_job} while broker active is {x.job_id}"
        # Check if worker is running but progress hasn't updated for longer than videoTimeoutSec
        last_prog = getattr(self, "active_last_progress_at", None) or x.started_at
        progress_stale_limit = max(float(settings.get("videoTimeoutSec") or 900), 300.0)
        if (now - last_prog) >= progress_stale_limit:
            return f"active progress frozen: no progress update for {int(now - last_prog)}s (limit={int(progress_stale_limit)}s)"
        # If extension is still executing this exact job and progress is fresh, allow it to continue
        if ext_job == x.job_id and ext_running:
            return None
        if age >= 60 and self.extension_ready(max_age=120) and not ext_running:
            if not ext_job:
                return f"active stale {int(age)}s while extension idle"
            if ext_job == x.job_id:
                return f"active stale {int(age)}s while extension idle but keeps stale jobId"
        return None

    def reset_queue(self) -> dict[str, Any]:
        """Manually clear blocked active job and prompt extension to reset."""
        old_active = self.active
        self.active = None
        if old_active:
            old_active.status = "FAILED"
            old_active.finished_at = time.time()
            old_active.result = "manually_reset"
            self.recent.append(old_active)
            self.job_routes.pop(old_active.job_id, None)
        self.event.set()
        asyncio.create_task(self._send_ext({"type": "STOP_ALL"}))
        return {"ok": True, "cleared_active": old_active.job_id if old_active else None}

    async def _fail_active_stale(self, reason: str) -> None:
        done = self.active
        if not done:
            return
        done.status = "FAILED"
        done.finished_at = time.time()
        done.result = reason
        self.recent.append(done)
        self.job_routes.pop(done.job_id, None)
        self.active = None
        self.active_last_progress_at = None
        self.active_stale_resets += 1
        self.last_active_stale = {
            "queueId": done.queue_id,
            "jobId": done.job_id,
            "source": done.source,
            "reason": reason,
            "finishedAt": done.finished_at,
        }
        db.log_event(f"Flow stale reset {done.source}/{done.job_id}: {reason}", level="WARNING", kind="flow")
        fut = self.job_waiters.pop(done.job_id, None)
        if fut and not fut.done():
            fut.set_result({
                "ok": False,
                "jobId": done.job_id,
                "video_paths": self.get_job_clips(done.job_id),
                "status": "FAILED",
                "error": f"Stale timeout: {reason}",
            })
        await self._forward_source(done.source, {
            "type": "FLOW_JOB_INTERRUPTED",
            "ok": False,
            "jobId": done.job_id,
            "error": reason,
            "queueId": done.queue_id,
            "source": done.source,
        })
        if self.extension:
            run_id = str(done.message.get("runId") or done.job_id)
            attempt_id = str(done.message.get("attemptId") or done.message.get("attempt") or "1")
            await self._send_ext({
                "type": "CANCEL_JOB",
                "jobId": done.job_id,
                "runId": run_id,
                "attemptId": attempt_id,
                "reason": reason,
            })
            await self._send_ext({"type": "FLOW_CONTROL", "action": "stop", "reason": reason, "jobId": done.job_id, "attemptId": attempt_id})
        self.event.set()

    async def _send_ext(self, msg: dict[str, Any]) -> bool:
        if not self.extension:
            return False
        try:
            async with self.send_lock:
                await self.extension.send_json(msg)
            return True
        except Exception as exc:
            db.log_event(f"Flow send lỗi: {exc}", level="WARNING", kind="flow")
            self.event.set()
            return False

    async def scheduler(self) -> None:
        while True:
            try:
                now = time.time()
                if self.extension:
                    stale_limit = 900 if self.active else 90
                    if self.extension_last_seen and now - self.extension_last_seen > stale_limit:
                        stale = self.extension
                        db.log_event(f"Flow worker stale >{stale_limit}s; dong socket de extension reconnect", level="WARNING", kind="flow")
                        try:
                            await stale.close(code=4010, reason="heartbeat timeout")
                        except Exception:
                            pass
                        if self.extension is stale:
                            self.extension = None
                            self.extension_meta = {}
                        self.event.set()
                    elif now - self.extension_last_ping >= 8:
                        self.extension_last_ping = now
                        await self._send_ext({"type": "PING", "ts": int(now * 1000)})
                if self.active:
                    reason = self._active_stale_reason(now)
                    if reason:
                        await self._fail_active_stale(reason)
                if self.extension_ready() and not self.active:
                    x = self._choose()
                    if x:
                        self.active = x
                        self.active_last_progress_at = time.time()
                        x.status = "RUNNING"
                        x.started_at = time.time()
                        out = x.message if str(x.message.get("type") or "") == "DOWNLOAD_MEDIA_FILES" else self._apply_globals(x.message, x.source)
                        if not await self._send_ext(out):
                            x.status = "WAITING"
                            x.started_at = None
                            self.active_last_progress_at = None
                            self.pending.appendleft(x)
                            self.active = None
                self.event.clear()
                try:
                    await asyncio.wait_for(self.event.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.log_event(f"Flow broker scheduler: {exc}", level="ERROR", kind="flow")
                await asyncio.sleep(0.5)

    def _route_source(self, msg: dict[str, Any]) -> str | None:
        jid = str(msg.get("jobId") or "")
        if self.active and jid and jid == self.active.job_id:
            return self.active.source
        if jid:
            return self.job_routes.get(jid)
        rid = str(msg.get("requestId") or "")
        if rid:
            return self.request_routes.get(rid)
        return self.active.source if self.active else None

    def _matches_active(self, msg: dict[str, Any]) -> bool:
        if not self.active:
            return False
        jid = str(msg.get("jobId") or "")
        return bool(jid and jid == self.active.job_id)

    def _lease_meta(self, scene_id: int, item: QueueItem | None = None) -> dict[str, Any]:
        target = item or self.active
        if not target:
            return {}
        m = target.message
        sid = str(scene_id)
        lease = None
        for s in m.get("scenes") or []:
            if str(s.get("sceneId")) == sid:
                lease = s.get("leaseId")
                break
        out: dict[str, Any] = {}
        if m.get("runGeneration") is not None:
            out["runGeneration"] = m.get("runGeneration")
        if m.get("commandId"):
            out["commandId"] = m.get("commandId")
        if lease:
            out["leaseId"] = lease
        return out

    def _queue_source_outbox(self, source: str, msg: dict[str, Any], error: str = "bridge offline") -> None:
        typ = str(msg.get("type") or "")
        try:
            raw = db.dumps(msg)
            if len(raw.encode("utf-8")) > 8 * 1024 * 1024:
                db.log_event(f"Flow outbox bỏ message quá lớn {source}/{typ}", level="ERROR", kind="flow")
                return
            oid = "fo_" + uuid.uuid4().hex[:16]
            parts = [source, typ, str(msg.get("jobId") or ""), str(msg.get("sceneId") or ""), str(msg.get("mediaId") or ""), str(msg.get("commandId") or "")]
            dedupe_key = ":".join(parts) if any(parts[2:]) else None
            ts = db.now_iso()
            with db.connect() as c:
                c.execute(
                    "INSERT OR IGNORE INTO flow_outbox(id,source,message_type,dedupe_key,payload_json,attempts,last_error,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
                    (oid, source, typ, dedupe_key, raw, str(error)[:2000], ts, ts),
                )
            db.log_event(f"Flow outbox +1 {source}/{typ}", level="WARNING", kind="flow")
        except Exception as exc:
            db.log_event(f"Flow outbox write lỗi {source}/{typ}: {exc}", level="ERROR", kind="flow")

    async def _flush_source_outbox(self, source: str) -> int:
        b = self.sources.get(source)
        if not b or not b.connected or not b.ws:
            return 0
        sent = 0
        rows = db.rows("SELECT * FROM flow_outbox WHERE source=? ORDER BY created_at,id LIMIT 200", (source,))
        for row in rows:
            msg = db.loads(row.get("payload_json"), {})
            if not isinstance(msg, dict):
                db.execute("DELETE FROM flow_outbox WHERE id=?", (row["id"],))
                continue
            ok = await b.send(msg)
            if not ok:
                with db.connect() as c:
                    c.execute("UPDATE flow_outbox SET attempts=attempts+1,last_error=?,updated_at=? WHERE id=?",
                              (str(b.last_error or "send failed")[:2000], db.now_iso(), row["id"]))
                break
            db.execute("DELETE FROM flow_outbox WHERE id=?", (row["id"],))
            sent += 1
        if sent:
            db.log_event(f"Flow outbox flush {source}: {sent}", kind="flow")
        return sent

    async def _forward_source(self, source: str, msg: dict[str, Any]) -> bool:
        b = self.sources.get(source)
        ok = await b.send(msg) if b else False
        if not ok and str(msg.get("type") or "") in DURABLE_SOURCE_TYPES:
            self._queue_source_outbox(source, msg, (b.last_error if b else "source missing") or "bridge offline")
        return ok

    async def _download_for_source(self, msg: dict[str, Any], source: str, item: QueueItem | None) -> None:
        jid = str(msg.get("jobId") or "")
        sid = int(msg.get("sceneId") or 0)
        mid = str(msg.get("mediaId") or "")
        url = str(msg.get("url") or msg.get("signedUrl") or "")
        if not (jid and sid and mid and url):
            await self._send_ext({"type": "VIDEO_URL_DOWNLOAD_ACK", "ok": False, "jobId": jid, "sceneId": sid, "mediaId": mid, "error": "V2.8: thiếu metadata download"})
            return
        if not url.lower().startswith("https://"):
            await self._send_ext({"type": "VIDEO_URL_DOWNLOAD_ACK", "ok": False, "jobId": jid, "sceneId": sid, "mediaId": mid, "error": "V2.8 chỉ tải signed URL HTTPS"})
            return
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in jid)[:80]
        safe_mid = "".join(c if c.isalnum() or c in "-_" else "_" for c in mid)[:32] or "media"
        folder = DOWNLOADS / safe
        folder.mkdir(parents=True, exist_ok=True)
        fn = folder / f"scene_{sid:03d}_{safe_mid}.mp4"
        try:
            max_bytes = 1024 * 1024 * 1024  # 1 GiB guard against a bad/hostile signed URL
            part = fn.with_suffix(fn.suffix + ".part")
            part.unlink(missing_ok=True)
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(180, connect=20)) as client:
                async with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as r:
                    r.raise_for_status()
                    cl = r.headers.get("content-length")
                    if cl and int(cl) > max_bytes:
                        raise RuntimeError(f"video quá lớn: {cl} bytes")
                    size = 0
                    with part.open("wb") as f:
                        async for chunk in r.aiter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > max_bytes:
                                raise RuntimeError("video vượt giới hạn 1 GiB")
                            f.write(chunk)
            if size < 1024:
                raise RuntimeError(f"file quá nhỏ {size} bytes")
            part.replace(fn)
            ready = {
                "type": "VIDEO_FILE_READY", "jobId": jid, "sceneId": sid, "mediaId": mid,
                "mediaIndex": int(msg.get("mediaIndex") or 0), "localPath": str(fn.resolve()), "size": size,
                "source": "v28-master-download", **self._lease_meta(sid, item),
            }
            forwarded = await self._forward_source(source, ready)
            await self._send_ext({"type": "VIDEO_URL_DOWNLOAD_ACK", "ok": bool(forwarded), "jobId": jid, "sceneId": sid, "mediaId": mid, "size": size, "localPath": str(fn.resolve()), "error": None if forwarded else "source bridge offline; V2.8 queued durable delivery"})
        except Exception as exc:
            try:
                fn.with_suffix(fn.suffix + ".part").unlink(missing_ok=True)
            except OSError:
                pass
            err = str(exc)
            await self._forward_source(source, {"type": "VIDEO_FILE_ERROR", "jobId": jid, "sceneId": sid, "mediaId": mid, "error": err, **self._lease_meta(sid, item)})
            await self._send_ext({"type": "VIDEO_URL_DOWNLOAD_ACK", "ok": False, "jobId": jid, "sceneId": sid, "mediaId": mid, "error": err})

    async def handle_ext(self, msg: dict[str, Any]) -> None:
        self.extension_last_seen = time.time()
        typ = str(msg.get("type") or "")
        if typ == "AGENT_HELLO":
            raw_runtime = msg.get("runtime") or {}
            runtime = {
                "running": bool(raw_runtime.get("running")) if isinstance(raw_runtime, dict) else False,
                "serverJobId": raw_runtime.get("serverJobId") if isinstance(raw_runtime, dict) else None,
                "progressLabel": raw_runtime.get("progressLabel") if isinstance(raw_runtime, dict) else None,
            }
            self.extension_meta = {
                "id": msg.get("extensionId"), "workerId": msg.get("workerId"), "version": msg.get("version"),
                "connectedAt": time.time(), "runtime": runtime,
                "capabilities": msg.get("capabilities") or {},
            }
            self.event.set()
            return
        if typ == "FLOW_JOB_ACCEPTED":
            jid = str(msg.get("jobId") or "")
            self.active_last_progress_at = time.time()
            if isinstance(self.extension_meta, dict):
                self.extension_meta["runtime"] = {
                    "running": True,
                    "serverJobId": jid,
                    "progressLabel": "JOB ACCEPTED",
                }
            return
        if typ == "PONG":
            raw_runtime = msg.get("runtime") if isinstance(msg.get("runtime"), dict) else None
            if raw_runtime is not None and isinstance(self.extension_meta, dict):
                self.extension_meta["runtime"] = {
                    "running": bool(raw_runtime.get("running")),
                    "serverJobId": raw_runtime.get("serverJobId") or raw_runtime.get("jobId"),
                    "progressLabel": raw_runtime.get("progressLabel") or raw_runtime.get("runtimeState"),
                }
            return
        if typ in {"AGENT_HEARTBEAT", "FLOW_RUNTIME"}:
            raw_runtime = msg.get("runtime") if isinstance(msg.get("runtime"), dict) else {}
            running = bool(msg.get("running") if "running" in msg else raw_runtime.get("running"))
            server_job_id = msg.get("jobId") or msg.get("serverJobId") or raw_runtime.get("serverJobId") or raw_runtime.get("jobId")
            progress_label = msg.get("progressLabel") or msg.get("runtimeState") or raw_runtime.get("progressLabel") or raw_runtime.get("runtimeState")
            old_label = (self.extension_meta.get("runtime") or {}).get("progressLabel") if isinstance(self.extension_meta, dict) else None
            if progress_label and progress_label != old_label:
                self.active_last_progress_at = time.time()
            if isinstance(self.extension_meta, dict):
                self.extension_meta["runtime"] = {
                    "running": running,
                    "serverJobId": server_job_id,
                    "progressLabel": progress_label,
                }
        if typ == "EXTENSION_LOG":
            txt = str(msg.get("message") or "")
            lvl = str(msg.get("level") or "INFO").upper()
            if txt:
                db.log_event(txt, level=lvl, kind="extension")
            return
        if typ == "FLOW_CONTROL_RESULT":
            fut = self.control_waiters.pop(str(msg.get("commandId") or ""), None)
            if fut and not fut.done():
                fut.set_result(msg)
            return
        if typ in {"SHOPEE_AFFILIATE_RESULT", "SHOPEE_AFFILIATE_DIAG_RESULT", "SHOPEE_SESSION_HEALTH_RESULT"}:
            fut = self.rpc_waiters.pop(str(msg.get("requestId") or ""), None)
            if fut and not fut.done():
                fut.set_result(msg)
            return
        if typ == "SCENE_CHECKPOINT":
            self.active_last_progress_at = time.time()
            jid = str(msg.get("jobId") or "")
            sidx = int(msg.get("sceneIndex") or 0)
            sid = int(msg.get("sceneId") or sidx + 1)
            imid = msg.get("imageMediaId")
            vmid = msg.get("videoMediaId")
            status = str(msg.get("status") or "RUNNING")
            prog = int(msg.get("progress") or 0)
            err = msg.get("error")
            db.save_scene_checkpoint(jid, sidx, scene_id=sid, image_media_id=imid,
                                     video_media_id=vmid, status=status, progress=prog, error=err, payload=msg)
            return
        if typ == "MEDIA_ID_TRACKED":
            self.active_last_progress_at = time.time()
            jid = str(msg.get("jobId") or "")
            sid = int(msg.get("sceneId") or 1)
            sidx = int(msg.get("sceneIndex") or (sid - 1))
            mid = str(msg.get("mediaId") or "")
            title = str(msg.get("title") or "")
            db.log_event(f"Ghi nhận mediaId Scene {sid} ({mid[:16]}…)", level="INFO", kind="flow")
            db.save_scene_checkpoint(jid, sidx, scene_id=sid, video_media_id=mid, status="VIDEO_PENDING")
            return
        if typ == "VIDEO_FILE_READY":
            self.active_last_progress_at = time.time()
            jid = str(msg.get("jobId") or "")
            sid = int(msg.get("sceneId") or 1)
            mid = str(msg.get("mediaId") or "")
            src_path = str(msg.get("localPath") or "")
            candidates = [
                Path(src_path) if src_path else None,
                Path.home() / "Downloads" / Path(src_path).name if src_path else None,
                Path.home() / "Downloads" / mid if mid else None,
                Path.home() / "Downloads" / f"{mid}.mp4" if mid else None,
            ]
            valid_src = None
            for c in candidates:
                if c and c.is_file() and c.stat().st_size > 1024:
                    valid_src = c
                    break
            if jid and valid_src:
                safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in jid)[:80]
                safe_mid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in mid)[:32] or "media"
                folder = DOWNLOADS / safe
                folder.mkdir(parents=True, exist_ok=True)
                target_fn = folder / f"scene_{sid:03d}_{safe_mid}.mp4"
                try:
                    shutil.copy2(valid_src, target_fn)
                    print(f"[BROKER] Saved video: {valid_src} -> {target_fn} ({valid_src.stat().st_size} bytes)", flush=True)
                    db.log_event(f"Đã lưu video Scene {sid} (mediaId={safe_mid}) -> {target_fn.name}", level="SUCCESS", kind="flow")
                    sidx = int(msg.get("sceneIndex") or (sid - 1))
                    db.save_scene_checkpoint(jid, sidx, scene_id=sid, video_media_id=mid,
                                             local_path=str(target_fn), status="DOWNLOADED", progress=100)
                    fut = self.job_waiters.get(jid)
                    if fut and not fut.done():
                        expected = getattr(fut, "_expected_scenes", None)
                        if expected:
                            clips = self.get_job_clips(jid)
                            if len(clips) >= expected:
                                fut.set_result({"ok": True, "jobId": jid, "video_paths": clips, "status": "DONE", "error": None})
                except Exception as exc:
                    print(f"[BROKER] Copy local VIDEO_FILE_READY error: {exc}", flush=True)
        source = self._route_source(msg)
        if typ in {"VIDEO_DOWNLOAD_URL", "VIDEO_DOWNLOAD_URL_READY"} and source:
            item = self.active if self._matches_active(msg) else None
            t = asyncio.create_task(self._download_for_source(msg, source, item))
            self.download_tasks.add(t)
            t.add_done_callback(self.download_tasks.discard)
            return
        if source:
            await self._forward_source(source, msg)
            if typ in {"SHOPEE_PRODUCT_RESULT", "SHOPEE_SEARCH_RESULT"}:
                rid = str(msg.get("requestId") or "")
                if rid:
                    self.request_routes.pop(rid, None)
        if typ in {"FLOW_JOB_RESULT", "FLOW_JOB_REJECTED", "FLOW_JOB_INTERRUPTED"}:
            jid = str(msg.get("jobId") or (self.active.job_id if self.active else ""))
            fut = self.job_waiters.pop(jid, None) if jid else None
            is_ok = bool(typ == "FLOW_JOB_RESULT" and msg.get("ok"))
            err = str(msg.get("error") or ("" if is_ok else typ))
            clips = self.get_job_clips(jid) if jid else []
            if fut and not fut.done():
                fut.set_result({"ok": is_ok, "jobId": jid, "video_paths": clips, "status": "DONE" if is_ok else "FAILED", "error": err if not is_ok else None})
            if self._matches_active(msg):
                done = self.active
                assert done is not None
                done.status = "DONE" if is_ok else "FAILED"
                done.finished_at = time.time()
                done.result = err or "OK"
                self.recent.append(done)
                self.job_routes.pop(done.job_id, None)
                db.log_event(f"Flow {done.status} {done.source}/{done.job_id}: {done.result}", kind="flow")
                self.active = None
                self.event.set()
        elif typ == "VIDEO_DOWNLOAD_SUMMARY" and self._matches_active(msg) and self.active and str(self.active.message.get("type") or "") == "DOWNLOAD_MEDIA_FILES":
            done = self.active
            failures = msg.get("failures") or []
            done.status = "FAILED" if failures else "DONE"
            done.finished_at = time.time()
            done.result = f"FAILED {len(failures)}" if failures else "DOWNLOAD OK"
            self.recent.append(done)
            self.job_routes.pop(done.job_id, None)
            self.active = None
            self.event.set()

    def get_job_clips(self, job_id: str) -> list[str]:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in job_id)[:80]
        folder = DOWNLOADS / safe
        clips: list[Path] = []
        if folder.exists():
            for p in folder.glob("*.mp4"):
                if p.is_file() and p.stat().st_size > 1024 and not p.name.endswith("_final.mp4"):
                    clips.append(p)
        cps = db.get_scene_checkpoints(job_id)
        for cp in cps:
            lp = cp.get("local_path")
            if lp:
                p_obj = Path(lp)
                if p_obj.is_file() and p_obj.stat().st_size > 1024 and p_obj not in clips:
                    clips.append(p_obj)
        clips.sort(key=lambda x: x.name)
        return [str(c.resolve()) for c in clips]

    def get_job_scene_clips(self, job_id: str, expected_scenes: int = 1) -> tuple[bool, list[str]]:
        """Validates that EVERY scene from 1 to expected_scenes has a valid clip."""
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in job_id)[:80]
        folder = DOWNLOADS / safe
        cps = db.get_scene_checkpoints(job_id)
        cp_by_index = {int(cp.get("scene_index") or 0): cp for cp in cps}
        cp_by_id = {int(cp.get("scene_id") or 0): cp for cp in cps}

        resolved_clips: list[Path] = []
        all_present = True

        for idx in range(expected_scenes):
            sid = idx + 1
            clip_path = None

            # 1. Check checkpoint table
            cp = cp_by_index.get(idx) or cp_by_id.get(sid)
            if cp and cp.get("local_path"):
                p = Path(cp["local_path"])
                if p.is_file() and p.stat().st_size > 1024:
                    clip_path = p

            # 2. Check filesystem by scene prefix
            if not clip_path and folder.exists():
                candidates = list(folder.glob(f"scene_{sid:03d}_*.mp4")) + list(folder.glob(f"scene_{idx:03d}_*.mp4"))
                for c in candidates:
                    if c.is_file() and c.stat().st_size > 1024 and not c.name.endswith("_final.mp4"):
                        clip_path = c
                        break

            if clip_path:
                resolved_clips.append(clip_path)
            else:
                all_present = False

        if not all_present and not resolved_clips:
            all_clips = self.get_job_clips(job_id)
            return (len(all_clips) >= expected_scenes, all_clips)

        return (all_present, [str(c.resolve()) for c in resolved_clips])

    async def wait_job(self, job_id: str, timeout: float = 600, expected_scenes: int = 1) -> dict[str, Any]:
        jid = str(job_id)
        is_complete, clips = self.get_job_scene_clips(jid, expected_scenes)
        if is_complete and len(clips) >= expected_scenes:
            return {"ok": True, "jobId": jid, "video_paths": clips, "status": "DONE", "error": None}

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        setattr(fut, "_expected_scenes", expected_scenes)
        self.job_waiters[jid] = fut

        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
            _, fresh_clips = self.get_job_scene_clips(jid, expected_scenes)
            res["video_paths"] = fresh_clips or self.get_job_clips(jid)
            return res
        except asyncio.TimeoutError:
            self.job_waiters.pop(jid, None)
            is_comp, fresh_clips = self.get_job_scene_clips(jid, expected_scenes)
            if fresh_clips and is_comp:
                return {"ok": True, "jobId": jid, "video_paths": fresh_clips, "status": "DONE", "error": None}
            elif fresh_clips:
                return {"ok": False, "jobId": jid, "video_paths": fresh_clips, "status": "PARTIAL", "error": f"Timeout {timeout}s but {len(fresh_clips)}/{expected_scenes} scenes ready"}
            else:
                return {"ok": False, "jobId": jid, "video_paths": [], "status": "TIMEOUT", "error": f"Timeout {timeout}s chờ job {jid}"}
        except Exception as exc:
            self.job_waiters.pop(jid, None)
            return {"ok": False, "jobId": jid, "video_paths": self.get_job_clips(jid), "status": "ERROR", "error": str(exc)}

    async def check_shopee_session_health(self, timeout: float = 10.0) -> dict[str, Any]:
        if not self.extension:
            return {"ok": False, "loggedIn": False, "error": "FLOW_WORKER chưa ONLINE"}
        rid = "shopee_sess_" + uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.rpc_waiters[rid] = fut
        ok = await self._send_ext({"type": "CHECK_SHOPEE_SESSION", "requestId": rid})
        if not ok:
            self.rpc_waiters.pop(rid, None)
            return {"ok": False, "loggedIn": False, "error": "Không gửi được lệnh tới extension"}
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
            return res
        except asyncio.TimeoutError:
            self.rpc_waiters.pop(rid, None)
            return {"ok": False, "loggedIn": False, "error": "Timeout kiểm tra session Shopee"}

    async def source_loop(self, source: str) -> None:
        b = self.sources[source]
        while True:
            try:
                async with websockets.connect(b.url, max_size=64 * 1024 * 1024, ping_interval=None, open_timeout=5) as ws:
                    b.ws = ws
                    b.connected = True
                    b.last_error = None
                    hello = {
                        "type": "AGENT_HELLO", "role": "flow-extension",
                        "extensionId": f"v28-broker-{source}", "workerId": f"v28-broker-{source}",
                        "label": f"V2.8 Central Queue · {source}", "browser": "Broker", "profile": "V28_MASTER",
                        "version": b.version, "imageCapacity": load_settings()["imageConcurrency"], "videoCapacity": load_settings()["videoConcurrency"],
                        "capabilities": {"imageMax": load_settings()["imageConcurrency"], "videoMax": load_settings()["videoConcurrency"]},
                        # Flow Content >=2.14 requires this flag before it will dispatch to an agent.
                        "failSafeReady": True,
                        "runtime": {"running": False, "broker": True, "masterPort": self.port},
                    }
                    await b.send(hello)
                    # Deliver a previous result before declaring this bridge READY. Otherwise the
                    # legacy engine can replay the same job while its terminal result is still in outbox.
                    for _ in range(10):
                        sent = await self._flush_source_outbox(source)
                        if sent < 200:
                            break
                    # Some engine generations unlock dispatch on AGENT_READY rather than HELLO alone.
                    await b.send({"type": "AGENT_READY", "runtime": hello["runtime"], "failSafeReady": True})
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            continue
                        try:
                            m = json.loads(raw)
                        except Exception:
                            continue
                        typ = str(m.get("type") or "")
                        if typ == "PING":
                            # Opportunistically drain durable results while the source stays online.
                            await self._flush_source_outbox(source)
                            await b.send({"type": "PONG", "runtime": {"running": bool(self.active and self.active.source == source), "broker": True}})
                            continue
                        if typ == "RUN_FLOW_JOB":
                            self.enqueue(source, m)
                            continue
                        if typ in {"STOP_ALL", "STOP_FLOW_ALL", "STOP_FLOW_JOB"}:
                            jid = str(m.get("jobId") or "")
                            if typ in {"STOP_ALL", "STOP_FLOW_ALL"}:
                                self.pending = deque(x for x in self.pending if x.source != source)
                            else:
                                self.pending = deque(x for x in self.pending if not (x.source == source and (not jid or x.job_id == jid)))
                            if self.active and self.active.source == source and (typ != "STOP_FLOW_JOB" or not jid or jid == self.active.job_id):
                                await self._send_ext(m)
                            self.event.set()
                            continue
                        if typ == "DOWNLOAD_MEDIA_FILES":
                            mm = dict(m)
                            mm["jobId"] = str(mm.get("jobId") or f"recover_{uuid.uuid4().hex[:8]}")
                            self.enqueue(source, mm)
                            continue
                        if typ in {"SHOPEE_INSPECT_PRODUCT", "SHOPEE_SEARCH_PRODUCTS"}:
                            rid = str(m.get("requestId") or "")
                            if rid:
                                self.request_routes[rid] = source
                            ok = await self._send_ext(m)
                            if not ok and rid:
                                self.request_routes.pop(rid, None)
                                await b.send({"type": "SHOPEE_SEARCH_RESULT" if typ == "SHOPEE_SEARCH_PRODUCTS" else "SHOPEE_PRODUCT_RESULT", "requestId": rid, "ok": False, "error": "Flow extension offline"})
                            continue
                        if self.active and self.active.source == source:
                            await self._send_ext(m)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                b.last_error = str(exc)
            finally:
                b.connected = False
                b.ws = None
            await asyncio.sleep(1.5)

    async def request_extension(self, message: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
        if not self.extension_ready(max_age=120):
            raise RuntimeError("Flow extension offline hoặc version cũ")
        rid = str(message.get("requestId") or uuid.uuid4().hex[:16])
        msg = dict(message)
        msg["requestId"] = rid
        fut = asyncio.get_running_loop().create_future()
        self.rpc_waiters[rid] = fut
        if not await self._send_ext(msg):
            self.rpc_waiters.pop(rid, None)
            raise RuntimeError("Kh?ng g?i ???c request sang extension")
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.rpc_waiters.pop(rid, None)

    async def control(self, action: str) -> dict[str, Any]:
        if not self.extension:
            raise HTTPException(409, "Flow extension chưa kết nối")
        if self.active:
            raise HTTPException(409, f"Flow đang chạy {self.active.source}/{self.active.job_id}")
        cid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.control_waiters[cid] = fut
        if not await self._send_ext({"type": "FLOW_CONTROL", "action": action, "commandId": cid}):
            self.control_waiters.pop(cid, None)
            raise HTTPException(409, "Mất kết nối extension")
        try:
            return await asyncio.wait_for(fut, 180)
        except asyncio.TimeoutError:
            self.control_waiters.pop(cid, None)
            if not fut.done():
                fut.cancel()
            raise HTTPException(504, "Flow control timeout")

    async def attach_extension(self, ws: WebSocket) -> None:
        await ws.accept()
        try:
            # Be tolerant across Flow worker generations. Some builds can emit a heartbeat/PONG
            # before AGENT_HELLO after service-worker wake. V2.8.0 closed immediately with 4400,
            # creating an endless reconnect loop. Wait for a recognisable agent frame instead.
            m: dict[str, Any] | None = None
            deadline = asyncio.get_running_loop().time() + 12.0
            while asyncio.get_running_loop().time() < deadline:
                try:
                    p = await asyncio.wait_for(ws.receive(), max(0.2, deadline - asyncio.get_running_loop().time()))
                except asyncio.TimeoutError:
                    break
                if p.get("type") == "websocket.disconnect":
                    return
                raw = p.get("text")
                if raw is None:
                    continue
                try:
                    candidate = json.loads(raw)
                except Exception:
                    continue
                typ = str(candidate.get("type") or "")
                role = str(candidate.get("role") or "")
                if typ == "AGENT_HELLO":
                    m = candidate
                    break
                if typ == "AGENT_HEARTBEAT" and (role in {"", "flow-extension"}):
                    # Older/restarted workers: heartbeat itself proves the local Flow worker.
                    m = {**candidate, "type": "AGENT_HELLO", "role": "flow-extension"}
                    break
                if typ in {"PONG", "FLOW_JOB_INTERRUPTED"} and candidate.get("extensionId"):
                    m = {**candidate, "type": "AGENT_HELLO", "role": "flow-extension"}
                    break
                # Unknown early frames are ignored instead of killing the socket.
            if not m:
                await ws.close(code=4400, reason="Flow worker handshake timeout")
                return
            incoming = str(m.get("version") or "0")
            if version_key(incoming) < version_key(MIN_EXTENSION_VERSION):
                # Fail loudly: the UI/log must make it obvious that Chrome still has an old
                # unpacked folder loaded instead of silently behaving like "extension offline".
                db.log_event(
                    f"REJECT FLOW_WORKER v{incoming} · cần >= {MIN_EXTENSION_VERSION} · extensionId={m.get('extensionId') or m.get('workerId') or '?'}",
                    level="ERROR", kind="flow",
                )
                print(f"[V2.8 FLOW] REJECT worker v{incoming} · cần >= {MIN_EXTENSION_VERSION}", flush=True)
                await ws.close(code=4009, reason=f"FLOW_WORKER quá cũ; cần >= {MIN_EXTENSION_VERSION}")
                return
            incoming_id = str(m.get("extensionId") or m.get("workerId") or "")
            if self.extension:
                current = str(self.extension_meta.get("version") or "0")
                current_id = str(self.extension_meta.get("id") or self.extension_meta.get("workerId") or "")
                age = time.time() - float(self.extension_last_seen or 0.0) if self.extension_last_seen else 999.0
                newer = version_key(incoming) > version_key(current)
                stale = age > 30.0
                same_worker = bool(incoming_id and current_id and incoming_id == current_id)
                # V2.8.5: never let an equal-version duplicate (including the same
                # Chrome extension id) replace a healthy socket. MV3 service-worker
                # wake/reconnect can briefly create a second websocket before the
                # old close frame reaches FastAPI; replacing the healthy socket here
                # caused the bridge to flap/disconnect itself. A newer worker or a
                # genuinely stale socket may still take over.
                if not newer and not stale:
                    self.duplicate_rejections += 1
                    self.last_duplicate = {"at": time.time(), "rejectedVersion": incoming, "activeVersion": current,
                                           "incomingId": incoming_id, "activeId": current_id, "activeAgeSec": round(age, 2),
                                           "sameWorker": same_worker}
                    await ws.close(code=4009, reason=f"duplicate Flow worker; giữ socket đang khỏe {current}")
                    return
                self.last_duplicate = {"at": time.time(), "replacedVersion": current, "newVersion": incoming,
                                       "reason": "newer" if newer else "stale",
                                       "activeAgeSec": round(age, 2)}
                old = self.extension
                try:
                    await old.close(code=4009, reason=f"superseded by Flow worker {incoming}")
                except Exception:
                    pass
            self.extension = ws
            self.extension_last_seen = time.time()
            self.extension_last_ping = 0.0
            await self.handle_ext(m)
            self.event.set()
            while True:
                p = await ws.receive()
                if p.get("type") == "websocket.disconnect":
                    break
                if p.get("text"):
                    try:
                        m = json.loads(p["text"])
                    except Exception:
                        continue
                    await self.handle_ext(m)
        finally:
            if self.extension is ws:
                self.extension = None
                self.extension_meta = {}
                self.extension_last_seen = 0.0
                self.extension_last_ping = 0.0
                if self.active:
                    item = self.active
                    item.status = "WAITING"
                    item.started_at = None
                    if not any(x.queue_id == item.queue_id for x in self.pending):
                        self.pending.appendleft(item)
                    self.active = None
                for cid, fut in list(self.control_waiters.items()):
                    self.control_waiters.pop(cid, None)
                    if not fut.done():
                        fut.set_exception(RuntimeError("Flow extension disconnected"))
                self.event.set()
