from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import db, facebook
from core.broker import FlowBroker, QueueItem, _sanitize_settings
from core.job_manager import JobManager, _coerce_config_value, _validate_schedule


class FakeEngine:
    pass


class FakeBroker:
    def __init__(self) -> None:
        self.ready = False
        self.sources = {
            "beauty": SimpleNamespace(connected=True),
            "parenting": SimpleNamespace(connected=True),
        }

    def extension_ready(self) -> bool:
        return self.ready


def assert_raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError(f"Expected {exc.__name__}: {getattr(fn, '__name__', fn)}")


def test_manager(tmp: Path) -> None:
    original = db.DB_PATH
    db.DB_PATH = tmp / "v28.sqlite3"
    try:
        db.init_db()
        broker = FakeBroker()
        m = JobManager(FakeEngine(), broker)
        m.load_plugins()
        ids = [x["id"] for x in m.list_instances()]
        assert "1.1" in ids and "2.1" in ids and "3.1" in ids and "4.1" in ids, ids

        # Validation: booleans, time, schedule, range/select and cross-field relationship.
        assert _coerce_config_value("x", {"type": "checkbox"}, "false") is False
        assert _coerce_config_value("x", {"type": "time"}, "7:05") == "07:05"
        assert_raises(ValueError, _coerce_config_value, "x", {"type": "time"}, "25:00")
        assert_raises(ValueError, _validate_schedule, {"enabled": True, "mode": "interval", "interval_minutes": 1})
        assert _validate_schedule({"enabled": True, "mode": "daily", "daily_slots": ["8:00", "08:00", "19:30"]})["daily_slots"] == ["08:00", "19:30"]
        assert_raises(ValueError, m.update_instance, "1.1", {"config": {"face_min_score": 999}})
        assert_raises(ValueError, m.update_instance, "1.1", {"config": {"does_not_exist": 1}})
        assert_raises(ValueError, m.update_instance, "1.1", {"config": {"hook_duration_min": 10, "hook_duration_max": 2}})

        # Clone schedule keeps rule but clears stale next_run_at.
        m.update_instance("1.1", {"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30, "next_run_at": "2026-08-14T01:00:00+00:00"}})
        clone = m.clone_instance("1.1")
        assert clone["id"] == "1.2"
        assert clone["schedule"]["mode"] == "interval"
        assert "next_run_at" not in clone["schedule"]

        # Concurrent clone IDs must remain unique.
        with ThreadPoolExecutor(max_workers=6) as ex:
            rows = list(ex.map(lambda _: m.clone_instance("1.1")["id"], range(6)))
        assert len(rows) == len(set(rows)), rows

        # Rapid concurrent RUN clicks result in exactly one active run.
        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(lambda _: m.run_instance("1.1", trigger="race-test"), range(8)))
        run_ids = {x["run_id"] for x in res}
        assert len(run_ids) == 1, run_ids
        rid = next(iter(run_ids))
        assert db.row("SELECT COUNT(*) n FROM runs WHERE instance_id='1.1' AND status='queued'")["n"] == 1

        # Soft archive preserves run history.
        m.cancel_run(rid)
        before = db.row("SELECT COUNT(*) n FROM runs WHERE instance_id='1.1'")["n"]
        m.delete_instance("1.1")
        after = db.row("SELECT COUNT(*) n FROM runs WHERE instance_id='1.1'")["n"]
        assert before == after and after >= 1
        assert m.get_instance("1.1") is None

        # Job 2 validation requires imported persona; then waits durably for Flow.
        assert_raises(RuntimeError, m.run_instance, "2.1")
        persona = tmp / "persona.jpg"
        persona.write_bytes(b"x" * 2048)
        m.update_instance("2.1", {"config": {"persona_path": str(persona)}})
        r2 = m.run_instance("2.1", trigger="flow-wait-test")
        assert m._claim_next_queued_run(1) is None
        assert m.get_run(r2["run_id"])["status"] == "waiting_flow"
        broker.ready = True
        claimed = m._claim_next_queued_run(1)
        assert claimed and claimed["id"] == r2["run_id"]
        assert m.get_run(r2["run_id"])["status"] == "dispatching"
        # Cancel a claimed run; executor must later see it as cancelled rather than replaying it.
        m.cancel_run(r2["run_id"])
        assert m.get_run(r2["run_id"])["status"] == "interrupted"

        # Page mapping dedupes and rejects unknown/archived pages.
        facebook.save_page("123456", "P", "token_1234567890")
        m.set_pages("2.1", ["123456", "123456"])
        assert len(m.instance_pages("2.1")) == 1
        facebook.delete_page("123456")
        assert db.row("SELECT COUNT(*) n FROM instance_pages WHERE page_id='123456'")["n"] == 0
        assert_raises(ValueError, m.set_pages, "2.1", ["123456"])

        # Publish rejects missing video before entering publisher queue.
        # Create a tiny run row for FK relation under an active instance.
        ts = db.now_iso()
        with db.connect() as c:
            c.execute("INSERT INTO runs(id,instance_id,template_id,engine,status,created_at,updated_at) VALUES('rpub','2.1','2','beauty','rendered',?,?)", (ts, ts))
        facebook.save_page("999999", "P2", "token_1234567890")
        assert_raises(ValueError, facebook.enqueue_publish, "rpub", "999999", str(tmp / "missing.mp4"))

    finally:
        db.DB_PATH = original


def test_db_migration_old_outbox(tmp: Path) -> None:
    original = db.DB_PATH
    db.DB_PATH = tmp / "old.sqlite3"
    try:
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db.DB_PATH)
        c.execute("CREATE TABLE flow_outbox(id TEXT PRIMARY KEY, source TEXT, message_type TEXT, payload_json TEXT, attempts INTEGER DEFAULT 0, last_error TEXT, created_at TEXT, updated_at TEXT)")
        c.commit(); c.close()
        db.init_db()
        check_conn = sqlite3.connect(db.DB_PATH)
        try:
            cols = {r[1] for r in check_conn.execute("PRAGMA table_info(flow_outbox)")}
        finally:
            check_conn.close()
        assert "dedupe_key" in cols
    finally:
        db.DB_PATH = original


