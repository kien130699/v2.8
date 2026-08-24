from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from . import db

FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v25.0").strip().strip("/") or "v25.0"


def graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{path.lstrip('/')}"


def request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    r = requests.request(method, url, timeout=(20, 180), **kwargs)
    try:
        data = r.json()
    except Exception:
        data = {"raw": (r.text or "")[:2000]}
    if not r.ok:
        raise RuntimeError(f"Facebook HTTP {r.status_code}: {data}")
    return data


def _page_public(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    d.pop("access_token", None)
    d["tasks"] = db.loads(d.pop("tasks_json", "[]"), [])
    d["last_test"] = db.loads(d.pop("last_test_json", None), None)
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def list_pages() -> list[dict[str, Any]]:
    return [_page_public(x) for x in db.rows("SELECT * FROM facebook_pages WHERE archived=0 ORDER BY name,id")]


from .crypto import encrypt_token, decrypt_token


def get_page_secret(page_id: str) -> dict[str, Any] | None:
    row = db.row("SELECT * FROM facebook_pages WHERE id=? AND archived=0 AND enabled=1", (page_id,))
    if row and row.get("access_token"):
        row["access_token"] = decrypt_token(row["access_token"])
    return row


def save_page(page_id: str, name: str, token: str, tasks: list[str] | None = None) -> None:
    page_id = str(page_id or "").strip()
    token = str(token or "").strip()
    name = str(name or page_id).strip()
    if not page_id or not page_id.isdigit():
        raise ValueError("Facebook Page ID phải là số")
    if len(token) < 10:
        raise ValueError("Facebook access token không hợp lệ")
    encrypted = encrypt_token(token)
    now = db.now_iso()
    with db.connect() as c:
        c.execute(
            """INSERT INTO facebook_pages(id,name,access_token,tasks_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,1,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,access_token=excluded.access_token,
                 tasks_json=excluded.tasks_json,enabled=1,archived=0,updated_at=excluded.updated_at""",
            (page_id, name, encrypted, db.dumps(tasks or []), now, now),
        )


def import_token(token: str) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        raise ValueError("Thiếu token Facebook")
    first_error = None
    try:
        data = request_json(
            "GET", graph_url("me/accounts"),
            params={"fields": "id,name,access_token,tasks", "limit": 100, "access_token": token},
        )
        saved = 0
        seen: set[str] = set()
        while True:
            for page in data.get("data") or []:
                pid = str(page.get("id") or "").strip()
                pt = str(page.get("access_token") or "").strip()
                if pid and pt and pid not in seen:
                    save_page(pid, str(page.get("name") or pid), pt, page.get("tasks") or [])
                    seen.add(pid)
                    saved += 1
            next_url = str(((data.get("paging") or {}).get("next")) or "").strip()
            if not next_url or saved >= 1000:
                break
            data = request_json("GET", next_url)
        if saved:
            db.log_event(f"Import Facebook: {saved} Page", kind="facebook", payload={"kind": "user_token", "saved": saved})
            return {"ok": True, "kind": "user_token", "saved": saved, "pages": list_pages()}
    except Exception as exc:
        first_error = str(exc)

    try:
        data = request_json(
            "GET", graph_url("me"),
            params={"fields": "id,name,category", "access_token": token},
        )
        pid = str(data.get("id") or "").strip()
        category = str(data.get("category") or "").strip()
        if not pid or not category:
            raise ValueError("Token không xác định được là Page Access Token")
        save_page(pid, str(data.get("name") or pid), token, [])
        db.log_event(f"Import Facebook Page: {data.get('name') or pid}", kind="facebook")
        return {"ok": True, "kind": "page_token", "saved": 1, "pages": list_pages()}
    except Exception as exc:
        detail = str(exc)
        if first_error:
            detail = f"User token: {first_error} | Page token: {detail}"
        raise RuntimeError(detail)


def test_page(page_id: str) -> dict[str, Any]:
    p = get_page_secret(page_id)
    if not p:
        raise ValueError("Không tìm thấy Facebook Page")
    data = request_json(
        "GET", graph_url(page_id),
        params={"fields": "id,name", "access_token": p["access_token"]},
    )
    with db.connect() as c:
        c.execute(
            "UPDATE facebook_pages SET last_test_json=?,updated_at=? WHERE id=?",
            (db.dumps(data), db.now_iso(), page_id),
        )
    return {"ok": True, **data, "data": data}


def delete_page(page_id: str) -> dict[str, Any]:
    # Keep old publish history/FK rows, but remove the token and hide the Page from new jobs.
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute("UPDATE facebook_pages SET archived=1,enabled=0,access_token='',updated_at=? WHERE id=? AND archived=0", (db.now_iso(), page_id))
        n = int(cur.rowcount or 0)
        # Detach it from current Jobs. Re-importing the same Page later must not silently
        # reactivate old mappings that the user thought were deleted.
        c.execute("DELETE FROM instance_pages WHERE page_id=?", (page_id,))
        c.commit()
    return {"ok": True, "deleted": bool(n), "archived": bool(n), "page_id": page_id}


def ffprobe_info(video_path: str | Path) -> dict[str, Any]:
    p = Path(video_path)
    if not p.exists():
        return {"ok": False, "error": f"Không thấy video: {p}"}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"ok": True, "ffprobe": False, "size": p.stat().st_size, "warning": "ffprobe chưa có trong PATH"}
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration,size",
        "-of", "json", str(p),
    ]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if cp.returncode != 0:
        return {"ok": False, "ffprobe": True, "error": (cp.stderr or cp.stdout)[-2000:]}
    try:
        data = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"ok": False, "ffprobe": True, "error": f"ffprobe JSON lỗi: {exc}"}
    s = (data.get("streams") or [{}])[0]
    f = data.get("format") or {}
    out = {
        "ok": True,
        "ffprobe": True,
        "width": int(s.get("width") or 0),
        "height": int(s.get("height") or 0),
        "duration": float(f.get("duration") or 0),
        "size": int(f.get("size") or p.stat().st_size),
    }
    if out["width"] <= 0 or out["height"] <= 0 or out["duration"] <= 0:
        return {**out, "ok": False, "error": "Video không có video stream/duration hợp lệ"}
    return out


