from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from typing import Any

from facebook_app.config import settings
from facebook_app.db import connect, execute, get_setting, now_iso, row, rows, set_setting


def list_pages() -> list[dict[str, Any]]:
    data = rows("SELECT * FROM pages ORDER BY id")
    for p in data:
        try:
            p["celebrity_pool"] = json.loads(p.get("celebrity_pool") or "[]")
        except json.JSONDecodeError:
            p["celebrity_pool"] = []
        p["token_configured"] = bool(os.getenv(p.get("token_env_key") or ""))
    return data


def get_page(page_row_id: int) -> dict[str, Any] | None:
    p = row("SELECT * FROM pages WHERE id=?", (page_row_id,))
    if not p:
        return None
    try:
        p["celebrity_pool"] = json.loads(p.get("celebrity_pool") or "[]")
    except json.JSONDecodeError:
        p["celebrity_pool"] = []
    p["token_configured"] = bool(os.getenv(p.get("token_env_key") or ""))
    return p


def update_page(page_row_id: int, payload: dict[str, Any]) -> None:
    allowed = {"name", "page_id", "token_env_key", "enabled", "posts_per_day", "slot1", "slot2", "theme", "celebrity_pool", "output_mode"}
    fields: list[str] = []
    values: list[Any] = []
    for k, v in payload.items():
        if k not in allowed:
            continue
        if k == "celebrity_pool" and not isinstance(v, str):
            v = json.dumps(v or [], ensure_ascii=False)
        if k == "enabled":
            v = 1 if bool(v) else 0
        fields.append(f"{k}=?")
        values.append(v)
    if not fields:
        return
    fields.append("updated_at=?")
    values.append(now_iso())
    values.append(page_row_id)
    execute(f"UPDATE pages SET {', '.join(fields)} WHERE id=?", tuple(values))


