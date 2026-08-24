from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from facebook_app.config import settings


SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    page_id TEXT NOT NULL DEFAULT '',
    token_env_key TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    posts_per_day INTEGER NOT NULL DEFAULT 2,
    slot1 TEXT NOT NULL DEFAULT '10:00',
    slot2 TEXT NOT NULL DEFAULT '19:00',
    theme TEXT NOT NULL DEFAULT 'life',
    celebrity_pool TEXT NOT NULL DEFAULT '[]',
    output_mode TEXT NOT NULL DEFAULT 'reel_9_16',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    page_row_id INTEGER NOT NULL,
    business_date TEXT NOT NULL,
    slot_no INTEGER NOT NULL,
    scheduled_at TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PLANNED',
    step TEXT NOT NULL DEFAULT 'WAITING',
    progress INTEGER NOT NULL DEFAULT 0,
    script_json TEXT NOT NULL DEFAULT '{}',
    output_path TEXT NOT NULL DEFAULT '',
    sources_path TEXT NOT NULL DEFAULT '',
    facebook_video_id TEXT NOT NULL DEFAULT '',
    facebook_status TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(page_row_id, business_date, slot_no),
    FOREIGN KEY(page_row_id) REFERENCES pages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_date ON jobs(business_date);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule ON jobs(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, id);
"""


def now_iso() -> str:
    return datetime.now(settings.tz).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
    seed_defaults()


def seed_defaults() -> None:
    pages = [
        ("Góc Nhìn Cuộc Sống", "life", ["Warren Buffett", "Jack Ma", "Charlie Munger"]),
        ("Ngẫm Mỗi Ngày", "reflection", ["Warren Buffett", "Jackie Chan", "Jack Ma"]),
        ("Tư Duy Người Giàu", "wealth", ["Warren Buffett", "Bill Gates", "Charlie Munger"]),
        ("Chuyện Đời Chuyện Người", "relationships", ["Jack Ma", "Jackie Chan", "Warren Buffett"]),
        ("Bài Học Kinh Doanh", "business", ["Jack Ma", "Steve Jobs", "Elon Musk", "Bill Gates"]),
        ("Kỷ Luật Mỗi Ngày", "discipline", ["Steve Jobs", "Elon Musk", "Jack Ma"]),
        ("Trí Tuệ Phương Đông", "eastern", ["Jack Ma", "Jackie Chan"]),
        ("Sự Thật Cuộc Sống", "truth", ["Warren Buffett", "Charlie Munger", "Jackie Chan"]),
        ("Thành Công & Thất Bại", "success", ["Steve Jobs", "Jack Ma", "Elon Musk"]),
        ("Một Phút Suy Ngẫm", "short_reflection", ["Warren Buffett", "Jack Ma", "Bill Gates"]),
    ]
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        if count == 0:
            ts = now_iso()
            for i, (name, theme, pool) in enumerate(pages, start=1):
                # Spread slots to avoid all pages publishing at exactly the same second.
                minute = ((i - 1) * 7) % 60
                slot1 = f"10:{minute:02d}"
                slot2 = f"19:{minute:02d}"
                conn.execute(
                    """INSERT INTO pages(name,page_id,token_env_key,enabled,posts_per_day,slot1,slot2,theme,celebrity_pool,output_mode,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (name, "", f"FB_PAGE_{i}_TOKEN", 1, 2, slot1, slot2, theme, json.dumps(pool, ensure_ascii=False), "reel_9_16", ts, ts),
                )
        defaults = {
            "factory_state": "STOPPED",
            "daily_target": "20",
            "auto_publish": "1" if settings.default_auto_publish else "0",
            "prepare_hours_ahead": "24",
            "llm_selected_model": settings.ninerouter_default_model or settings.llm_model,
        }
        ts = now_iso()
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings_kv(key,value,updated_at) VALUES(?,?,?)",
                (key, value, ts),
            )


def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def get_setting(key: str, default: str = "") -> str:
    r = row("SELECT value FROM settings_kv WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO settings_kv(key,value,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, ts),
        )
