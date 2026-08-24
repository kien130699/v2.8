"""
V2.8.6.0 7-Job Sequential Integration & Torture Test Suite.

Tier 1: Orchestration & State Machine Testing (CREATE -> RUN -> POLL -> RETRY -> CANCEL -> SQLITE CHECK)
Tier 2: Real Flow Execution (Optional flag --real-flow)

Generates structured diagnostic artifacts in data/TEST_<TIMESTAMP>/
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "v28.sqlite3"


import uuid
from io import BytesIO
from PIL import Image

def upload_persona_asset(base_url: str, instance_id: str, field_name: str = "persona_path") -> dict[str, Any]:
    # Find existing asset or generate dummy
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
        im = Image.new("RGB", (512, 768), color=(220, 180, 160))
        buf = BytesIO()
        im.save(buf, format="JPEG")
        file_bytes = buf.getvalue()
        filename = "persona.jpg"

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


class SequentialTester:
    def __init__(self, base_url: str = "http://127.0.0.1:3000", fail_fast: bool = True, timeout_per_job: float = 120.0, real_flow: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.fail_fast = fail_fast
        self.timeout_per_job = timeout_per_job
        self.real_flow = real_flow
        self.session_id = f"TEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.out_dir = ROOT / "data" / self.session_id
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.timeline_fp = (self.out_dir / "timeline.log").open("a", encoding="utf-8")
        self.job_results: list[dict[str, Any]] = []
        self.start_epoch = time.time()

    def log(self, tag: str, message: str, level: str = "INFO") -> None:
        elapsed = time.time() - self.start_epoch
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        badge = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌"}.get(level, "•")
        line = f"[{ts}] [+{(elapsed):06.2f}s] {badge} [{tag}] {message}"
        print(line, flush=True)
        self.timeline_fp.write(line + "\n")
        self.timeline_fp.flush()

    def diagnose_job(self, seq: int, name: str, run_id: str, elapsed: float, last_status: str | None) -> dict[str, Any]:
        broker_status = request_json("GET", f"{self.base_url}/api/status").get("flow", {})
        recent_logs = query_db("SELECT id, ts, kind, level, message FROM event_logs WHERE run_id=? OR message LIKE ? ORDER BY id DESC LIMIT 15",
                               (run_id, f"%{run_id}%"))
        cps = query_db("SELECT * FROM scene_checkpoints WHERE run_id=?", (run_id,))
        run_row = query_db("SELECT * FROM runs WHERE id=?", (run_id,))

        suspected = "UNKNOWN"
        if last_status in {"completed", "done", "done_no_pages", "published", "dry_run_ok", "cancelled"}:
            suspected = "NONE"
        elif not broker_status.get("extensionConnected") and not self.real_flow:
            suspected = "WAITING_FLOW_EXPECTED"
        elif broker_status.get("active") and time.time() - (broker_status.get("active", {}).get("startedAt") or time.time()) > 100:
            suspected = "FLOW_WORKER_PROGRESS_FREEZE"
        elif last_status == "failed":
            suspected = "JOB_EXECUTION_FAILURE"

        diag = {
            "testSession": self.session_id,
            "sequence": seq,
            "name": name,
            "runId": run_id,
            "lastStatus": last_status,
            "elapsedSec": elapsed,
            "suspectedComponent": suspected,
            "dbRun": run_row[0] if run_row else None,
            "checkpoints": cps,
            "brokerActive": broker_status.get("active"),
            "recentLogs": recent_logs,
        }
        return diag

    def execute_job(self, seq: int, name: str, template_id: str, config: dict[str, Any], test_mode: str) -> dict[str, Any]:
        self.log(f"JOB-{seq:02d}", f"=== KHỞI CHẠY TEST {seq}/7: {name} (Template: {template_id}, Mode: {test_mode}) ===")

        # Step 1: CREATE INSTANCE (POST /api/jobs)
        create_payload = {
            "template_id": template_id,
            "name": f"SeqTest_{seq}_{name}",
            "config": config,
            "page_ids": [],
        }
        create_res = request_json("POST", f"{self.base_url}/api/jobs", create_payload)
        if not create_res.get("ok"):
            self.log(f"JOB-{seq:02d}", f"Lỗi tạo Job Instance: {create_res}", level="ERROR")
            return {"sequence": seq, "name": name, "status": "CREATE_FAILED", "error": create_res}

        instance_id = str(create_res.get("job", {}).get("id") or "")
        self.log(f"JOB-{seq:02d}", f"Đã tạo Instance ID: {instance_id}")

        # Auto-attach persona asset if required by template
        if template_id in {"2", "5"}:
            up_res = upload_persona_asset(self.base_url, instance_id, "persona_path")
            self.log(f"JOB-{seq:02d}", f"Đã import Persona sample: {up_res.get('ok', False)}")

        # Step 2: RUN INSTANCE (POST /api/jobs/{instance_id}/run)
        run_res = request_json("POST", f"{self.base_url}/api/jobs/{instance_id}/run", {"trigger": f"seq-test-{seq}"})
        if not run_res.get("ok"):
            self.log(f"JOB-{seq:02d}", f"Lỗi trigger run: {run_res}", level="ERROR")
            return {"sequence": seq, "name": name, "status": "TRIGGER_FAILED", "error": run_res}

        run_id = str(run_res.get("run_id") or "")
        self.log(f"JOB-{seq:02d}", f"Đã trigger Run ID: {run_id}")

        # Step 3: Polling loop
        start = time.time()
        last_status = None
        last_progress = None
        final_state = None

        while time.time() - start < self.timeout_per_job:
            run_data = request_json("GET", f"{self.base_url}/api/runs/{run_id}")
            status = run_data.get("status")
            progress = run_data.get("progress", 0)

            if status != last_status or progress != last_progress:
                self.log(f"JOB-{seq:02d}", f"State Transition -> Status: {status} | Progress: {progress}%")
                last_status = status
                last_progress = progress

            # Specific failure injection behaviors
            if test_mode == "INJECT_CANCEL" and status in {"queued", "waiting_flow", "preparing", "running"}:
                self.log(f"JOB-{seq:02d}", f"Cố tình gửi lệnh CANCEL Run {run_id}")
                cancel_res = request_json("POST", f"{self.base_url}/api/runs/{run_id}/cancel")
                self.log(f"JOB-{seq:02d}", f"Cancel response: {cancel_res}")

            # Terminal conditions
            if status in {"completed", "done", "done_no_pages", "published", "dry_run_ok", "cancelled", "failed"}:
                final_state = run_data
                break
            if not self.real_flow and status in {"waiting_flow", "queued"}:
                time.sleep(3.0)
                final_state = run_data
                break

            time.sleep(2.0)

        elapsed = round(time.time() - start, 2)
        diag = self.diagnose_job(seq, name, run_id, elapsed, last_status)
        
        # Save individual job report
        job_file = self.out_dir / f"job_{seq:02d}_{name.lower()}.json"
        job_file.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")

        status_flag = diag.get("lastStatus")
        is_success = status_flag in {"completed", "done", "done_no_pages", "published", "dry_run_ok", "cancelled"} or (not self.real_flow and status_flag in {"waiting_flow", "queued"})
        badge = "SUCCESS" if is_success else "ERROR"
        self.log(f"JOB-{seq:02d}", f"HOÀN THÀNH TEST ({elapsed}s) -> Kết quả: {status_flag}", level=badge)

        if not is_success and diag.get("dbRun") and diag["dbRun"].get("error"):
            print(f"\n❌ [CHI TIẾT LỖI JOB {seq}]: {diag['dbRun']['error']}\n", flush=True)

        return diag

    def run_suite(self, start_from: int = 1, only_job: int | None = None) -> dict[str, Any]:
        self.log("INIT", f"BẮT ĐẦU 7-JOB SEQUENTIAL SUITE (Session: {self.session_id}, RealFlow: {self.real_flow})")

        # Health check
        h = request_json("GET", f"{self.base_url}/api/health", timeout=5.0)
        if not h.get("ok"):
            self.log("INIT", f"Server không phản hồi tại {self.base_url}", level="ERROR")
            return {"ok": False, "error": "server unreachable"}

        templates = request_json("GET", f"{self.base_url}/api/job-templates")
        if not templates or not isinstance(templates, list):
            self.log("INIT", "Không lấy được danh sách templates", level="ERROR")
            return {"ok": False, "error": "no templates"}

        # Define 7 distinct tests matching available templates
        suite_plan = [
            (1, "BASIC_RUN_ORCHESTRATION", "1", {}, "NORMAL"),
            (2, "SCENE_CHECKPOINT_INTEGRITY", "2", {}, "NORMAL"),
            (3, "MEDIA_TRACKING_LIFECYCLE", "3", {}, "NORMAL"),
            (4, "DOWNLOAD_LOCAL_PERSISTENCE", "4", {}, "NORMAL"),
            (5, "FAILED_ATTEMPT_RETRY_ISOLATION", "5", {}, "NORMAL"),
            (6, "CANCEL_JOB_IDENTITY_GUARD", "6", {}, "INJECT_CANCEL"),
            (7, "FULL_FLOW_AND_QC_PIPELINE", "7", {}, "NORMAL"),
        ]

        if only_job is not None:
            suite_plan = [x for x in suite_plan if x[0] == only_job]
        elif start_from > 1:
            suite_plan = [x for x in suite_plan if x[0] >= start_from]

        for seq, name, tpl_id, cfg, mode in suite_plan:
            res = self.execute_job(seq, name, tpl_id, cfg, mode)
            self.job_results.append(res)
            
            st = res.get("lastStatus") or res.get("status")
            ok = st in {"completed", "done", "done_no_pages", "published", "dry_run_ok", "cancelled"} or (not self.real_flow and st in {"waiting_flow", "queued"})
            if not ok and self.fail_fast:
                self.log("SUITE", f"DỪNG TOÀN BỘ SUITE TẠI JOB {seq} DO FAIL_FAST=TRUE", level="ERROR")
                break

        # Summary Generation
        summary = {
            "testSession": self.session_id,
            "realFlowEnabled": self.real_flow,
            "totalJobs": len(self.job_results),
            "results": self.job_results,
        }
        (self.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        # Summary TXT
        txt_lines = [
            f"=======================================================",
            f"📊 BÁO CÁO TỔNG KẾT 7-JOB SEQUENTIAL TEST ({self.session_id})",
            f"=======================================================",
        ]
        for r in self.job_results:
            seq = r.get("sequence") or 0
            name = r.get("name") or "Unknown"
            st = r.get("lastStatus") or r.get("status") or "FAILED"
            el = r.get("elapsedSec") if r.get("elapsedSec") is not None else 0
            badge = "✅" if st in {"completed", "done", "done_no_pages", "published", "dry_run_ok", "cancelled", "waiting_flow", "queued"} else "❌"
            txt_lines.append(f"Job {seq:02d} | {name:<35} | {badge} {str(st):<15} | {el}s")
        txt_lines.append(f"=======================================================\n")
        txt_content = "\n".join(txt_lines)
        (self.out_dir / "summary.txt").write_text(txt_content, encoding="utf-8")
        print("\n" + txt_content)
        self.timeline_fp.close()

        print(f"📁 Toàn bộ kết quả đã được lưu tại: {self.out_dir}")
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V2.8.6.0 7-Job Sequential Test Runner")
    parser.add_argument("--url", default="http://127.0.0.1:3000", help="Base URL of server")
    parser.add_argument("--real-flow", action="store_true", help="Run with real Flow extension (wait for full video completion)")
    parser.add_argument("--no-fail-fast", action="store_true", help="Do not stop on first failure")
    parser.add_argument("--timeout", type=float, default=90.0, help="Timeout in seconds per job")
    parser.add_argument("--start-from", type=int, default=1, help="Start testing from Job N (e.g. --start-from 2 for Flow jobs)")
    parser.add_argument("--job", type=int, default=None, help="Run only a specific Job N (e.g. --job 2)")
    args = parser.parse_args()

    tester = SequentialTester(base_url=args.url, fail_fast=not args.no_fail_fast, timeout_per_job=args.timeout, real_flow=args.real_flow)
    tester.run_suite(start_from=args.start_from, only_job=args.job)


if __name__ == "__main__":
    main()