def _retryable_publish_error(exc: Exception) -> bool:
    if isinstance(exc, requests.RequestException):
        return True
    text = str(exc)
    # Most Graph 4xx errors are permission/token/parameter problems; retrying five times
    # only delays a useful failure. Retry transient/rate-limit/server classes instead.
    for code in (408, 409, 425, 429, 500, 502, 503, 504):
        if f"HTTP {code}:" in text:
            return True
    if "Facebook HTTP 4" in text:
        return False
    return True


def enqueue_publish(run_id: str, page_id: str, video_path: str, title: str = "", description: str = "",
                    dry_run: bool = False) -> str:
    if not db.row("SELECT id FROM runs WHERE id=?", (run_id,)):
        raise ValueError("Run không tồn tại")
    if not get_page_secret(page_id):
        raise ValueError(f"Facebook Page không tồn tại/đã tắt: {page_id}")
    vp = Path(str(video_path)).resolve()
    if not vp.is_file() or vp.stat().st_size < 1024:
        raise ValueError(f"Video publish không tồn tại/rỗng: {vp}")
    video_path = str(vp)
    pid = "pub_" + uuid.uuid4().hex[:14]
    ts = db.now_iso()
    with db.connect() as c:
        existing = c.execute(
            "SELECT id FROM publish_jobs WHERE run_id=? AND page_id=? AND video_path=?",
            (run_id, page_id, video_path),
        ).fetchone()
        if existing:
            return str(existing[0])
        c.execute(
            """INSERT INTO publish_jobs(id,run_id,page_id,status,video_path,title,description,dry_run,created_at,updated_at)
               VALUES(?,?,?,'queued',?,?,?,?,?,?)""",
            (pid, run_id, page_id, video_path, title, description, 1 if dry_run else 0, ts, ts),
        )
    return pid


def _update_publish(pid: str, **fields: Any) -> None:
    fields["updated_at"] = db.now_iso()
    cols = ",".join(f"{k}=?" for k in fields)
    with db.connect() as c:
        c.execute(f"UPDATE publish_jobs SET {cols} WHERE id=?", (*fields.values(), pid))


def _finish_reel(page: dict[str, Any], video_id: str, title: str, description: str) -> dict[str, Any]:
    return request_json(
        "POST", graph_url(f"{page['id']}/video_reels"),
        params={
            "access_token": page["access_token"],
            "video_id": video_id,
            "upload_phase": "finish",
            "video_state": "PUBLISHED",
            "title": title[:255],
            "description": description[:5000],
        },
    )