def list_jobs(business_date: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if business_date:
        return rows(
            """SELECT j.*, p.name AS page_name FROM jobs j JOIN pages p ON p.id=j.page_row_id
               WHERE business_date=? ORDER BY scheduled_at, slot_no LIMIT ?""",
            (business_date, limit),
        )
    return rows(
        """SELECT j.*, p.name AS page_name FROM jobs j JOIN pages p ON p.id=j.page_row_id
           ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    )


def get_job(job_id: str) -> dict[str, Any] | None:
    return row(
        """SELECT j.*, p.name AS page_name, p.page_id AS facebook_page_id, p.token_env_key, p.output_mode,
                  p.theme, p.celebrity_pool
           FROM jobs j JOIN pages p ON p.id=j.page_row_id WHERE j.id=?""",
        (job_id,),
    )


def ensure_job(page: dict[str, Any], business_date: date, slot_no: int, scheduled_at: datetime) -> str:
    job_id = uuid.uuid4().hex[:16]
    ts = now_iso()
    with connect() as conn:
        try:
            conn.execute(
                """INSERT INTO jobs(id,page_row_id,business_date,slot_no,scheduled_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (job_id, page["id"], business_date.isoformat(), slot_no, scheduled_at.isoformat(timespec="seconds"), ts, ts),
            )
            return job_id
        except Exception:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE page_row_id=? AND business_date=? AND slot_no=?",
                (page["id"], business_date.isoformat(), slot_no),
            ).fetchone()
            if existing:
                return str(existing[0])
            raise



def create_test_job(page_row_id: int) -> str:
    """Create an isolated render-only job that can never be auto-published."""
    job_id = "test_" + uuid.uuid4().hex[:12]
    now = datetime.now(settings.tz)
    # Unique business_date prevents collision with normal daily slots and with other tests.
    business_date = f"TEST-{now.strftime('%Y%m%d-%H%M%S')}-{job_id[-4:]}"
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO jobs(id,page_row_id,business_date,slot_no,scheduled_at,status,step,progress,created_at,updated_at)
               VALUES(?,?,?,?,?,'TEST_QUEUED','TEST',0,?,?)""",
            (job_id, page_row_id, business_date, 0, now.isoformat(timespec="seconds"), ts, ts),
        )
    return job_id

def claim_next_render_job() -> dict[str, Any] | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute(
            """SELECT id FROM jobs WHERE status IN ('PLANNED','RETRY_RENDER')
               ORDER BY scheduled_at, created_at LIMIT 1"""
        ).fetchone()
        if not r:
            conn.execute("COMMIT")
            return None
        job_id = r[0]
        ts = now_iso()
        conn.execute(
            "UPDATE jobs SET status='SCRIPTING',step='SCRIPT',progress=5,attempts=attempts+1,updated_at=? WHERE id=?",
            (ts, job_id),
        )
        conn.execute("COMMIT")
    return get_job(job_id)


def claim_next_publish_job(now_iso_value: str) -> dict[str, Any] | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute(
            """SELECT id FROM jobs WHERE status='READY' AND scheduled_at<=?
               ORDER BY scheduled_at LIMIT 1""",
            (now_iso_value,),
        ).fetchone()
        if not r:
            conn.execute("COMMIT")
            return None
        job_id = r[0]
        conn.execute(
            "UPDATE jobs SET status='UPLOADING',step='FACEBOOK_START',progress=86,updated_at=? WHERE id=?",
            (now_iso(), job_id),
        )
        conn.execute("COMMIT")
    return get_job(job_id)


def update_job(job_id: str, **fields: Any) -> None:
    allowed = {
        "topic", "title", "status", "step", "progress", "script_json", "output_path", "sources_path",
        "facebook_video_id", "facebook_status", "error", "scheduled_at",
    }
    parts: list[str] = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "script_json" and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}=?")
        vals.append(v)
    if not parts:
        return
    parts.append("updated_at=?")
    vals.append(now_iso())
    vals.append(job_id)
    execute(f"UPDATE jobs SET {', '.join(parts)} WHERE id=?", tuple(vals))


def add_log(job_id: str, message: str, level: str = "INFO") -> None:
    execute(
        "INSERT INTO job_logs(job_id,ts,level,message) VALUES(?,?,?,?)",
        (job_id, now_iso(), level, message[:8000]),
    )


def job_logs(job_id: str, limit: int = 300) -> list[dict[str, Any]]:
    return rows(
        "SELECT * FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT ?",
        (job_id, limit),
    )[::-1]


def retry_job(job_id: str) -> None:
    update_job(job_id, status="RETRY_RENDER", step="WAITING", progress=0, error="")


def dashboard() -> dict[str, Any]:
    today = datetime.now(settings.tz).date().isoformat()
    counts = {r["status"]: r["c"] for r in rows("SELECT status,COUNT(*) c FROM jobs WHERE business_date=? GROUP BY status", (today,))}
    pages = list_pages()
    page_stats = rows(
        """SELECT p.id, p.name,
                  SUM(CASE WHEN j.business_date=? THEN 1 ELSE 0 END) AS today_total,
                  SUM(CASE WHEN j.business_date=? AND j.status='PUBLISHED' THEN 1 ELSE 0 END) AS published,
                  SUM(CASE WHEN j.business_date=? AND j.status='READY' THEN 1 ELSE 0 END) AS ready,
                  SUM(CASE WHEN j.business_date=? AND j.status='FAILED' THEN 1 ELSE 0 END) AS failed
           FROM pages p LEFT JOIN jobs j ON j.page_row_id=p.id GROUP BY p.id,p.name ORDER BY p.id""",
        (today, today, today, today),
    )
    return {
        "date": today,
        "factory_state": get_setting("factory_state", "STOPPED"),
        "auto_publish": get_setting("auto_publish", "0") == "1",
        "daily_target": int(get_setting("daily_target", "20")),
        "counts": counts,
        "pages": pages,
        "page_stats": page_stats,
    }


def factory_state() -> str:
    return get_setting("factory_state", "STOPPED")


def set_factory_state(value: str) -> None:
    set_setting("factory_state", value)
