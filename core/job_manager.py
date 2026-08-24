from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=7))

from . import db
from .engine import EngineFacade
from . import facebook
from . import server_features

ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = ROOT / "job_types"
ASSET_ROOT = ROOT / "data" / "job_assets"
ACTIVE_RUN_STATES = ("queued", "waiting_flow", "waiting_engine", "dispatching", "preparing", "running", "rendering")
def _safe_print(*values: object, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
    text = sep.join(str(value) for value in values)
    try:
        print(text, end=end, flush=flush)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, end=end, flush=flush)


def _coerce_config_value(key: str, spec: dict[str, Any], value: Any) -> Any:
    typ = str(spec.get("type") or "text")
    if typ == "checkbox":
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "yes", "on"}:
                return True
            if v in {"0", "false", "no", "off", ""}:
                return False
            raise ValueError(f"{spec.get('label') or key}: boolean không hợp lệ")
        return bool(value)
    if typ in {"number", "range"}:
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{spec.get('label') or key}: phải là số")
        if spec.get('min') is not None and n < float(spec['min']):
            raise ValueError(f"{spec.get('label') or key}: nhỏ hơn min {spec['min']}")
        if spec.get("max") is not None and n > float(spec["max"]):
            raise ValueError(f"{spec.get('label') or key}: lớn hơn max {spec['max']}")
        return int(n) if n.is_integer() else n
    if typ == "select":
        text = str(value or "").strip()
        options = []
        for option in spec.get("options") or []:
            if isinstance(option, dict):
                raw = option.get("value") if option.get("value") is not None else option.get("label")
                options.append(str(raw or ""))
            else:
                options.append(str(option))
        if options and text not in options:
            raise ValueError(f"{spec.get('label') or key}: lựa chọn không hợp lệ")
        return text
    if typ == "product_basket":
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, str):
            try:
                data = json.loads(value)
            except Exception:
                data = []
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        raise ValueError(f"{spec.get('label') or key}: phải là danh sách sản phẩm")
    if typ in {"tags", "lines"}:
        if isinstance(value, str):
            parts = value.splitlines() if typ == "lines" else value.replace("\n", ",").split(",")
        elif isinstance(value, list):
            parts = value
        else:
            raise ValueError(f"{spec.get('label') or key}: phải là danh sách")
        return [str(x).strip() for x in parts if str(x).strip()]
    if typ == "file":
        return str(value or "").strip()
    if typ == "time":
        text = str(value or "").strip()
        try:
            hh, mm = [int(x) for x in text.split(":")[:2]]
        except Exception:
            raise ValueError(f"{spec.get('label') or key}: giá» pháº£i dáº¡ng HH:MM")
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"{spec.get('label') or key}: giờ không hợp lệ")
        return f"{hh:02d}:{mm:02d}"
    text = str(value or "").strip()
    if len(text) > 20000:
        raise ValueError(f"{spec.get('label') or key}: nội dung quá dài")
    return text