def publish_one(pub_id: str) -> dict[str, Any]:
    job = db.row("SELECT * FROM publish_jobs WHERE id=?", (pub_id,))
    if not job:
        raise ValueError("Không tìm thấy publish job")
    page = get_page_secret(str(job["page_id"]))
    if not page:
        _update_publish(pub_id, status="failed", error="Facebook Page/token không tồn tại")
        return {"ok": False, "error": "Facebook Page/token không tồn tại"}
    path = Path(str(job["video_path"]))
    if not path.exists():
        _update_publish(pub_id, status="failed", error=f"Không thấy video: {path}")
        return {"ok": False, "error": f"Không thấy video: {path}"}

    preflight = ffprobe_info(path)
    if not preflight.get("ok"):
        _update_publish(pub_id, status="failed", error=str(preflight.get("error") or "Video preflight failed"), result_json=db.dumps({"preflight": preflight}))
        return {"ok": False, "error": preflight.get("error") or "Video preflight failed", "preflight": preflight}
    if bool(job.get("dry_run")):
        _update_publish(pub_id, status="dry_run_ok", result_json=db.dumps({"preflight": preflight}))
        return {"ok": True, "dry_run": True, "preflight": preflight}

    try:
        _update_publish(pub_id, status="starting", error=None)
        start = request_json(
            "POST", graph_url(f"{page['id']}/video_reels"),
            params={"access_token": page["access_token"], "upload_phase": "start"},
        )
        video_id = str(start.get("video_id") or "")
        upload_url = str(start.get("upload_url") or "")
        if not video_id or not upload_url:
            raise RuntimeError(f"Facebook không trả video_id/upload_url: {start}")
        _update_publish(pub_id, status="uploading", fb_video_id=video_id)
        size = path.stat().st_size
        headers = {
            "Authorization": f"OAuth {page['access_token']}",
            "offset": "0",
            "file_size": str(size),
            "Content-Type": "application/octet-stream",
        }
        with path.open("rb") as f:
            upload = request_json("POST", upload_url, headers=headers, data=f)
        _update_publish(pub_id, status="finishing")
        finish = _finish_reel(page, video_id, str(job.get("title") or ""), str(job.get("description") or ""))
        result = {"video_id": video_id, "upload": upload, "finish": finish, "preflight": preflight}
        _update_publish(pub_id, status="published", result_json=db.dumps(result), error=None)
        db.log_event(f"Facebook published → {page['name']} · {video_id}", kind="facebook", run_id=job.get("run_id"), payload={"page_id": page["id"], "publish_id": pub_id})
        return {"ok": True, **result}
    except Exception as exc:
        retry = int(job.get("retry_count") or 0) + 1
        retryable = retry <= 5 and _retryable_publish_error(exc)
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=min(1800, 30 * (2 ** max(0, retry - 1))))).isoformat(timespec="seconds") if retryable else None
        _update_publish(pub_id, status="retry_wait" if retryable else "failed", error=str(exc), retry_count=retry, retry_after=retry_at)
        db.log_event(f"Facebook publish lỗi: {exc}", level="ERROR", kind="facebook", run_id=job.get("run_id"), payload={"publish_id": pub_id, "retry": retry})
        return {"ok": False, "error": str(exc), "retry": retryable, "retry_at": retry_at}


def due_publish_jobs(limit: int = 10) -> list[dict[str, Any]]:
    now = db.now_iso()
    return db.rows(
        """SELECT * FROM publish_jobs
           WHERE status='queued' OR (status='retry_wait' AND retry_after IS NOT NULL AND retry_after<=?)
           ORDER BY created_at LIMIT ?""",
        (now, max(1, min(limit, 50))),
    )


def list_publish_jobs(limit: int = 200) -> list[dict[str, Any]]:
    out = db.rows(
        """SELECT p.*,f.name AS page_name FROM publish_jobs p
           LEFT JOIN facebook_pages f ON f.id=p.page_id ORDER BY p.created_at DESC LIMIT ?""",
        (max(1, min(limit, 1000)),),
    )
    for x in out:
        x["dry_run"] = bool(x.get("dry_run"))
        x["result"] = db.loads(x.pop("result_json", None), None)
    return out