async def test_broker(tmp: Path) -> None:
    original = db.DB_PATH
    db.DB_PATH = tmp / "broker.sqlite3"
    try:
        db.init_db()
        b = FlowBroker(3000)
        # A stale terminal result must not finish a different active job.
        b.active = QueueItem("q1", "beauty", {"type": "RUN_FLOW_JOB", "jobId": "new-job"}, 1.0, status="RUNNING")
        await b.handle_ext({"type": "FLOW_JOB_RESULT", "jobId": "old-job", "ok": True})
        assert b.active and b.active.job_id == "new-job"
        # Matching result finishes it; if source bridge is offline, durable outbox stores delivery.
        await b.handle_ext({"type": "FLOW_JOB_RESULT", "jobId": "new-job", "ok": True, "results": []})
        assert b.active is None
        out = db.rows("SELECT * FROM flow_outbox")
        assert len(out) == 1 and out[0]["source"] == "beauty"
        # Repeated identical terminal payload is deduped.
        b._queue_source_outbox("beauty", {"type": "FLOW_JOB_RESULT", "jobId": "new-job", "ok": True})
        assert db.row("SELECT COUNT(*) n FROM flow_outbox")["n"] == 1
        # Global setting sanitizer cannot crash or accept absurd values.
        s = _sanitize_settings({"imageConcurrency": "bad", "videoConcurrency": 99, "videoDuration": "99s", "imageModel": "bogus"})
        assert s["imageConcurrency"] == 9 and s["videoConcurrency"] == 10 and s["videoDuration"] == "8s"
        s = _sanitize_settings({"autoDownloadVideo": "false", "submitPolicy": "GLOBAL_FIFO", "imageTimeoutSec": 333, "videoTimeoutSec": 777, "systemicFailureLimit": 4})
        assert s["autoDownloadVideo"] is False
        assert s["submitPolicy"] == "GLOBAL_FIFO" and s["imageTimeoutSec"] == 333 and s["videoTimeoutSec"] == 777 and s["systemicFailureLimit"] == 4
    finally:
        db.DB_PATH = original


