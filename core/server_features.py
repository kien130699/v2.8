
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import db

ORCHESTRATOR_STEPS = [
    "search", "affiliate", "script", "flow", "download", "merge", "validate", "caption", "publish"
]


def step(run_id: str, key: str, status: str, detail: str = "", payload: dict[str, Any] | None = None) -> None:
    ts = db.now_iso()
    payload_json = db.dumps(payload or {})
    with db.connect() as c:
        c.execute(
            "INSERT INTO run_steps(id,run_id,step_key,status,detail,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id,step_key) DO UPDATE SET status=excluded.status,detail=excluded.detail,payload_json=excluded.payload_json,updated_at=excluded.updated_at",
            (f"{run_id}:{key}", run_id, key, status, str(detail or "")[:2000], payload_json, ts, ts),
        )


def init_steps(run_id: str) -> None:
    for key in ORCHESTRATOR_STEPS:
        step(run_id, key, "pending")


def list_steps(run_id: str) -> list[dict[str, Any]]:
    rows = db.rows("SELECT * FROM run_steps WHERE run_id=? ORDER BY created_at,id", (run_id,))
    for row in rows:
        row["payload"] = db.loads(row.pop("payload_json", "{}"), {})
    return rows


def checkpoint(run_id: str, scene_key: str, media_type: str, status: str, *, output_path: str = "", error: str = "", payload: dict[str, Any] | None = None) -> None:
    ts = db.now_iso()
    with db.connect() as c:
        c.execute(
            "INSERT INTO scene_checkpoints(id,run_id,scene_key,media_type,status,output_path,attempts,last_error,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?,?) "
            "ON CONFLICT(run_id,scene_key,media_type) DO UPDATE SET "
            "status=CASE "
            "  WHEN scene_checkpoints.status IN ('done','completed','ready','DOWNLOADED','DONE') AND excluded.status IN ('pending','running','queued','NOT_STARTED','RUNNING') THEN scene_checkpoints.status "
            "  ELSE excluded.status "
            "END, "
            "output_path=CASE WHEN excluded.output_path != '' THEN excluded.output_path ELSE scene_checkpoints.output_path END, "
            "attempts=scene_checkpoints.attempts+CASE WHEN excluded.status='retry' THEN 1 ELSE 0 END, "
            "last_error=excluded.last_error, "
            "payload_json=CASE WHEN excluded.payload_json != '{}' THEN excluded.payload_json ELSE scene_checkpoints.payload_json END, "
            "updated_at=excluded.updated_at",
            (f"{run_id}:{scene_key}:{media_type}", run_id, scene_key, media_type, status, output_path, str(error or "")[:2000], db.dumps(payload or {}), ts, ts),
        )


def list_checkpoints(run_id: str) -> list[dict[str, Any]]:
    rows = db.rows("SELECT * FROM scene_checkpoints WHERE run_id=? ORDER BY scene_key,media_type", (run_id,))
    for row in rows:
        row["payload"] = db.loads(row.pop("payload_json", "{}"), {})
    return rows


def get_affiliate(origin_url: str) -> str:
    row = db.row("SELECT affiliate_url FROM affiliate_cache WHERE origin_url=?", (origin_url,))
    return str(row.get("affiliate_url") or "") if row else ""


def set_affiliate(origin_url: str, affiliate_url: str, source: str = "shopee") -> None:
    if not origin_url or not affiliate_url:
        return
    ts = db.now_iso()
    with db.connect() as c:
        c.execute(
            "INSERT INTO affiliate_cache(origin_url,affiliate_url,source,created_at,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(origin_url) DO UPDATE SET affiliate_url=excluded.affiliate_url,source=excluded.source,updated_at=excluded.updated_at,last_error=NULL",
            (origin_url, affiliate_url, source, ts, ts),
        )


def ffprobe_duration(path: str) -> float | None:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.check_output([exe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path], timeout=20)
        data = json.loads(out.decode("utf-8", "ignore"))
        value = float((data.get("format") or {}).get("duration") or 0)
        return value if math.isfinite(value) and value > 0 else None
    except Exception:
        return None


def validate_output(videos: list[str], *, min_seconds: float = 4.0, max_seconds: float = 180.0) -> dict[str, Any]:
    checks = []
    ok = True
    for path in videos:
        p = Path(path)
        item = {"path": str(p), "exists": p.is_file(), "size": (p.stat().st_size if p.is_file() else 0), "duration": None, "ok": False}
        if not item["exists"] or int(item["size"] or 0) < 1024:
            ok = False
            checks.append(item)
            continue
        duration = ffprobe_duration(str(p))
        item["duration"] = duration
        item["ok"] = duration is None or (min_seconds <= duration <= max_seconds)
        ok = ok and bool(item["ok"])
        checks.append(item)
    return {"ok": ok and bool(videos), "checks": checks, "ffprobe": bool(shutil.which("ffprobe"))}


def enforce_caption_affiliate(caption: str, config: dict[str, Any]) -> str:
    products = config.get("shopee_products") or []
    if not isinstance(products, list):
        products = []
    affiliate_links = []
    origin_links = []
    for product in products:
        if not isinstance(product, dict):
            continue
        aff = str(product.get("affiliate_url") or "").strip()
        origin = str(product.get("origin_url") or product.get("url") or product.get("product_url") or "").strip()
        if aff:
            affiliate_links.append(aff)
        if origin:
            origin_links.append(origin)
    out = str(caption or "")
    for origin, aff in zip(origin_links, affiliate_links):
        if origin and aff:
            out = out.replace(origin, aff)
    missing = [x for x in affiliate_links if x not in out]
    if missing:
        out = (out.rstrip() + "\n\n" + "\n".join(f"Link s?n ph?m: {x}" for x in missing)).strip()
    return out