def _validate_schedule(schedule: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(schedule or {})
    mode = str(raw.get("mode") or "manual").lower()
    if mode not in {"manual", "daily", "interval"}:
        raise ValueError("Scheduler mode không hợp lệ")
    if mode == "manual" or not bool(raw.get("enabled")):
        return {"enabled": False, "mode": "manual"}
    if mode == "interval":
        try:
            mins = int(raw.get("interval_minutes") or 60)
        except (TypeError, ValueError):
            raise ValueError("Interval phút không hợp lệ")
        if mins < 5 or mins > 10080:
            raise ValueError("Interval phải từ 5 đến 10080 phút")
        out = {"enabled": True, "mode": "interval", "interval_minutes": mins}
    else:
        slots: list[str] = []
        for value in raw.get("daily_slots") or []:
            text = str(value).strip()
            try:
                hh, mm = [int(x) for x in text.split(":")[:2]]
            except Exception:
                raise ValueError(f"Daily slot sai: {text}")
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError(f"Daily slot sai: {text}")
            slots.append(f"{hh:02d}:{mm:02d}")
        slots = sorted(set(slots))
        if not slots:
            raise ValueError("Daily cần ít nhất một khung giờ")
        out = {"enabled": True, "mode": "daily", "daily_slots": slots}
    next_run = raw.get("next_run_at")
    if next_run:
        try:
            datetime.fromisoformat(str(next_run).replace("Z", "+00:00"))
            out["next_run_at"] = str(next_run)
        except Exception:
            pass
    return out


def _deep_merge(a: dict[str, Any], b: dict[str, Any] | None) -> dict[str, Any]:
    out = json.loads(json.dumps(a or {}))
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Plugin:
    id: str
    slug: str
    name: str
    engine: str
    description: str
    defaults: dict[str, Any]
    schema: dict[str, Any]
    adapter: Any


class JobManager:
    def __init__(self, engine: EngineFacade, flow_broker: Any | None = None) -> None:
        self.engine = engine
        self.flow_broker = flow_broker
        self.plugins: dict[str, Plugin] = {}
        self.tasks: set[asyncio.Task] = set()
        self._scheduler_stop = asyncio.Event()
        # Durable RUN queue: HTTP only inserts SQLite rows. Background workers claim them.
        self._run_queue_event = asyncio.Event()
        # Legacy engines contain shared state/files. Serialize each Job Type while still keeping a durable multi-worker queue.
        self._engine_locks: dict[str, asyncio.Lock] = {}
        self._engine_owners: dict[str, str] = {}

    def load_plugins(self) -> None:
        self.plugins.clear()
        for manifest_path in sorted(PLUGINS_DIR.glob("*/manifest.json")):
            try:
                m = json.loads(manifest_path.read_text("utf-8"))
                pid = str(m["id"])
                slug = str(m["slug"])
                adapter_path = manifest_path.parent / "adapter.py"
                spec = importlib.util.spec_from_file_location(f"v28_job_{slug}", adapter_path)
                if not spec or not spec.loader:
                    raise RuntimeError(f"Không load được {adapter_path}")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                plugin = Plugin(
                    id=pid, slug=slug, name=str(m["name"]), engine=str(m["engine"]),
                    description=str(m.get("description") or ""), defaults=m.get("defaults") or {},
                    schema=m.get("schema") or {}, adapter=mod.Adapter(),
                )
                self.plugins[pid] = plugin
            except Exception as exc:
                db.log_event(f"Plugin load fail {manifest_path}: {exc}", level="ERROR", kind="plugin")
        self._sync_templates()

    def _sync_templates(self) -> None:
        now = db.now_iso()
        with db.connect() as c:
            for p in self.plugins.values():
                c.execute(
                    """INSERT INTO job_templates(id,slug,name,engine,description,defaults_json,schema_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET slug=excluded.slug,name=excluded.name,engine=excluded.engine,
                         description=excluded.description,defaults_json=excluded.defaults_json,schema_json=excluded.schema_json,updated_at=excluded.updated_at""",
                    (p.id, p.slug, p.name, p.engine, p.description, db.dumps(p.defaults), db.dumps(p.schema), now, now),
                )
                exists = c.execute("SELECT id FROM job_instances WHERE template_id=? LIMIT 1", (p.id,)).fetchone()
                if not exists:
                    iid = f"{p.id}.1"
                    c.execute(
                        """INSERT INTO job_instances(id,template_id,name,enabled,config_json,schedule_json,created_at,updated_at)
                           VALUES(?,?,?,1,?,'{}',?,?)""",
                        (iid, p.id, f"{p.name} {iid}", db.dumps(p.defaults), now, now),
                    )
                    c.execute("UPDATE job_instances SET template_version=(SELECT version FROM job_templates WHERE id=?) WHERE id=?", (p.id, iid))

    def templates(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.plugins.values(), key=lambda x: tuple(int(i) if i.isdigit() else 0 for i in x.id.split("."))):
            count = db.row("SELECT COUNT(*) n FROM job_instances WHERE template_id=? AND archived=0", (p.id,)) or {"n": 0}
            out.append({
                "id": p.id, "slug": p.slug, "name": p.name, "engine": p.engine,
                "description": p.description, "defaults": p.defaults, "schema": p.schema,
                "instance_count": int(count["n"]),
            })
        return out

    def _instance_from_row(self, r: dict[str, Any]) -> dict[str, Any]:
        d = dict(r)
        d["enabled"] = bool(d.get("enabled"))
        stored_config = db.loads(d.pop("config_json", "{}"), {})
        d["schedule"] = db.loads(d.pop("schedule_json", "{}"), {})
        d["pages"] = self.instance_pages(d["id"])
        p = self.plugins.get(str(d["template_id"]))
        # Always overlay stored values on the latest template defaults. This lets an older 2.1/3.1
        # automatically gain prompts/settings added by V2.8.1 without deleting its custom values.
        d["config"] = _deep_merge(p.defaults, stored_config) if p else stored_config
        d["template"] = {"id": p.id, "slug": p.slug, "name": p.name, "engine": p.engine} if p else None
        last = db.row("SELECT id,status,created_at,updated_at,error FROM runs WHERE instance_id=? ORDER BY created_at DESC LIMIT 1", (d["id"],))
        d["last_run"] = last
        return d

    def list_instances(self) -> list[dict[str, Any]]:
        rs = db.rows("SELECT * FROM job_instances WHERE archived=0 ORDER BY CAST(template_id AS INTEGER), id")
        return [self._instance_from_row(x) for x in rs]

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        r = db.row("SELECT * FROM job_instances WHERE id=? AND archived=0", (instance_id,))
        return self._instance_from_row(r) if r else None

    def instance_pages(self, instance_id: str) -> list[dict[str, Any]]:
        rs = db.rows(
            """SELECT m.*,p.name,p.enabled AS page_enabled,p.tasks_json,p.last_test_json
               FROM instance_pages m JOIN facebook_pages p ON p.id=m.page_id
               WHERE m.instance_id=? AND p.archived=0 ORDER BY p.name,p.id""",
            (instance_id,),
        )
        for x in rs:
            x["enabled"] = bool(x.get("enabled"))
            x["page_enabled"] = bool(x.get("page_enabled"))
            x["tasks"] = db.loads(x.pop("tasks_json", "[]"), [])
            x["last_test"] = db.loads(x.pop("last_test_json", None), None)
        return rs

    def set_pages(self, instance_id: str, page_ids: list[str]) -> None:
        if not db.row("SELECT id FROM job_instances WHERE id=? AND archived=0", (instance_id,)):
            raise KeyError(instance_id)
        requested = list(dict.fromkeys(str(x).strip() for x in page_ids if str(x).strip()))
        if requested:
            marks = ",".join("?" for _ in requested)
            valid = {str(r["id"]) for r in db.rows(f"SELECT id FROM facebook_pages WHERE archived=0 AND id IN ({marks})", tuple(requested))}
            missing = [x for x in requested if x not in valid]
            if missing:
                raise ValueError(f"Facebook Page không tồn tại/đã xóa: {', '.join(missing[:8])}")
        ts = db.now_iso()
        with db.connect() as c:
            old = {str(r["page_id"]): dict(r) for r in c.execute("SELECT * FROM instance_pages WHERE instance_id=?", (instance_id,)).fetchall()}
            c.execute("DELETE FROM instance_pages WHERE instance_id=?", (instance_id,))
            for pid in requested:
                prev = old.get(pid, {})
                c.execute(
                    "INSERT INTO instance_pages(instance_id,page_id,enabled,publish_delay_seconds,caption_suffix,created_at) VALUES(?,?,?,?,?,?)",
                    (instance_id, pid, int(prev.get("enabled", 1)), int(prev.get("publish_delay_seconds") or 0), str(prev.get("caption_suffix") or ""), ts),
                )

    def update_instance(self, instance_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_instance(instance_id)
        if not current:
            raise KeyError(instance_id)
        fields: dict[str, Any] = {}
        if payload.get("name") is not None:
            fields["name"] = str(payload["name"]).strip() or current["name"]
        if payload.get("enabled") is not None:
            fields["enabled"] = 1 if bool(payload["enabled"]) else 0
        if payload.get("config") is not None:
            patch = dict(payload.get("config") or {})
            plugin = self.plugins.get(str(current["template_id"]))
            if plugin:
                unknown = [key for key in patch if key not in plugin.schema and key not in plugin.defaults]
                if unknown:
                    raise ValueError(f"Setting không hợp lệ: {', '.join(unknown[:8])}")
                for key, value in list(patch.items()):
                    if key in plugin.schema:
                        patch[key] = _coerce_config_value(key, plugin.schema[key], value)
            merged_config = _deep_merge(current["config"], patch)
            if plugin and str(plugin.id) == "1":
                if float(merged_config.get("hook_duration_min") or 0) > float(merged_config.get("hook_duration_max") or 0):
                    raise ValueError("Hook tối thiểu không được lớn hơn Hook tối đa")
            fields["config_json"] = db.dumps(merged_config)
        if payload.get("schedule") is not None:
            fields["schedule_json"] = db.dumps(_validate_schedule(payload.get("schedule") or {}))
        if fields:
            fields["updated_at"] = db.now_iso()
            cols = ",".join(f"{k}=?" for k in fields)
            with db.connect() as c:
                c.execute(f"UPDATE job_instances SET {cols} WHERE id=?", (*fields.values(), instance_id))
        if payload.get("page_ids") is not None:
            self.set_pages(instance_id, list(payload.get("page_ids") or []))
        out = self.get_instance(instance_id)
        assert out
        return out

    def next_clone_id(self, template_id: str) -> str:
        rs = db.rows("SELECT id FROM job_instances WHERE template_id=?", (template_id,))
        nums = []
        for r in rs:
            raw = str(r["id"])
            try:
                nums.append(int(raw.split(".", 1)[1]))
            except Exception:
                pass
        return f"{template_id}.{max(nums or [0]) + 1}"

    @staticmethod
    def _next_clone_id_in_connection(c: Any, template_id: str) -> str:
        rows = c.execute("SELECT id FROM job_instances WHERE template_id=?", (template_id,)).fetchall()
        nums: list[int] = []
        for row in rows:
            try:
                nums.append(int(str(row[0]).split(".", 1)[1]))
            except (ValueError, IndexError):
                continue
        return f"{template_id}.{max(nums or [0]) + 1}"

    def clone_instance(self, source_id: str, name: str | None = None) -> dict[str, Any]:
        src = self.get_instance(source_id)
        if not src:
            raise KeyError(source_id)
        ts = db.now_iso()
        with db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            iid = self._next_clone_id_in_connection(c, str(src["template_id"]))
            clone_name = str(name or f"{src['template']['name']} {iid}")
            clone_schedule = dict(src.get("schedule") or {})
            clone_schedule.pop("next_run_at", None)
            c.execute(
                """INSERT INTO job_instances(id,template_id,name,enabled,config_json,engine_ref,schedule_json,created_at,updated_at)
                   VALUES(?,?,?,1,?,NULL,?,?,?)""",
                (iid, src["template_id"], clone_name, db.dumps(src["config"]), db.dumps(clone_schedule), ts, ts),
            )
            for page in src["pages"]:
                c.execute(
                    """INSERT INTO instance_pages(instance_id,page_id,enabled,publish_delay_seconds,caption_suffix,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (iid, page["page_id"], int(page.get("enabled", 1)), int(page.get("publish_delay_seconds") or 0), str(page.get("caption_suffix") or ""), ts),
                )
            c.commit()
        # Clone persona files so 1.x/2.x instances are independent instead of sharing source paths.
        copied: dict[str, str] = {}
        src_cfg = dict(src["config"])
        dst_folder = ASSET_ROOT / iid.replace(".", "_")
        for key in ("persona_path", "persona_left_path", "persona_right_path", "persona_back_path"):
            raw = str(src_cfg.get(key) or "").strip()
            if not raw:
                continue
            sp = Path(raw)
            if sp.is_file():
                dst_folder.mkdir(parents=True, exist_ok=True)
                dp = dst_folder / f"{key}{sp.suffix.lower()}"
                shutil.copy2(sp, dp)
                copied[key] = str(dp.resolve())
        if copied:
            with db.connect() as c:
                clone_row = c.execute("SELECT config_json FROM job_instances WHERE id=?", (iid,)).fetchone()
                cfg = db.loads(clone_row[0] if clone_row else "{}", {})
                cfg.update(copied)
                c.execute("UPDATE job_instances SET config_json=?,updated_at=? WHERE id=?", (db.dumps(cfg), db.now_iso(), iid))
        db.log_event(f"Clone {source_id} → {iid}", kind="job", instance_id=iid)
        out = self.get_instance(iid)
        assert out
        return out

    def create_instance(self, template_id: str, name: str | None = None, config: dict[str, Any] | None = None,
                        page_ids: list[str] | None = None) -> dict[str, Any]:
        p = self.plugins.get(str(template_id))
        if not p:
            raise KeyError(template_id)
        ts = db.now_iso()
        cfg_patch = dict(config or {})
        unknown = [key for key in cfg_patch if key not in p.schema and key not in p.defaults]
        if unknown:
            raise ValueError(f"Setting không hợp lệ: {', '.join(unknown[:8])}")
        for key, value in list(cfg_patch.items()):
            if key in p.schema:
                cfg_patch[key] = _coerce_config_value(key, p.schema[key], value)
        cfg = _deep_merge(p.defaults, cfg_patch)
        if str(p.id) == "1" and float(cfg.get("hook_duration_min") or 0) > float(cfg.get("hook_duration_max") or 0):
            raise ValueError("Hook tối thiểu không được lớn hơn Hook tối đa")
        with db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            iid = self._next_clone_id_in_connection(c, p.id)
            c.execute(
                """INSERT INTO job_instances(id,template_id,name,enabled,config_json,schedule_json,created_at,updated_at)
                   VALUES(?,?,?,1,?,'{}',?,?)""",
                (iid, p.id, str(name or f"{p.name} {iid}"), db.dumps(cfg), ts, ts),
            )
            c.commit()
        if page_ids:
            self.set_pages(iid, page_ids)
        out = self.get_instance(iid)
        assert out
        return out

    def delete_instance(self, instance_id: str) -> dict[str, Any]:
        active = db.row("SELECT id FROM runs WHERE instance_id=? AND status IN ('queued','waiting_flow','waiting_engine','dispatching','preparing','running','rendering') LIMIT 1", (instance_id,))
        if active:
            raise RuntimeError("Job đang chạy; không xóa instance lúc này")
        # Soft archive keeps Runs/Output/Facebook history intact (old code cascaded and erased it).
        n = db.execute("UPDATE job_instances SET archived=1,enabled=0,updated_at=? WHERE id=? AND archived=0", (db.now_iso(), instance_id))
        return {"ok": True, "deleted": bool(n), "archived": bool(n), "id": instance_id}

    def set_engine_ref(self, instance_id: str, ref: str | None) -> None:
        with db.connect() as c:
            c.execute("UPDATE job_instances SET engine_ref=?,updated_at=? WHERE id=?", (ref, db.now_iso(), instance_id))

    def _update_run(self, run_id: str, **fields: Any) -> None:
        fields["updated_at"] = db.now_iso()
        if "engine_job_ids" in fields:
            fields["engine_job_ids_json"] = db.dumps(fields.pop("engine_job_ids"))
        if "output" in fields:
            fields["output_json"] = db.dumps(fields.pop("output"))
        cols = ",".join(f"{k}=?" for k in fields)
        with db.connect() as c:
            c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))

    @staticmethod
    def _is_retryable_engine_error(error: str) -> bool:
        text = str(error or "").lower()
        retry_tokens = (
            "err_network_changed", "err_name_not_resolved", "timeout", "timed out",
            "loading", "connection", "temporarily", "429", "502", "503", "504",
            "public_error_minor", "invalid_argument", "flow tải", "flow load",
        )
        return any(token in text for token in retry_tokens)

    def _auto_requeue_run(self, run_id: str, instance: dict[str, Any], error: str) -> bool:
        cfg = instance.get("config") or {}
        max_attempts = int(cfg.get("auto_retry_attempts") or 3)
        run = self.get_run(run_id) or {}
        attempt = int(run.get("attempt") or 0)
        if attempt >= max_attempts or not self._is_retryable_engine_error(error):
            return False
        delay = min(180, 10 * (2 ** max(0, attempt - 1)))
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        self._update_run(
            run_id,
            status="waiting_flow",
            worker_id=None,
            error=f"AUTO RETRY {attempt + 1}/{max_attempts} sau {delay}s · {str(error)[:1000]}",
            heartbeat_at=db.now_iso(),
        )
        db.log_event(f"AUTO RETRY RUN {attempt + 1}/{max_attempts} at {retry_at} · {error}", level="WARNING", kind="job", instance_id=instance["id"], run_id=run_id)
        return True

    def _run_public(self, r: dict[str, Any]) -> dict[str, Any]:
        d = dict(r)
        d["engine_job_ids"] = db.loads(d.pop("engine_job_ids_json", "[]"), [])
        d["output"] = db.loads(d.pop("output_json", "{}"), {})
        d["publish_jobs"] = db.rows(
            """SELECT p.id,p.page_id,p.status,p.video_path,p.fb_video_id,p.error,p.retry_count,p.created_at,p.updated_at,
                      f.name AS page_name
               FROM publish_jobs p LEFT JOIN facebook_pages f ON f.id=p.page_id
               WHERE p.run_id=? ORDER BY p.created_at,p.page_id""",
            (d["id"],),
        )
        d["orchestrator_steps"] = server_features.list_steps(str(d["id"]))
        d["scene_checkpoints"] = server_features.list_checkpoints(str(d["id"]))
        return d

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        r = db.row("SELECT * FROM runs WHERE id=?", (run_id,))
        return self._run_public(r) if r else None

    def list_runs(self, limit: int = 100, instance_id: str | None = None) -> list[dict[str, Any]]:
        if instance_id:
            rs = db.rows("SELECT * FROM runs WHERE instance_id=? ORDER BY created_at DESC LIMIT ?", (instance_id, max(1, min(limit, 500))))
        else:
            rs = db.rows("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),))
        if not rs:
            return []
        run_ids = [str(x["id"]) for x in rs]
        marks = ",".join("?" for _ in run_ids)
        pubs = db.rows(
            f"""SELECT p.id,p.run_id,p.page_id,p.status,p.video_path,p.fb_video_id,p.error,p.retry_count,p.created_at,p.updated_at,
                       f.name AS page_name
                FROM publish_jobs p LEFT JOIN facebook_pages f ON f.id=p.page_id
                WHERE p.run_id IN ({marks}) ORDER BY p.created_at,p.page_id""",
            tuple(run_ids),
        )
        by_run: dict[str, list[dict[str, Any]]] = {}
        for p in pubs:
            by_run.setdefault(str(p["run_id"]), []).append(p)
        out: list[dict[str, Any]] = []
        for r in rs:
            d = dict(r)
            d["engine_job_ids"] = db.loads(d.pop("engine_job_ids_json", "[]"), [])
            d["output"] = db.loads(d.pop("output_json", "{}"), {})
            d["publish_jobs"] = by_run.get(str(d["id"]), [])
            out.append(d)
        return out

    def run_instance(self, instance_id: str, *, trigger: str = "manual") -> dict[str, Any]:
        instance = self.get_instance(instance_id)
        if not instance:
            raise KeyError(instance_id)
        if not instance["enabled"]:
            raise RuntimeError("Job instance đang OFF")
        if str(instance.get("template_id")) == "2":
            persona = str((instance.get("config") or {}).get("persona_path") or "").strip()
            pp = Path(persona) if persona else None
            if not pp or not pp.is_file() or pp.stat().st_size < 512:
                raise RuntimeError("Job 2 cáº§n Import Persona Front trÆ°á»›c khi RUN")
        run_id = f"run_{instance_id.replace('.', '_')}_{uuid.uuid4().hex[:12]}"
        ts = db.now_iso()
        active = None
        with db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            active_row = c.execute(
                "SELECT id,status FROM runs WHERE instance_id=? AND status IN ('queued','waiting_flow','waiting_engine','dispatching','preparing','running','rendering') ORDER BY created_at DESC LIMIT 1",
                (instance_id,),
            ).fetchone()
            if active_row:
                active = dict(active_row)
            else:
                c.execute(
                    """INSERT INTO runs(id,instance_id,template_id,engine,status,trigger,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (run_id, instance_id, instance["template_id"], instance["template"]["engine"], "queued", str(trigger or "manual")[:80], ts, ts),
                )
                c.execute("INSERT INTO run_steps(id,run_id,step_key,status,detail,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (f"{run_id}:queued", run_id, "queued", "done", "Run queued", "{}", ts, ts))
            c.commit()
        if active:
            self._run_queue_event.set()
            return {"ok": True, "deduped": True, "run_id": active["id"], "status": active["status"]}
        db.log_event(f"RUN {instance_id} · {trigger}", kind="job", instance_id=instance_id, run_id=run_id)
        _safe_print(f"[V2.8 JOB] QUEUED {instance_id} · {run_id} · {trigger}", flush=True)
        self._run_queue_event.set()
        return {"ok": True, "run_id": run_id, "status": "queued"}

    def _flow_ready_for_template(self, template_id: str) -> tuple[bool, str | None]:
        source = {"2": "beauty", "3": "parenting"}.get(str(template_id))
        if not source:
            return True, None
        b = self.flow_broker
        if not b:
            return False, "Flow broker chÆ°a khá»Ÿi táº¡o"
        src = b.sources.get(source) if getattr(b, "sources", None) else None
        if not src or not bool(src.connected):
            return False, f"Bridge ná»™i bá»™ {source} chÆ°a ONLINE"
        if not b.extension_ready():
            return False, "FLOW_WORKER chÆ°a ONLINE/heartbeat stale"
        return True, None

    def _claim_next_queued_run(self, worker_id: int) -> dict[str, Any] | None:
        # IMPORTANT: transaction contains SQL only on one connection. No nested db.log_event()
        # or other writer while BEGIN IMMEDIATE is held (that deadlocked an earlier build).
        wait_log: tuple[str, str, str] | None = None
        stale_log: tuple[str, str, str] | None = None
        claimed: dict[str, Any] | None = None
        with db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT id,instance_id,template_id,status FROM runs WHERE status IN ('queued','waiting_flow','waiting_engine') ORDER BY created_at ASC LIMIT 50"
            ).fetchall()
            if not rows:
                c.commit()
                return None
            ts = db.now_iso()
            engine_wait_log: tuple[str, str, str] | None = None
            for row in rows:
                ready, reason = self._flow_ready_for_template(str(row["template_id"]))
                if not ready:
                    if str(row["status"]) != "waiting_flow":
                        c.execute(
                            "UPDATE runs SET status='waiting_flow',error=?,updated_at=? WHERE id=? AND status IN ('queued','waiting_flow','waiting_engine')",
                            (reason, ts, row["id"]),
                        )
                        wait_log = (str(row["id"]), str(row["instance_id"]), str(reason or "Flow chÆ°a ready"))
                    continue
                busy = c.execute(
                    "SELECT id,instance_id,status,heartbeat_at,updated_at FROM runs WHERE template_id=? AND id<>? AND status IN ('dispatching','preparing','running','rendering') ORDER BY updated_at DESC LIMIT 1",
                    (str(row["template_id"]), str(row["id"])),
                ).fetchone()
                if busy:
                    # A healthy engine run updates heartbeat every 10s. If the task vanished while
                    # the FastAPI process stayed alive, the DB row used to block this template forever.
                    # Reap only clearly stale owners; 90s is far above the normal heartbeat cadence.
                    stamp = str(busy["heartbeat_at"] or busy["updated_at"] or "")
                    stale = False
                    if stamp:
                        try:
                            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            stale = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() > 90
                        except Exception:
                            stale = False
                    if stale:
                        stale_reason = f"stale engine owner {busy['instance_id']}/{busy['id']} · heartbeat >90s"
                        c.execute(
                            "UPDATE runs SET status='interrupted',error=?,finished_at=?,updated_at=? WHERE id=? AND status IN ('dispatching','preparing','running','rendering')",
                            (stale_reason, ts, ts, busy["id"]),
                        )
                        stale_log = (str(busy["id"]), str(busy["instance_id"]), stale_reason)
                    else:
                        reason_engine = f"engine {row['template_id']} bận bởi {busy['instance_id']}/{busy['id']} · {busy['status']}"
                        if str(row["status"]) != "waiting_engine":
                            c.execute(
                                "UPDATE runs SET status='waiting_engine',error=?,worker_id=NULL,updated_at=? WHERE id=? AND status IN ('queued','waiting_flow','waiting_engine')",
                                (reason_engine, ts, row["id"]),
                            )
                            engine_wait_log = (str(row["id"]), str(row["instance_id"]), reason_engine)
                        continue
                cur = c.execute(
                    "UPDATE runs SET status='dispatching',error=NULL,worker_id=?,attempt=attempt+1,heartbeat_at=?,updated_at=? WHERE id=? AND status IN ('queued','waiting_flow','waiting_engine')",
                    (f"W{worker_id}", ts, ts, row["id"]),
                )
                if cur.rowcount == 1:
                    claimed = {"id": str(row["id"]), "instance_id": str(row["instance_id"])}
                    break
            c.commit()
        if stale_log:
            rid, iid, reason = stale_log
            db.log_event(f"STALE ENGINE RECOVER {rid} · {reason}", level="WARNING", kind="job", instance_id=iid, run_id=rid)
            _safe_print(f"[V2.8 JOB] STALE ENGINE RECOVER {iid} · {rid} · {reason}", flush=True)
        if wait_log:
            rid, iid, reason = wait_log
            db.log_event(f"WAIT FLOW {rid} · {reason}", kind="job", instance_id=iid, run_id=rid)
            _safe_print(f"[V2.8 JOB] WAIT FLOW {iid} · {rid} · {reason}", flush=True)
        if 'engine_wait_log' in locals() and engine_wait_log:
            rid, iid, reason = engine_wait_log
            db.log_event(f"WAIT ENGINE {rid} · {reason}", kind="job", instance_id=iid, run_id=rid)
            _safe_print(f"[V2.8 JOB] WAIT ENGINE {iid} · {rid} · {reason}", flush=True)
        return claimed

    async def run_worker_loop(self, worker_id: int) -> None:
        _safe_print(f"[V2.8 JOB] DB WORKER {worker_id} READY", flush=True)
        while True:
            try:
                claimed = self._claim_next_queued_run(worker_id)
                if claimed:
                    rid, iid = claimed["id"], claimed["instance_id"]
                    db.log_event(f"WORKER {worker_id} CLAIM {rid}", kind="job", instance_id=iid, run_id=rid)
                    _safe_print(f"[V2.8 JOB] CLAIM W{worker_id} {iid} · {rid}", flush=True)
                    await self._execute_run(rid)
                    continue
                self._run_queue_event.clear()
                # Only brand-new queued rows should wake immediately. waiting_flow is polled at 1 Hz;
                # treating waiting_flow as immediate work caused a hot loop that starved the API.
                if db.row("SELECT id FROM runs WHERE status='queued' LIMIT 1"):
                    self._run_queue_event.set()
                    continue
                try:
                    await asyncio.wait_for(self._run_queue_event.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.log_event(f"Run worker {worker_id}: {exc}", level="ERROR", kind="job")
                _safe_print(f"[V2.8 JOB] WORKER {worker_id} ERROR · {exc}", flush=True)
                await asyncio.sleep(0.5)

    async def _run_heartbeat(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(10)
            self._update_run(run_id, heartbeat_at=db.now_iso())

    async def _execute_run(self, run_id: str) -> None:
        run = self.get_run(run_id)
        if not run:
            return
        instance = self.get_instance(run["instance_id"])
        if not instance:
            self._update_run(run_id, status="failed", error="Instance đã bị xóa", finished_at=db.now_iso())
            return
        plugin = self.plugins.get(str(instance["template_id"]))
        if not plugin:
            self._update_run(run_id, status="failed", error="Plugin không tồn tại", finished_at=db.now_iso())
            return
        engine_lock_key = str(plugin.engine or plugin.id)
        lock = self._engine_locks.setdefault(engine_lock_key, asyncio.Lock())
        heartbeat = asyncio.create_task(self._run_heartbeat(run_id), name=f"heartbeat-{run_id}")
        try:
            # Claim logic serializes each engine type in SQLite. The in-memory lock is only
            # a final safety net. Never park a DB worker indefinitely on an asyncio.Lock.
            if lock.locked():
                owner = self._engine_owners.get(engine_lock_key) or "unknown"
                self._update_run(run_id, status="waiting_engine", worker_id=None, error=f"engine {plugin.slug} lock báº­n bá»Ÿi {owner}")
                db.log_event(f"WAIT ENGINE {run_id} · {plugin.slug} lock owner={owner}", kind="job", instance_id=instance["id"], run_id=run_id)
                self._run_queue_event.set()
                return
            await lock.acquire()
            self._engine_owners[engine_lock_key] = run_id
            try:
                latest = self.get_run(run_id)
                if not latest or str(latest.get("status") or "") != "dispatching":
                    return
                self._update_run(run_id, status="preparing", started_at=db.now_iso(), heartbeat_at=db.now_iso(), error=None)
                _safe_print(f"[V2.8 JOB] START {instance['id']} · {run_id} · {plugin.slug}", flush=True)
                db.log_event(f"{instance['id']} prepare → {plugin.slug}", kind="job", instance_id=instance["id"], run_id=run_id)
                resume_jid = str(latest.get("engine_run_id") or "").strip() or None
                try:
                    started = await plugin.adapter.start(self, instance, resume_job_id=resume_jid)
                except TypeError:
                    started = await plugin.adapter.start(self, instance)
                server_features.step(run_id, "flow", "running", str(started.get("engine_run_id") or "engine started"))
                self._update_run(
                    run_id, status="running", engine_run_id=started.get("engine_run_id"),
                    engine_job_ids=started.get("engine_job_ids") or [], output={"engine_start": started},
                )
                db.log_event(f"{instance['id']} engine started · {started.get('engine_run_id')}", kind="job", instance_id=instance["id"], run_id=run_id)
                _safe_print(f"[V2.8 JOB] ENGINE {instance['id']} · {started.get('engine_run_id')}", flush=True)
                result = await plugin.adapter.wait(self, instance, started)
                server_features.step(run_id, "download", "done", "Engine returned media paths")
                server_features.step(run_id, "merge", "done", "Engine render completed")
                videos = [str(Path(str(x)).resolve()) for x in result.get("video_paths") or [] if x]
                if not videos:
                    raise RuntimeError("Engine báo xong nhưng không có video output")
                missing = [x for x in videos if not Path(x).is_file() or Path(x).stat().st_size < 1024]
                if missing:
                    raise RuntimeError(f"Engine trả output không tồn tại/rỗng: {missing[:3]}")
                validation = server_features.validate_output(videos, min_seconds=4.0, max_seconds=180.0)
                server_features.step(run_id, "validate", "done" if validation.get("ok") else "failed", "Output validator", validation)
                if not validation.get("ok"):
                    raise RuntimeError(f"Output validator failed: {validation}")
                for idx, video_path in enumerate(videos, 1):
                    server_features.checkpoint(run_id, f"video_{idx}", "final", "done", output_path=video_path, payload={"validator": validation.get("checks", [])[idx-1] if idx-1 < len(validation.get("checks", [])) else {}})
                title = str(result.get("title") or instance["name"])
                caption = server_features.enforce_caption_affiliate(str(result.get("caption") or ""), instance["config"])
                server_features.step(run_id, "caption", "done", "Caption checked affiliate_url")
                output = {"video_paths": videos, "engine": result.get("raw") or {}, "render_once": True, "validation": validation}
                self._update_run(run_id, status="rendered", output=output, title=title, caption=caption)
                pages = [p for p in self.instance_pages(instance["id"]) if p.get("enabled") and p.get("page_enabled")]
                server_features.step(run_id, "publish", "queued" if pages else "skipped", f"{len(pages)} page(s)")
                if not pages:
                    self._update_run(run_id, status="done_no_pages", finished_at=db.now_iso())
                    db.log_event(f"{instance['id']} render xong · chưa gắn Page", kind="job", instance_id=instance["id"], run_id=run_id)
                    return
                dry_run = bool(instance["config"].get("facebook_dry_run", False))
                now = datetime.now(timezone.utc)
                for p in pages:
                    suffix = str(p.get("caption_suffix") or "").strip()
                    desc = (caption + ("\n\n" + suffix if suffix else "")).strip()
                    delay = max(0, int(p.get("publish_delay_seconds") or 0))
                    for idx, path in enumerate(videos):
                        pid = facebook.enqueue_publish(run_id, p["page_id"], path, title, desc, dry_run=dry_run)
                        if delay or idx:
                            retry_at = (now + timedelta(seconds=delay + idx * 10)).isoformat(timespec="seconds")
                            with db.connect() as c:
                                c.execute("UPDATE publish_jobs SET status='retry_wait',retry_after=?,updated_at=? WHERE id=?", (retry_at, db.now_iso(), pid))
                self._update_run(run_id, status="publish_queued")
                db.log_event(f"{instance['id']} render 1 lần → queue {len(pages)} Page", kind="facebook", instance_id=instance["id"], run_id=run_id, payload={"pages": [p["page_id"] for p in pages], "videos": len(videos)})
            finally:
                if self._engine_owners.get(engine_lock_key) == run_id:
                    self._engine_owners.pop(engine_lock_key, None)
                if lock.locked():
                    lock.release()
                self._run_queue_event.set()
        except asyncio.CancelledError:
            self._update_run(run_id, status="interrupted", error="Task cancelled", finished_at=db.now_iso())
            raise
        except Exception as exc:
            if self._auto_requeue_run(run_id, instance, str(exc)):
                self._run_queue_event.set()
                _safe_print(f"[V2.8 JOB] AUTO RETRY {instance['id']} · {run_id} · {exc}", flush=True)
                return
            self._update_run(run_id, status="failed", error=str(exc), finished_at=db.now_iso())
            db.log_event(str(exc), level="ERROR", kind="job", instance_id=instance["id"], run_id=run_id)
            _safe_print(f"[V2.8 JOB] FAILED {instance['id']} · {run_id} · {exc}", flush=True)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        status = str(run.get("status") or "")
        if status in {"queued", "waiting_flow", "waiting_engine", "dispatching", "preparing", "running", "rendering"}:
            # Cancel engine job if active
            for jid in run.get("engine_job_ids") or []:
                try:
                    self.broker.cancel_job(str(jid), reason="user_cancelled_run", run_id=run_id, attempt_id=str(run.get("attempt") or "1"))
                except Exception:
                    pass
            self._update_run(run_id, status="interrupted", error="Đã hủy bởi người dùng", finished_at=db.now_iso())
            # Release memory engine lock if owned
            template_id = str(run.get("template_id") or "")
            if self._engine_owners.get(template_id) == run_id:
                self._engine_owners.pop(template_id, None)
                lock = self._engine_locks.get(template_id)
                if lock and lock.locked():
                    try:
                        lock.release()
                    except RuntimeError:
                        pass
            self._run_queue_event.set()
            return {"ok": True, "run_id": run_id, "status": "interrupted"}
        return {"ok": True, "run_id": run_id, "status": status, "unchanged": True}

    def recover_orphaned_runs(self) -> int:
        """Queued and active work is durable. Auto-reconciles scene checkpoints and resumes interrupted runs."""
        queued = db.rows("SELECT id,instance_id,status,engine_run_id,attempt FROM runs WHERE status IN ('queued','waiting_flow','waiting_engine')")
        active = db.rows("SELECT id,instance_id,status,engine_run_id,attempt FROM runs WHERE status IN ('dispatching','preparing','running','rendering')")
        ts = db.now_iso()
        resumed_count = 0
        
        if active:
            with db.connect() as c:
                for r in active:
                    # Auto-resume: mark as queued for worker pickup while preserving attempt budget
                    new_attempt = int(r.get("attempt") or 0)
                    c.execute(
                        "UPDATE runs SET status='queued',attempt=?,error=NULL,updated_at=? WHERE id=?",
                        (new_attempt, ts, r["id"]),
                    )
                    resumed_count += 1
            for r in active:
                db.log_event(
                    f"STARTUP RESUME {r['id']} · {r['status']} → queued (auto-reconcile attempt {int(r.get('attempt') or 0)})",
                    level="INFO", kind="job", instance_id=str(r["instance_id"]), run_id=str(r["id"]),
                )
        for r in queued:
            db.log_event(
                f"STARTUP RESUME {r['id']} · {r['status']} → worker queue",
                kind="job", instance_id=str(r["instance_id"]), run_id=str(r["id"]),
            )
            resumed_count += 1
            
        if active or queued:
            self._run_queue_event.set()
            _safe_print(f"[V2.8 JOB] RECOVERY AUTO-RESUME · queued={len(queued)} active_resumed={len(active)}", flush=True)
        return resumed_count

    def _refresh_run_publish_state(self, run_id: str) -> None:
        jobs = db.rows("SELECT status,error FROM publish_jobs WHERE run_id=?", (run_id,))
        if not jobs:
            return
        statuses = [str(x["status"]) for x in jobs]
        active = {"queued", "retry_wait", "starting", "uploading", "finishing"}
        if any(s in active for s in statuses):
            if not any(s in {"starting", "uploading", "finishing"} for s in statuses):
                self._update_run(run_id, status="publish_queued")
            else:
                self._update_run(run_id, status="publishing")
            return
        ok = {"published", "dry_run_ok"}
        if all(s in ok for s in statuses):
            self._update_run(run_id, status="published", finished_at=db.now_iso(), error=None)
        elif any(s in ok for s in statuses):
            err = " | ".join(str(x.get("error") or "") for x in jobs if x.get("error"))[:4000]
            self._update_run(run_id, status="partial_failed", finished_at=db.now_iso(), error=err or "Má»™t sá»‘ Page publish lá»—i")
        else:
            err = " | ".join(str(x.get("error") or "") for x in jobs if x.get("error"))[:4000]
            self._update_run(run_id, status="publish_failed", finished_at=db.now_iso(), error=err or "Publish tháº¥t báº¡i")

    async def publisher_loop(self) -> None:
        while True:
            try:
                jobs = facebook.due_publish_jobs(3)
                for j in jobs:
                    pid = str(j["id"])
                    # claim atomically enough for single V2.8 publisher task
                    with db.connect() as c:
                        cur = c.execute("UPDATE publish_jobs SET status='starting',updated_at=? WHERE id=? AND status IN ('queued','retry_wait')", (db.now_iso(), pid))
                        if not cur.rowcount:
                            continue
                    await asyncio.to_thread(facebook.publish_one, pid)
                    self._refresh_run_publish_state(str(j["run_id"]))
                # Reconcile terminal jobs after restart as well.
                rs = db.rows("SELECT DISTINCT run_id FROM publish_jobs WHERE status IN ('published','dry_run_ok','failed') ORDER BY updated_at DESC LIMIT 50")
                for r in rs:
                    self._refresh_run_publish_state(str(r["run_id"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.log_event(f"Publisher loop: {exc}", level="ERROR", kind="facebook")
            await asyncio.sleep(2)

    async def scheduler_loop(self) -> None:
        """Small scheduler owned by V2.8. Legacy engine schedulers stay unused for these instances."""
        while not self._scheduler_stop.is_set():
            try:
                now = datetime.now(LOCAL_TZ)
                for inst in self.list_instances():
                    try:
                        if not inst["enabled"]:
                            continue
                        s = inst.get("schedule") or {}
                        if not s.get("enabled"):
                            continue
                        mode = str(s.get("mode") or "interval").lower()
                        next_raw = str(s.get("next_run_at") or "")
                        due = None
                        if next_raw:
                            try:
                                due = datetime.fromisoformat(next_raw.replace("Z", "+00:00"))
                                if due.tzinfo is None:
                                    due = due.replace(tzinfo=LOCAL_TZ)
                                due = due.astimezone(LOCAL_TZ)
                            except Exception:
                                due = None
                        def next_due(base_now: datetime) -> datetime:
                            base_local = base_now.astimezone(LOCAL_TZ)
                            if mode == "interval":
                                mins = max(5, int(s.get("interval_minutes") or 180))
                                return base_local + timedelta(minutes=mins)
                            slots = [str(x) for x in (s.get("daily_slots") or ["08:00", "14:00", "21:00"])]
                            candidates = []
                            for add_day in (0, 1):
                                base = base_local.date() + timedelta(days=add_day)
                                for slot in slots:
                                    try:
                                        hh, mm = [int(x) for x in slot.split(":")[:2]]
                                        candidates.append(datetime(base.year, base.month, base.day, hh, mm, tzinfo=LOCAL_TZ))
                                    except Exception:
                                        pass
                            return min((x for x in candidates if x > base_local + timedelta(seconds=30)), default=base_local + timedelta(days=1))

                        # Enabling a schedule must not unexpectedly render immediately. First pass only arms next_run_at.
                        if due is None:
                            nxt = next_due(now)
                            ns = dict(s); ns["next_run_at"] = nxt.astimezone(LOCAL_TZ).isoformat(timespec="seconds")
                            self.update_instance(inst["id"], {"schedule": ns})
                            continue
                        if due > now:
                            continue
                        self.run_instance(inst["id"], trigger="scheduler")
                        nxt = next_due(now)
                        ns = dict(s); ns["next_run_at"] = nxt.astimezone(LOCAL_TZ).isoformat(timespec="seconds")
                        self.update_instance(inst["id"], {"schedule": ns})
                    except Exception as inst_exc:
                        # One bad Job must not prevent every later Job in the scheduler tick.
                        db.log_event(f"Scheduler {inst.get('id')}: {inst_exc}", level="ERROR", kind="scheduler", instance_id=str(inst.get("id") or ""))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.log_event(f"Scheduler loop: {exc}", level="ERROR", kind="scheduler")
            try:
                await asyncio.wait_for(self._scheduler_stop.wait(), 5)
            except asyncio.TimeoutError:
                pass

    async def shutdown(self) -> None:
        self._scheduler_stop.set()
        for t in list(self.tasks):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()