def test_static() -> None:
    bg = (ROOT / "extensions" / "FLOW_WORKER" / "background.js").read_text("utf-8")
    popup = (ROOT / "extensions" / "FLOW_WORKER" / "popup.js").read_text("utf-8")
    manifest = json.loads((ROOT / "extensions" / "FLOW_WORKER" / "manifest.json").read_text("utf-8"))
    assert manifest["version"].startswith(("14.7.", "14.8."))
    assert "serverMessages.length?9" not in bg
    assert "serverMessages.length?4" not in bg
    assert "imageConcurrency:9,videoConcurrency:4" not in bg.replace(" ", "")
    assert "flowConcurrencyPresetV1459:true" not in popup
    assert "imageTimeoutSec" in bg and "videoTimeoutSec" in bg and "systemicFailureLimit" in bg
    # Locale-aware Flow project routing: never classify /fx/vi/tools/flow/project/<id> as outside Flow.
    assert ("function isFlowToolUrl" in bg or "function isFlowToolTabUrl" in bg) and "tools/flow" in bg.replace("\\", "")
    assert "startsWith('https://labs.google/fx/tools/flow')" not in bg
    assert "isFlowProjectRootUrl" in bg and "projectIdFromFlowUrl" in bg
    # Port isolation: this worker is allowed to reach only localhost:3000.
    assert "LEGACY_SERVER_URL_RE" not in bg
    assert "DEFAULT_SERVER_URL" in bg
    local_perms = [x for x in manifest.get("host_permissions", []) if "127.0.0.1" in x or "localhost" in x]
    assert local_perms and all(":3000/" in x for x in local_perms), local_perms
    csp = manifest.get("content_security_policy", {}).get("extension_pages", "")
    assert "127.0.0.1:3000" in csp and "127.0.0.1:*" not in csp
    popup_html = (ROOT / "extensions" / "FLOW_WORKER" / "popup.html").read_text("utf-8")
    assert 'id="serverUrl" value="ws://127.0.0.1:3000/ws/flow"' in popup_html
    env_example = (ROOT / ".env.example").read_text("utf-8")
    assert "9ROUTER_API_KEY=" in env_example and "OPENAI_API_KEY=" not in env_example and "GEMINI_API_KEY=" not in env_example
    assert "server_console.log" in (ROOT / "run_server.py").read_text("utf-8")
    assert "V28_SUPERVISED" in (ROOT / "run_server.py").read_text("utf-8")
    # Job manifests must not reintroduce execution settings owned by the single global Flow worker.
    beauty = json.loads((ROOT / "job_types" / "beauty" / "manifest.json").read_text("utf-8"))
    parent = json.loads((ROOT / "job_types" / "parenting" / "manifest.json").read_text("utf-8"))
    forbidden = {"image_concurrency", "video_concurrency", "continuation_mode"}
    assert not (forbidden & set(beauty.get("schema", {})))
    assert not (forbidden & set(parent.get("schema", {})))


