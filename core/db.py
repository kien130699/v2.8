from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator

try:
    from zoneinfo import ZoneInfo
    VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    VN_TZ = timezone(timedelta(hours=7))

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "v28.sqlite3"

SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS facebook_pages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    access_token TEXT NOT NULL,
    tasks_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    last_test_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_templates (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    engine TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    defaults_json TEXT NOT NULL DEFAULT '{}',
    schema_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_instances (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    engine_ref TEXT,
    schedule_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES job_templates(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_job_instances_template ON job_instances(template_id, id);

CREATE TABLE IF NOT EXISTS instance_pages (
    instance_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    publish_delay_seconds INTEGER NOT NULL DEFAULT 0,
    caption_suffix TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY(instance_id, page_id),
    FOREIGN KEY(instance_id) REFERENCES job_instances(id) ON DELETE CASCADE,
    FOREIGN KEY(page_id) REFERENCES facebook_pages(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    trigger TEXT NOT NULL DEFAULT 'manual',
    worker_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    heartbeat_at TEXT,
    engine_run_id TEXT,
    engine_job_ids_json TEXT NOT NULL DEFAULT '[]',
    output_json TEXT NOT NULL DEFAULT '{}',
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(instance_id) REFERENCES job_instances(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_runs_instance ON runs(instance_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, created_at);

CREATE TABLE IF NOT EXISTS flow_scene_checkpoints (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    scene_index INTEGER NOT NULL,
    scene_id INTEGER NOT NULL DEFAULT 1,
    image_media_id TEXT,
    video_media_id TEXT,
    local_path TEXT,
    status TEXT NOT NULL DEFAULT 'NOT_STARTED',
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, scene_index)
);
CREATE INDEX IF NOT EXISTS idx_flow_scene_checkpoints_job ON flow_scene_checkpoints(job_id, scene_index);

CREATE TABLE IF NOT EXISTS publish_jobs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    video_path TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    dry_run INTEGER NOT NULL DEFAULT 0,
    fb_video_id TEXT,
    result_json TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    retry_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, page_id, video_path),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY(page_id) REFERENCES facebook_pages(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status, retry_after, created_at);

CREATE TABLE IF NOT EXISTS event_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO',
    kind TEXT NOT NULL DEFAULT 'system',
    instance_id TEXT,
    run_id TEXT,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_event_logs_ts ON event_logs(id DESC);

CREATE TABLE IF NOT EXISTS settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flow_outbox (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    message_type TEXT NOT NULL,
    dedupe_key TEXT,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flow_outbox_source ON flow_outbox(source, created_at);


CREATE TABLE IF NOT EXISTS affiliate_cache (
    origin_url TEXT PRIMARY KEY,
    affiliate_url TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'shopee',
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_affiliate_cache_updated ON affiliate_cache(updated_at DESC);

CREATE TABLE IF NOT EXISTS run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    detail TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, step_key),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step_key);

CREATE TABLE IF NOT EXISTS scene_checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scene_key TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'video',
    status TEXT NOT NULL DEFAULT 'pending',
    output_path TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, scene_key, media_type),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scene_checkpoints_run ON scene_checkpoints(run_id, status, scene_key);
"""


def now_iso() -> str:
    return datetime.now(VN_TZ).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
    finally:
        c.close()


def _columns(c: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _add_column(c: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(c, table):
        c.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def init_db() -> None:
    """Create/upgrade V2.8 DB without deleting user jobs/pages/history."""
    with connect() as c:
        # WAL is persistent but set explicitly on startup for old databases too.
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _add_column(c, "facebook_pages", "archived INTEGER NOT NULL DEFAULT 0")
        _add_column(c, "job_instances", "archived INTEGER NOT NULL DEFAULT 0")
        _add_column(c, "runs", "trigger TEXT NOT NULL DEFAULT 'manual'")
        _add_column(c, "runs", "worker_id TEXT")
        _add_column(c, "runs", "attempt INTEGER NOT NULL DEFAULT 0")
        _add_column(c, "runs", "heartbeat_at TEXT")
        _add_column(c, "job_templates", "version INTEGER NOT NULL DEFAULT 1")
        _add_column(c, "job_instances", "template_version INTEGER NOT NULL DEFAULT 1")
        
        # Publish jobs session tracking & idempotency
        _add_column(c, "publish_jobs", "upload_session_id TEXT")
        _add_column(c, "publish_jobs", "upload_offset INTEGER NOT NULL DEFAULT 0")
        _add_column(c, "publish_jobs", "idempotency_key TEXT")

        # Unify schema migrations for checkpoint tables across versions
        _add_column(c, "flow_scene_checkpoints", "run_id TEXT")
        _add_column(c, "flow_scene_checkpoints", "scene_key TEXT NOT NULL DEFAULT ''")
        _add_column(c, "flow_scene_checkpoints", "output_path TEXT NOT NULL DEFAULT ''")
        _add_column(c, "flow_scene_checkpoints", "attempts INTEGER NOT NULL DEFAULT 0")

        _add_column(c, "scene_checkpoints", "job_id TEXT NOT NULL DEFAULT ''")
        _add_column(c, "scene_checkpoints", "scene_index INTEGER NOT NULL DEFAULT 0")
        _add_column(c, "scene_checkpoints", "scene_id INTEGER NOT NULL DEFAULT 1")
        _add_column(c, "scene_checkpoints", "image_media_id TEXT")
        _add_column(c, "scene_checkpoints", "video_media_id TEXT")
        _add_column(c, "scene_checkpoints", "local_path TEXT NOT NULL DEFAULT ''")
        _add_column(c, "scene_checkpoints", "progress INTEGER NOT NULL DEFAULT 0")
        _add_column(c, "scene_checkpoints", "error TEXT")
        _add_column(c, "scene_checkpoints", "attempt_id TEXT NOT NULL DEFAULT '0'")
        _add_column(c, "scene_checkpoints", "attempt INTEGER NOT NULL DEFAULT 0")

        # Safe migration: populate authoritative scene_checkpoints from existing flow_scene_checkpoints without deleting old table
        try:
            c.execute("""
            INSERT OR IGNORE INTO scene_checkpoints(id, run_id, job_id, scene_key, scene_index, scene_id, media_type, status, output_path, local_path, progress, attempts, last_error, payload_json, created_at, updated_at)
            SELECT COALESCE(run_id, job_id) || ':' || scene_id || ':video', COALESCE(run_id, job_id), job_id, 'scene_' || scene_id, scene_index, scene_id, 'video', status, COALESCE(local_path, ''), COALESCE(local_path, ''), progress, 0, error, payload_json, created_at, updated_at
            FROM flow_scene_checkpoints
            """)
        except Exception:
            pass

        if "flow_outbox" in {str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            _add_column(c, "flow_outbox", "dedupe_key TEXT")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_flow_outbox_dedupe ON flow_outbox(dedupe_key) WHERE dedupe_key IS NOT NULL")
        c.execute("CREATE INDEX IF NOT EXISTS idx_job_instances_archived ON job_instances(archived,template_id,id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status,retry_after,created_at)")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as c:
        r = c.execute(sql, params).fetchone()
    return dict(r) if r else None


def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as c:
        rs = c.execute(sql, params).fetchall()
    return [dict(r) for r in rs]


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with connect() as c:
        cur = c.execute(sql, params)
        return int(cur.rowcount or 0)


def set_setting(key: str, value: Any) -> None:
    ts = now_iso()
    raw = value if isinstance(value, str) else dumps(value)
    with connect() as c:
        c.execute(
            "INSERT INTO settings_kv(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, raw, ts),
        )


def get_setting(key: str, default: Any = None, *, json_value: bool = False) -> Any:
    r = row("SELECT value FROM settings_kv WHERE key=?", (key,))
    if not r:
        return default
    return loads(r["value"], default) if json_value else r["value"]


def log_event(message: str, *, level: str = "INFO", kind: str = "system", instance_id: str | None = None,
              run_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO event_logs(ts,level,kind,instance_id,run_id,message,payload_json) VALUES(?,?,?,?,?,?,?)",
                (now_iso(), str(level or "INFO").upper(), str(kind or "system"), instance_id, run_id,
                 str(message)[:8000], dumps(payload or {})),
            )
    except sqlite3.Error as exc:
        # Logging must never be able to kill a worker/publisher loop.
        print(f"[V2.8 LOG ERROR] {exc}: {message}", flush=True)


def list_logs(limit: int = 500, kind: str | None = None) -> list[dict[str, Any]]:
    limit_val = max(1, min(int(limit or 500), 5000))
    if str(kind).lower() == "server":
        out = rows("SELECT * FROM event_logs WHERE kind != 'extension' ORDER BY id DESC LIMIT ?", (limit_val,))
    elif str(kind).lower() == "extension":
        out = rows("SELECT * FROM event_logs WHERE kind = 'extension' ORDER BY id DESC LIMIT ?", (limit_val,))
    else:
        out = rows("SELECT * FROM event_logs ORDER BY id DESC LIMIT ?", (limit_val,))
    for x in out:
        x["payload"] = loads(x.pop("payload_json", "{}"), {})
        x["created_at"] = x.get("ts")
    return out


def prune_logs(keep: int = 10000) -> int:
    keep = max(1000, int(keep))
    with connect() as c:
        cur = c.execute(
            "DELETE FROM event_logs WHERE id < COALESCE((SELECT id FROM event_logs ORDER BY id DESC LIMIT 1 OFFSET ?),0)",
            (keep,),
        )
        return int(cur.rowcount or 0)


def save_scene_checkpoint(job_id: str, scene_index: int, *, run_id: str | None = None, scene_id: int = 1,
                          image_media_id: str | None = None, video_media_id: str | None = None,
                          local_path: str | None = None, status: str = "NOT_STARTED",
                          progress: int = 0, attempt_id: str = "0", error: str | None = None,
                          payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ts = now_iso()
    uid = f"{job_id}_{scene_index}"
    actual_run_id = run_id or job_id
    scene_key = f"scene_{scene_id}"
    media_type = "video"
    with connect() as c:
        # Write to legacy table flow_scene_checkpoints
        c.execute(
            """INSERT INTO flow_scene_checkpoints(id, job_id, run_id, scene_key, scene_index, scene_id, image_media_id, video_media_id,
                                            local_path, output_path, status, progress, error, payload_json, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id, scene_index) DO UPDATE SET
                   scene_id = COALESCE(excluded.scene_id, flow_scene_checkpoints.scene_id),
                   run_id = COALESCE(excluded.run_id, flow_scene_checkpoints.run_id),
                   image_media_id = COALESCE(excluded.image_media_id, flow_scene_checkpoints.image_media_id),
                   video_media_id = COALESCE(excluded.video_media_id, flow_scene_checkpoints.video_media_id),
                   local_path = COALESCE(excluded.local_path, flow_scene_checkpoints.local_path),
                   output_path = COALESCE(excluded.output_path, flow_scene_checkpoints.output_path),
                   status = CASE
                       WHEN flow_scene_checkpoints.status IN ('DONE', 'COMPLETED', 'DOWNLOADED', 'ready') AND excluded.status IN ('RUNNING', 'PENDING', 'NOT_STARTED', 'SUBMITTED', 'queued', 'pending') THEN flow_scene_checkpoints.status
                       WHEN flow_scene_checkpoints.status = 'FAILED' AND excluded.status IN ('RUNNING', 'PENDING', 'NOT_STARTED', 'queued', 'pending') THEN flow_scene_checkpoints.status
                       ELSE excluded.status
                   END,
                   progress = MAX(excluded.progress, flow_scene_checkpoints.progress),
                   error = excluded.error,
                   payload_json = CASE WHEN excluded.payload_json != '{}' THEN excluded.payload_json ELSE flow_scene_checkpoints.payload_json END,
                   updated_at = excluded.updated_at
            """,
            (uid, str(job_id), str(actual_run_id), scene_key, int(scene_index), int(scene_id), image_media_id, video_media_id,
             local_path, local_path or "", str(status), int(progress), error, dumps(payload or {}), ts, ts)
        )
        # Write to authoritative table scene_checkpoints
        sc_uid = f"{actual_run_id}:{scene_key}:{media_type}"
        c.execute(
            """INSERT INTO scene_checkpoints(id, run_id, job_id, scene_key, scene_index, scene_id, media_type, status,
                                             output_path, local_path, progress, attempts, attempt_id, last_error, payload_json, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id, scene_key, media_type) DO UPDATE SET
                   job_id = COALESCE(excluded.job_id, scene_checkpoints.job_id),
                   scene_index = COALESCE(excluded.scene_index, scene_checkpoints.scene_index),
                   scene_id = COALESCE(excluded.scene_id, scene_checkpoints.scene_id),
                   image_media_id = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.image_media_id
                       ELSE COALESCE(excluded.image_media_id, scene_checkpoints.image_media_id)
                   END,
                   video_media_id = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.video_media_id
                       ELSE COALESCE(excluded.video_media_id, scene_checkpoints.video_media_id)
                   END,
                   local_path = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.local_path
                       WHEN excluded.local_path != '' THEN excluded.local_path
                       ELSE scene_checkpoints.local_path
                   END,
                   output_path = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.output_path
                       WHEN excluded.output_path != '' THEN excluded.output_path
                       ELSE scene_checkpoints.output_path
                   END,
                   status = CASE
                       -- 1. Stale event from older generation -> REJECT entirely
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.status
                       -- 2. Newer generation -> ACCEPT new generation status
                       WHEN CAST(excluded.attempt_id AS INTEGER) > CAST(scene_checkpoints.attempt_id AS INTEGER) THEN excluded.status
                       -- 3. Same generation -> Monotonic state rank
                       WHEN scene_checkpoints.status IN ('done','completed','ready','DOWNLOADED','DONE') AND excluded.status IN ('pending','running','queued','NOT_STARTED','RUNNING','SUBMITTED') THEN scene_checkpoints.status
                       WHEN scene_checkpoints.status IN ('failed','FAILED') AND excluded.status IN ('pending','running','queued','NOT_STARTED','RUNNING') THEN scene_checkpoints.status
                       ELSE excluded.status
                   END,
                   progress = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.progress
                       WHEN CAST(excluded.attempt_id AS INTEGER) > CAST(scene_checkpoints.attempt_id AS INTEGER) THEN excluded.progress
                       ELSE MAX(excluded.progress, scene_checkpoints.progress)
                   END,
                   attempts = scene_checkpoints.attempts + CASE WHEN excluded.status IN ('retry','RETRY') THEN 1 ELSE 0 END,
                   attempt_id = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.attempt_id
                       ELSE excluded.attempt_id
                   END,
                   last_error = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.last_error
                       ELSE excluded.last_error
                   END,
                   payload_json = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.payload_json
                       WHEN excluded.payload_json != '{}' THEN excluded.payload_json
                       ELSE scene_checkpoints.payload_json
                   END,
                   updated_at = CASE
                       WHEN CAST(excluded.attempt_id AS INTEGER) < CAST(scene_checkpoints.attempt_id AS INTEGER) THEN scene_checkpoints.updated_at
                       ELSE excluded.updated_at
                   END
            """,
            (sc_uid, str(actual_run_id), str(job_id), scene_key, int(scene_index), int(scene_id), media_type, str(status),
             local_path or "", local_path or "", int(progress), 0, str(attempt_id), str(error or "")[:2000], dumps(payload or {}), ts, ts)
        )
    return get_scene_checkpoint(job_id, scene_index) or {}


def get_scene_checkpoints(identifier: str) -> list[dict[str, Any]]:
    rs = rows("SELECT * FROM scene_checkpoints WHERE run_id=? OR job_id=? ORDER BY scene_index ASC, scene_id ASC", (str(identifier), str(identifier)))
    if not rs:
        rs = rows("SELECT * FROM flow_scene_checkpoints WHERE job_id=? OR run_id=? ORDER BY scene_index ASC", (str(identifier), str(identifier)))
    for r in rs:
        r["payload"] = loads(r.pop("payload_json", "{}"), {})
    return rs


def get_scene_checkpoint(job_id: str, scene_index: int) -> dict[str, Any] | None:
    r = row("SELECT * FROM scene_checkpoints WHERE (job_id=? OR run_id=?) AND (scene_index=? OR scene_id=?)", (str(job_id), str(job_id), int(scene_index), int(scene_index) + 1))
    if not r:
        r = row("SELECT * FROM flow_scene_checkpoints WHERE job_id=? AND scene_index=?", (str(job_id), int(scene_index)))
    if r:
        r["payload"] = loads(r.pop("payload_json", "{}"), {})
    return r

