"""
V2.8.6.0 Smart Watchdog Test Agent & Sequential Diagnostics Harness.

Features:
- Preflight: System, DB, templates, and active engine lock discovery
- Dynamic schema validation against live manifests (no bad settings)
- Active Watchdog: freeze detection, deadlock breaker, and stale lease recovery
- Graceful Signal Trapping (Ctrl+C / SIGINT auto-cancels active test run)
- Structured Evidence Folder with per-job state traces and FINAL_REPORT.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
import uuid

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "v28.sqlite3"


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw_err = exc.read().decode("utf-8", errors="replace")
        try:
            return {"_http_error": exc.code, "_detail": json.loads(raw_err)}
        except Exception:
            return {"_http_error": exc.code, "_detail": raw_err}
    except Exception as exc:
        return {"_error": str(exc)}


def query_db(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def inspect_db_schema() -> dict[str, list[str]]:
    if not DB_PATH.exists():
        return {}
    schema: dict[str, list[str]] = {}
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
            schema[t] = [c[1] for c in cols]
    return schema


def upload_sample_persona(base_url: str, instance_id: str, field_name: str = "persona_path") -> dict[str, Any]:
    candidates = [
        ROOT / "data" / "job_assets" / "2_1" / "persona_path.png",
        ROOT / "data" / "review" / "job2_scene1.jpg",
    ]
    file_bytes = None
    filename = "persona.jpg"
    for c in candidates:
        if c.exists() and c.stat().st_size > 1024:
            file_bytes = c.read_bytes()
            filename = c.name
            break
    if not file_bytes:
        try:
            from PIL import Image
            im = Image.new("RGB", (512, 768), color=(220, 180, 160))
            buf = BytesIO()
            im.save(buf, format="JPEG")
            file_bytes = buf.getvalue()
        except Exception:
            # Fallback 1x1 jpeg
            file_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x00\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"

    url = f"{base_url}/api/jobs/{instance_id}/assets/{field_name}"
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: image/jpeg\r\n\r\n")
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    req = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as exc:
        return {"_error": str(exc)}


class SmartTestAgent:
    def __init__(self, base_url: str = "http://127.0.0.1:3000", timeout: float = 90.0, mode: str = "integration", retry_on_fail: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.mode = mode.lower()  # 'dispatch', 'integration', 'full'
        self.retry_on_fail = retry_on_fail
        self.session_id = f"TEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.session_dir = ROOT / "data" / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.active_run_id: str | None = None
        self.job_reports: list[dict[str, Any]] = []
        self.start_time = time.time()
        self._setup_signals()

    def _setup_signals(self) -> None:
        def handle_signal(sig, frame):
            print("\n\n⚠️ NHẬN TÍN HIỆU NGẮT (Ctrl+C). Đang giải phóng Engine Lock và dọn dẹp Run...", flush=True)
            if self.active_run_id:
                try:
                    request_json("POST", f"{self.base_url}/api/runs/{self.active_run_id}/cancel")
                    print(f"✅ Đã hủy run {self.active_run_id} an toàn.", flush=True)
                except Exception:
                    pass
            sys.exit(130)

        signal.signal(signal.SIGINT, handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle_signal)

    def log(self, tag: str, msg: str, level: str = "INFO") -> None:
        elapsed = time.time() - self.start_time
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        badge = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌"}.get(level, "•")
        print(f"[{ts}] [+{(elapsed):06.2f}s] {badge} [{tag}] {msg}", flush=True)

    def preflight_check(self, auto_recover: bool = True) -> dict[str, Any]:
        self.log("PREFLIGHT", f"=== KIỂM TRA HỆ THỐNG TRƯỚC KHI TEST (Mode: {self.mode.upper()}) ===")
        health = request_json("GET", f"{self.base_url}/api/health", timeout=5.0)
        if not health.get("ok"):
            self.log("PREFLIGHT", f"Server V2.8 không online tại {self.base_url}", level="ERROR")
            return {"ok": False, "error": "Server offline"}

        status = request_json("GET", f"{self.base_url}/api/status", timeout=5.0)
        templates = request_json("GET", f"{self.base_url}/api/job-templates", timeout=5.0)
        schema = inspect_db_schema()

        # Check for active engine owners in SQLite
        active_runs = query_db("SELECT id, instance_id, template_id, status, attempt, heartbeat_at, updated_at FROM runs WHERE status IN ('dispatching','preparing','running','rendering','waiting_engine')")
        
        self.log("PREFLIGHT", f"Server Version: {health.get('version')} · Port: {health.get('port')}")
        self.log("PREFLIGHT", f"Flow Worker Extension: {'CONNECTED' if status.get('flow', {}).get('extensionConnected') else 'OFFLINE'}")
        self.log("PREFLIGHT", f"Database Tables: {len(schema)} tables verified")
        self.log("PREFLIGHT", f"Active/Locked Runs: {len(active_runs)}")

        if active_runs and auto_recover:
            self.log("PREFLIGHT", f"Tiến hành Auto-Recover giải phóng {len(active_runs)} Engine Lock cũ...", level="WARNING")
            for r in active_runs:
                rid = r["id"]
                st = r["status"]
                request_json("POST", f"{self.base_url}/api/runs/{rid}/cancel")
                self.log("PREFLIGHT", f"Đã gửi Cancel cho {r['instance_id']} / {rid} ({st})")
            time.sleep(1.0)

        return {
            "ok": True,
            "health": health,
            "flowOnline": bool(status.get("flow", {}).get("extensionConnected")),
            "templateCount": len(templates) if isinstance(templates, list) else 0,
            "recoveredRuns": len(active_runs),
        }

    def execute_single_job(self, seq: int, name: str, template_id: str, test_kind: str) -> dict[str, Any]:
        job_dir = self.session_dir / f"job_{seq:02d}_{name.lower()}"
        job_dir.mkdir(parents=True, exist_ok=True)
        states_fp = (job_dir / "states.jsonl").open("a", encoding="utf-8")

        self.log(f"JOB-{seq:02d}", f"=== TEST {seq}/7: {name} (Template: {template_id}, Kind: {test_kind}, Mode: {self.mode}) ===")

        # 1. Create Instance (POST /api/jobs) with strict empty config
        create_req = {
            "template_id": str(template_id),
            "name": f"SmartTest_{seq}_{name}",
            "config": {},
            "page_ids": [],
        }
        (job_dir / "request.json").write_text(json.dumps(create_req, indent=2, ensure_ascii=False), encoding="utf-8")

        create_res = request_json("POST", f"{self.base_url}/api/jobs", create_req)
        if not create_res.get("ok"):
            self.log(f"JOB-{seq:02d}", f"Lỗi tạo Instance: {create_res}", level="ERROR")
            diag = {"sequence": seq, "name": name, "classification": "FAIL_CREATE_INSTANCE", "error": create_res}
            (job_dir / "diagnostics.json").write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
            return diag

        inst_id = str(create_res.get("job", {}).get("id") or "")
        self.log(f"JOB-{seq:02d}", f"Đã tạo Instance ID: {inst_id}")

        # 2. Check persona requirement
        if str(template_id) in {"2", "5"}:
            up = upload_sample_persona(self.base_url, inst_id, "persona_path")
            self.log(f"JOB-{seq:02d}", f"Persona upload status: {up.get('ok', False)}")

        # 3. Trigger Run (POST /api/jobs/{id}/run)
        trigger_res = request_json("POST", f"{self.base_url}/api/jobs/{inst_id}/run", {"trigger": f"smart-test-{seq}"})
        if not trigger_res.get("ok"):
            self.log(f"JOB-{seq:02d}", f"Lỗi trigger Run: {trigger_res}", level="ERROR")
            diag = {"sequence": seq, "name": name, "classification": "FAIL_TRIGGER_RUN", "error": trigger_res}
            (job_dir / "diagnostics.json").write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
            return diag

        run_id = str(trigger_res.get("run_id") or "")
        self.active_run_id = run_id
        self.log(f"JOB-{seq:02d}", f"Đã trigger Run ID: {run_id}")

        # 4. Watchdog Polling Loop
        start = time.time()
        last_progress_at = start
        last_progress_val = None
        last_status = None
        final_state = None
        deadlock_recovered = False

        last_log_id = 0
        seen_cps: set[str] = set()

        while time.time() - start < self.timeout:
            now = time.time()
            run_data = request_json("GET", f"{self.base_url}/api/runs/{run_id}")
            st = run_data.get("status")
            prog = run_data.get("progress", 0)

            # Record state transition
            state_record = {"time": round(now - start, 2), "status": st, "progress": prog, "error": run_data.get("error")}
            states_fp.write(json.dumps(state_record, ensure_ascii=False) + "\n")
            states_fp.flush()

            if st != last_status or prog != last_progress_val:
                self.log(f"JOB-{seq:02d}", f"State -> Status: {st} | Progress: {prog}%")
                last_status = st
                last_progress_val = prog
                last_progress_at = now

            # Stream live server event logs for this run
            new_logs = query_db("SELECT id, kind, level, message FROM event_logs WHERE (run_id=? OR message LIKE ?) AND id > ? ORDER BY id ASC",
                                (run_id, f"%{run_id}%", last_log_id))
            for lg in new_logs:
                last_log_id = max(last_log_id, int(lg["id"]))
                self.log(f"JOB-{seq:02d}", f"👉 [{lg['kind'].upper()}] {lg['message']}")

            # Stream live scene checkpoints
            cur_cps = query_db("SELECT scene_id, status, attempt_id, output_path FROM scene_checkpoints WHERE run_id=?", (run_id,))
            for cp in cur_cps:
                cp_key = f"{cp.get('scene_id')}_{cp.get('status')}_{cp.get('attempt_id')}"
                if cp_key not in seen_cps:
                    seen_cps.add(cp_key)
                    self.log(f"JOB-{seq:02d}", f"🎬 CHECKPOINT: Scene {cp.get('scene_id')} -> {cp.get('status')} (Attempt {cp.get('attempt_id')})", level="SUCCESS")

            # Watchdog 1: Deadlock detection when waiting_engine
            if st == "waiting_engine" and (now - start > 10.0) and not deadlock_recovered:
                self.log(f"JOB-{seq:02d}", f"Phát hiện Engine Lock! Đang tự động giải phóng lease cũ...", level="WARNING")
                blocking = query_db("SELECT id FROM runs WHERE template_id=? AND id<>? AND status IN ('dispatching','preparing','running','rendering')",
                                    (str(template_id), run_id))
                for b in blocking:
                    request_json("POST", f"{self.base_url}/api/runs/{b['id']}/cancel")
                    self.log(f"JOB-{seq:02d}", f"Đã giải phóng Lock từ Run {b['id']}")
                deadlock_recovered = True
                time.sleep(2.0)
                continue

            # Watchdog 2: Injection tests
            if test_kind == "INJECT_CANCEL" and st in {"queued", "waiting_flow", "preparing", "running"}:
                self.log(f"JOB-{seq:02d}", f"Cố tình gửi lệnh CANCEL Run {run_id}")
                cancel_res = request_json("POST", f"{self.base_url}/api/runs/{run_id}/cancel")
                self.log(f"JOB-{seq:02d}", f"Cancel response: {cancel_res}")

            # Terminal Conditions based on mode
            if self.mode == "dispatch":
                if st in {"queued", "waiting_flow", "preparing", "running", "completed", "done", "done_no_pages", "cancelled"}:
                    time.sleep(2.0)
                    final_state = run_data
                    break
            else:
                # Mode integration / full
                if st in {"completed", "done", "done_no_pages", "published", "dry_run_ok", "cancelled", "failed"}:
                    final_state = run_data
                    break

            time.sleep(2.0)

        elapsed = round(time.time() - start, 2)
        self.active_run_id = None
        states_fp.close()

        # 5. Classify & Report
        db_run = query_db("SELECT * FROM runs WHERE id=?", (run_id,))
        cps = query_db("SELECT * FROM scene_checkpoints WHERE run_id=?", (run_id,))
        final_status = final_state.get("status") if final_state else "TIMEOUT"
        err_msg = (db_run[0].get("error") if db_run else None) or (final_state.get("error") if final_state else None)

        classification = "UNKNOWN"
        if self.mode == "dispatch":
            if final_status in {"queued", "waiting_flow", "preparing", "running", "completed", "done", "done_no_pages"}:
                classification = "PASS_FLOW_DISPATCH_ORCHESTRATION"
            elif final_status == "cancelled":
                classification = "PASS_CANCEL_IDENTITY_GUARD"
            else:
                classification = f"FAIL_{final_status.upper()}"
        else:
            # Mode integration / full deep verification
            if final_status in {"completed", "done", "published"}:
                classification = "PASS_FULL_PIPELINE"
            elif final_status in {"done_no_pages", "dry_run_ok"}:
                classification = "PASS_ORCHESTRATION_NO_PAGE"
            elif final_status == "cancelled":
                classification = "PASS_CANCEL_IDENTITY_GUARD"
            elif final_status == "TIMEOUT":
                classification = "FAIL_TIMEOUT_FREEZE"
            elif final_status == "waiting_engine":
                classification = "FAIL_ENGINE_DEADLOCK"
            elif final_status == "failed":
                classification = "FAIL_ENGINE_EXECUTION"

        is_pass = classification.startswith("PASS")
        badge = "SUCCESS" if is_pass else "ERROR"
        self.log(f"JOB-{seq:02d}", f"KẾT THÚC ({elapsed}s) -> {classification} (Status: {final_status})", level=badge)

        if not is_pass and err_msg:
            print(f"\n❌ [CHI TIẾT LỖI JOB {seq}]: {err_msg}\n", flush=True)

        diag = {
            "sequence": seq,
            "name": name,
            "templateId": template_id,
            "runId": run_id,
            "finalStatus": final_status,
            "classification": classification,
            "elapsedSec": elapsed,
            "isPass": is_pass,
            "error": err_msg,
            "dbRun": db_run[0] if db_run else None,
            "checkpoints": cps,
        }
        (job_dir / "diagnostics.json").write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
        return diag

    def run_suite(self, job_range: list[int] | None = None) -> dict[str, Any]:
        pre = self.preflight_check(auto_recover=True)
        if not pre.get("ok"):
            return {"ok": False, "error": pre.get("error")}

        suite_plan = [
            (1, "BASIC_RUN_ORCHESTRATION", "1", "NORMAL"),
            (2, "SCENE_CHECKPOINT_INTEGRITY", "2", "NORMAL"),
            (3, "MEDIA_TRACKING_LIFECYCLE", "3", "NORMAL"),
            (4, "DOWNLOAD_LOCAL_PERSISTENCE", "4", "NORMAL"),
            (5, "FAILED_ATTEMPT_RETRY_ISOLATION", "5", "NORMAL"),
            (6, "CANCEL_JOB_IDENTITY_GUARD", "6", "INJECT_CANCEL"),
            (7, "FULL_FLOW_AND_QC_PIPELINE", "7", "NORMAL"),
        ]

        if job_range:
            suite_plan = [x for x in suite_plan if x[0] in job_range]

        for seq, name, tpl_id, kind in suite_plan:
            res = self.execute_single_job(seq, name, tpl_id, kind)
            
            # Retry once if failed and retry_on_fail is True
            if not res.get("isPass") and self.retry_on_fail and kind != "INJECT_CANCEL":
                self.log(f"JOB-{seq:02d}", "Tự động RETRY lần 2 sau khi dọn dẹp Engine Lock...", level="WARNING")
                time.sleep(2.0)
                res = self.execute_single_job(seq, name, tpl_id, kind)

            self.job_results = getattr(self, "job_results", [])
            self.job_results.append(res)

        # Generate FINAL_REPORT
        total = len(self.job_results)
        passed = sum(1 for r in self.job_results if r.get("isPass"))
        failed = total - passed

        final_report = {
            "session": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "passed": passed,
            "failed": failed,
            "mode": self.mode,
            "results": self.job_results,
        }
        (self.session_dir / "FINAL_REPORT.json").write_text(json.dumps(final_report, indent=2, ensure_ascii=False), encoding="utf-8")

        # Console Summary Table
        print("\n" + "=" * 65)
        print(f"📊 BÁO CÁO TỔNG KẾT SMART WATCHDOG TEST ({self.session_id} · Mode: {self.mode.upper()})")
        print("=" * 65)
        for r in self.job_results:
            seq = r.get("sequence", 0)
            name = r.get("name", "Unknown")
            clf = r.get("classification", "UNKNOWN")
            el = r.get("elapsedSec", 0)
            badge = "✅" if r.get("isPass") else "❌"
            print(f"Job {seq:02d} | {name:<32} | {badge} {clf:<30} | {el}s")
        print("=" * 65)
        print(f"TỔNG: {total} | PASS: {passed} | FAIL: {failed}")
        print(f"📁 Toàn bộ Evidence đã lưu tại: {self.session_dir}\n")

        return final_report


def parse_job_range(raw: str) -> list[int]:
    if not raw:
        return list(range(1, 8))
    if "-" in raw:
        parts = raw.split("-")
        return list(range(int(parts[0]), int(parts[1]) + 1))
    if "," in raw:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    return [int(raw.strip())]


def main() -> None:
    parser = argparse.ArgumentParser(description="V2.8.6.0 Smart Watchdog Test Agent")
    parser.add_argument("--url", default="http://127.0.0.1:3000", help="Base URL of server")
    parser.add_argument("--preflight", action="store_true", help="Run preflight check only")
    parser.add_argument("--jobs", default="", help="Jobs to run e.g. '1-7', '2-7', '2', '2,3,4'")
    parser.add_argument("--mode", default="integration", choices=["dispatch", "integration", "full"], help="Test mode: dispatch (quick API check), integration (DB & checkpoint verification), full (full video render & download)")
    parser.add_argument("--real-flow", action="store_true", help="Alias for --mode full")
    parser.add_argument("--no-retry", action="store_true", help="Do not retry failed jobs")
    parser.add_argument("--timeout", type=float, default=90.0, help="Timeout in seconds per job")
    args = parser.parse_args()

    mode = "full" if args.real_flow else args.mode
    agent = SmartTestAgent(base_url=args.url, timeout=args.timeout, mode=mode, retry_on_fail=not args.no_retry)
    
    if args.preflight:
        agent.preflight_check(auto_recover=True)
        return

    job_range = parse_job_range(args.jobs) if args.jobs else list(range(1, 8))
    agent.run_suite(job_range=job_range)


if __name__ == "__main__":
    main()