def test_env_loader() -> None:
    import os
    from core import env_loader
    import tempfile

    key_blank = "PEXELS_API_KEY"
    key_process = "PIXABAY_API_KEY"
    old_blank = os.environ.get(key_blank)
    old_process = os.environ.get(key_process)
    old_v28 = os.environ.get("V28_ENV_FILE")
    old_sources = dict(env_loader._SOURCES)
    try:
        with tempfile.TemporaryDirectory(prefix="v28-env-") as d:
            f = Path(d) / ".env"
            f.write_text("PEXELS_API_KEY=from-file\nPIXABAY_API_KEY=from-file-2\n", encoding="utf-8")
            os.environ["V28_ENV_FILE"] = str(f)
            os.environ[key_blank] = ""
            os.environ[key_process] = "process-wins"
            env_loader._SOURCES.clear()
            env_loader.load_project_env([key_blank, key_process])
            assert os.environ[key_blank] == "from-file"
            assert os.environ[key_process] == "process-wins"
            st = env_loader.env_status(key_blank, key_process)
            assert st[key_blank]["configured"] is True and st[key_blank]["source"] == "file"
            assert st[key_blank]["source_path"] == str(f)
            snap = env_loader.build_subprocess_env()
            assert snap[key_blank] == "from-file" and snap[key_process] == "process-wins"
    finally:
        env_loader._SOURCES.clear(); env_loader._SOURCES.update(old_sources)
        if old_blank is None: os.environ.pop(key_blank, None)
        else: os.environ[key_blank] = old_blank
        if old_process is None: os.environ.pop(key_process, None)
        else: os.environ[key_process] = old_process
        if old_v28 is None: os.environ.pop("V28_ENV_FILE", None)
        else: os.environ["V28_ENV_FILE"] = old_v28



def test_celebrity_prepare_resolved_scope(tmp: Path) -> None:
    """Regression: 2.8.5.7 referenced undefined `resolved` in prepare()."""
    from core.engine import EngineFacade
    from core import env_loader

    factory_db = tmp / "celebrity_factory.db"
    c = sqlite3.connect(factory_db)
    c.execute("""CREATE TABLE pages(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, page_id TEXT, token_env_key TEXT,
        enabled INTEGER, posts_per_day INTEGER, slot1 TEXT, slot2 TEXT, theme TEXT,
        celebrity_pool TEXT, output_mode TEXT, created_at TEXT, updated_at TEXT
    )""")
    c.commit(); c.close()

    envf = tmp / "celebrity.env"
    envf.write_text("SERPER_API_KEY=test-serper\n", encoding="utf-8")
    old_v28 = os.environ.get("V28_ENV_FILE")
    old_serper = os.environ.get("SERPER_API_KEY")
    old_sources = dict(env_loader._SOURCES)
    try:
        os.environ["V28_ENV_FILE"] = str(envf)
        os.environ["SERPER_API_KEY"] = ""
        env_loader._SOURCES.clear()
        facade = EngineFacade({})
        facade._celebrity_db_path = lambda: factory_db
        ref = facade.ensure_celebrity_profile(
            "1.1", "Job 1.1", {"celebrity_pool": ["Warren Buffett"], "theme": "life"}, None
        )
        assert ref == "1"
        check_conn = sqlite3.connect(factory_db)
        try:
            row = check_conn.execute("SELECT celebrity_pool FROM pages WHERE id=1").fetchone()
        finally:
            check_conn.close()
        assert row and "Warren Buffett" in row[0]
    finally:
        env_loader._SOURCES.clear(); env_loader._SOURCES.update(old_sources)
        if old_v28 is None: os.environ.pop("V28_ENV_FILE", None)
        else: os.environ["V28_ENV_FILE"] = old_v28
        if old_serper is None: os.environ.pop("SERPER_API_KEY", None)
        else: os.environ["SERPER_API_KEY"] = old_serper

def test_authoritative_checkpoint_monotonic(tmp: Path) -> None:
    from core import db
    db_file = tmp / "test_cp.sqlite3"
    old_db = db.DB_PATH
    db.DB_PATH = db_file
    try:
        db.init_db()
        with db.connect() as c:
            c.execute("INSERT INTO job_templates(id, slug, name, engine, created_at, updated_at) VALUES('tpl', 'tpl_slug', 'Tpl', 'custom', '2026-08-24T00:00:00', '2026-08-24T00:00:00')")
            c.execute("INSERT INTO job_instances(id, template_id, name, config_json, created_at, updated_at) VALUES('inst_test', 'tpl', 'Test', '{}', '2026-08-24T00:00:00', '2026-08-24T00:00:00')")
            c.execute("INSERT INTO runs(id, instance_id, template_id, engine, status, created_at, updated_at) VALUES('run_mon', 'inst_test', 'tpl', 'custom', 'running', '2026-08-24T00:00:00', '2026-08-24T00:00:00')")
        # 1. Normal save to DONE on attempt 1 with 100% progress
        db.save_scene_checkpoint("job_mon", 0, run_id="run_mon", scene_id=1, status="DONE", progress=100, attempt_id="1")
        cp = db.get_scene_checkpoint("job_mon", 0)
        assert cp["status"] == "DONE" and cp["progress"] == 100
        
        # 2. Late event RUNNING on attempt 1 should be IGNORED (monotonic protection)
        db.save_scene_checkpoint("job_mon", 0, run_id="run_mon", scene_id=1, status="RUNNING", progress=20, attempt_id="1")
        cp = db.get_scene_checkpoint("job_mon", 0)
        assert cp["status"] == "DONE" and cp["progress"] == 100

        # 3. Mark scene 2 as FAILED on attempt 1
        db.save_scene_checkpoint("job_mon", 1, run_id="run_mon", scene_id=2, status="FAILED", progress=80, attempt_id="1")
        cp2 = db.get_scene_checkpoint("job_mon", 1)
        assert cp2["status"] == "FAILED"

        # 4. Retry attempt 2 should transition to RUNNING and reset progress to 10% (NOT keep 80%!)
        db.save_scene_checkpoint("job_mon", 1, run_id="run_mon", scene_id=2, status="RUNNING", progress=10, attempt_id="2")
        cp2_retry = db.get_scene_checkpoint("job_mon", 1)
        assert cp2_retry["status"] == "RUNNING" and cp2_retry["progress"] == 10 and cp2_retry["attempt_id"] == "2"

        # 5. Stale event from attempt 1 (e.g. DONE from old worker) arrives late -> MUST BE REJECTED!
        db.save_scene_checkpoint("job_mon", 1, run_id="run_mon", scene_id=2, status="DONE", progress=100, attempt_id="1")
        cp2_stale = db.get_scene_checkpoint("job_mon", 1)
        assert cp2_stale["status"] == "RUNNING" and cp2_stale["progress"] == 10 and cp2_stale["attempt_id"] == "2"
    finally:
        db.DB_PATH = old_db


def test_facebook_upload_session_idempotency(tmp: Path) -> None:
    from core import db
    db_file = tmp / "test_fb.sqlite3"
    old_db = db.DB_PATH
    db.DB_PATH = db_file
    try:
        db.init_db()
        with db.connect() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(publish_jobs)").fetchall()}
        assert "upload_session_id" in cols
        assert "upload_offset" in cols
        assert "idempotency_key" in cols
    finally:
        db.DB_PATH = old_db


def test_console_guard() -> None:
    start_bat = (ROOT / "START.bat").read_text("utf-8")
    sup = (ROOT / "supervisor.py").read_text("utf-8")
    run_server = (ROOT / "run_server.py").read_text("utf-8")
    assert 'cmd.exe /k' in start_bat
    assert 'guarded_main' in sup and 'SUPERVISOR FATAL EXCEPTION' in sup
    assert 'guarded_main as supervisor_main' in run_server


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="v284-selftest-") as d:
        tmp = Path(d)
        test_manager(tmp)
        test_db_migration_old_outbox(tmp)
        asyncio.run(test_broker(tmp))
        test_static()
        test_env_loader()
        test_celebrity_prepare_resolved_scope(tmp)
        test_authoritative_checkpoint_monotonic(tmp)
        test_facebook_upload_session_idempotency(tmp)
        test_console_guard()
    sup = (ROOT / "supervisor.py").read_text("utf-8")
    assert "server HALTED but supervisor stays alive" in sup and "CONSOLE_LOG" in sup
    print("V2.8.6.0 SELF TEST OK")


if __name__ == "__main__":
    main()




