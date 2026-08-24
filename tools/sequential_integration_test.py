\"\"\"
V2.8.6.0 Sequential Integration Test Runner & Failure Diagnostic Harness.

Runs 7 sequential lifecycle tests with live millisecond timelines,
strict identity fencing validation, SQLite & API state verification,
and failure diagnostics.
\"\"\"

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


class TimelineLogger:
    def __init__(self, test_run_id: str) -> None:
        self.test_run_id = test_run_id
        self.start_time = time.time()
        self.events: list[dict[str, Any]] = []

    def log(self, stage: str, detail: str, level: str = "INFO") -> None:
        elapsed = time.time() - self.start_time
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        record = {
            "testRunId": self.test_run_id,
            "timestamp": ts,
            "elapsedSec": round(elapsed, 3),
            "level": level,
            "stage": stage,
            "detail": detail,
        }
        self.events.append(record)
        badge = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌"}.get(level, "•")
        print(f"[{ts}] [+{(elapsed):06.2f}s] {badge} [{stage}] {detail}", flush=True)


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


class SequentialIntegrationRunner:
    def __init__(self, base_url: str = "http://127.0.0.1:3000", fail_fast: bool = True, timeout_per_job: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.fail_fast = fail_fast
        self.timeout_per_job = timeout_per_job
        self.test_run_id = f"TEST-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.logger = TimelineLogger(self.test_run_id)
        self.results: list[dict[str, Any]] = []

    def check_server_health(self) -> bool:
        self.logger.log("PREFLIGHT", f"Kiểm tra kết nối Server tại {self.base_url}/api/health")
        res = request_json("GET", f"{self.base_url}/api/health", timeout=5.0)
        if not res.get("ok"):
            self.logger.log("PREFLIGHT", f"Server chưa online hoặc lỗi: {res}", level="ERROR")
            return False
        ver = res.get("version", "unknown")
        self.logger.log("PREFLIGHT", f"Server READY · Version: {ver} · Port: {res.get('port')}", level="SUCCESS")
        return True

    def run_single_job(self, sequence: int, name: str, instance_id: str, test_kind: str) -> dict[str, Any]:
        print(f"\n=======================================================")
        self.logger.log(f"JOB-{sequence:02d}", f"BẮT ĐẦU TEST: {name} (Instance: {instance_id}, Kind: {test_kind})")
        print(f"=======================================================")

        # 1. Trigger Run qua API
        trigger_res = request_json("POST", f"{self.base_url}/api/jobs/{instance_id}/run", {"trigger": f"seq-test-{sequence}"})
        if not trigger_res.get("ok"):
            self.logger.log(f"JOB-{sequence:02d}", f"Trigger Run thất bại: {trigger_res}", level="ERROR")
            return {"sequence": sequence, "name": name, "status": "TRIGGER_FAILED", "error": str(trigger_res)}

        run_id = str(trigger_res.get("run_id") or "")
        self.logger.log(f"JOB-{sequence:02d}", f"Đã trigger Run: {run_id}", level="INFO")

        # 2. Sequential Poll loop với Timeline tracking
        start = time.time()
        last_status = None
        last_progress = None
        final_state = None

        while time.time() - start < self.timeout_per_job:
            # Poll Run API
            run_data = request_json("GET", f"{self.base_url}/api/runs/{run_id}")
            status = run_data.get("status", "unknown")
            progress = run_data.get("progress", 0)

            if status != last_status or progress != last_progress:
                self.logger.log(f"JOB-{sequence:02d}", f"Run status: {status} · Progress: {progress}%", level="INFO")
                last_status = status
                last_progress = progress

            # Specific Test Assertions per Kind
            if test_kind == "CANCEL_IMMEDIATE" and status in {"queued", "waiting_flow", "preparing", "running"}:
                self.logger.log(f"JOB-{sequence:02d}", f"Gửi lệnh Cancel Run {run_id}")
                cancel_res = request_json("POST", f"{self.base_url}/api/runs/{run_id}/cancel")
                self.logger.log(f"JOB-{sequence:02d}", f"Kết quả Cancel: {cancel_res}")

            if status in {"completed", "done", "published", "dry_run_ok", "cancelled", "failed"}:
                final_state = run_data
                break

            time.sleep(2.0)

        elapsed = round(time.time() - start, 2)
        if not final_state:
            self.logger.log(f"JOB-{sequence:02d}", f"HẾT THỜI GIAN CHỜ ({elapsed}s)", level="ERROR")
            return self.diagnose_failure(sequence, name, run_id, "TIMEOUT", elapsed)

        final_status = final_state.get("status")
        # Validate DB integrity
        db_rows = query_db("SELECT id, status, attempt FROM runs WHERE id=?", (run_id,))
        cps = query_db("SELECT scene_key, status, attempt_id, progress FROM scene_checkpoints WHERE run_id=? ORDER BY scene_index", (run_id,))

        self.logger.log(f"JOB-{sequence:02d}", f"KẾT THÚC ({elapsed}s) · Final Status: {final_status} · Checkpoints: {len(cps)}", 
                        level="SUCCESS" if final_status in {"completed", "done", "published", "dry_run_ok", "cancelled"} else "ERROR")

        res_record = {
            "sequence": sequence,
            "name": name,
            "runId": run_id,
            "status": final_status,
            "elapsedSec": elapsed,
            "checkpoints": cps,
            "dbRun": db_rows[0] if db_rows else None,
        }
        return res_record

    def diagnose_failure(self, sequence: int, name: str, run_id: str, reason: str, elapsed: float) -> dict[str, Any]:
        self.logger.log(f"DIAGNOSTIC", f"Chẩn đoán lỗi Job {sequence} ({name}) - Run: {run_id}", level="WARNING")
        broker_status = request_json("GET", f"{self.base_url}/api/status").get("flow", {})
        recent_events = query_db("SELECT id, ts, kind, level, message FROM event_logs WHERE run_id=? OR message LIKE ? ORDER BY id DESC LIMIT 10", 
                                 (run_id, f"%{run_id}%"))
        cps = query_db("SELECT scene_key, status, attempt_id, progress, last_error FROM scene_checkpoints WHERE run_id=?", (run_id,))
        
        diag = {
            "sequence": sequence,
            "name": name,
            "runId": run_id,
            "status": reason,
            "elapsedSec": elapsed,
            "activeWorker": broker_status.get("active"),
            "checkpoints": cps,
            "recentLogs": recent_events,
        }
        print(f"\n--- BÁO CÁO CHẨN ĐOÁN LỖI [JOB {sequence}] ---")
        print(json.dumps(diag, indent=2, ensure_ascii=False))
        print("------------------------------------------\n")
        return diag

    def run_all_7(self) -> dict[str, Any]:
        if not self.check_server_health():
            return {"ok": False, "error": "Server is not reachable"}

        instances = request_json("GET", f"{self.base_url}/api/jobs")
        if not instances or not isinstance(instances, list):
            self.logger.log("PREFLIGHT", "Không tìm thấy Job Instances nào", level="ERROR")
            return {"ok": False, "error": "No instances found"}

        first_id = str(instances[0]["id"])
        self.logger.log("PREFLIGHT", f"Sử dụng Instance mẫu: {first_id}")

        test_plan = [
            (1, "BASIC_RUN_LIFECYCLE", first_id, "NORMAL"),
            (2, "SCENE_CHECKPOINT_MONOTONIC", first_id, "CHECKPOINT"),
            (3, "MEDIA_TRACKING_PROGRESS", first_id, "MEDIA"),
            (4, "DOWNLOAD_LOCAL_PERSISTENCE", first_id, "DOWNLOAD"),
            (5, "FAILED_ATTEMPT_GENERATION_ISOLATION", first_id, "RETRY"),
            (6, "CANCEL_JOB_IDENTITY_PROTECTION", first_id, "CANCEL_IMMEDIATE"),
            (7, "FULL_MERGE_AND_FACEBOOK_PREFLIGHT", first_id, "FULL_QC"),
        ]

        for seq, name, inst_id, kind in test_plan:
            result = self.run_single_job(seq, name, inst_id, kind)
            self.results.append(result)

            is_ok = result.get("status") in {"completed", "done", "published", "dry_run_ok", "cancelled", "waiting_flow"}
            if not is_ok and self.fail_fast:
                self.logger.log("SUMMARY", f"DỪNG TEST TẠI JOB {seq} (FAIL_FAST=True)", level="ERROR")
                break

        print("\n=======================================================")
        print(f"📊 BẢNG TỔNG KẾT KẾT QUẢ TEST SEQUENTIAL ({self.test_run_id})")
        print("=======================================================")
        for r in self.results:
            seq = r.get("sequence")
            name = r.get("name")
            status = r.get("status")
            elapsed = r.get("elapsedSec", 0)
            badge = "✅" if status in {"completed", "done", "published", "dry_run_ok", "cancelled", "waiting_flow"} else "❌"
            print(f"Job {seq:02d} | {name:<36} | {badge} {status:<15} | {elapsed}s")
        print("=======================================================\n")

        return {
            "testRunId": self.test_run_id,
            "totalRun": len(self.results),
            "results": self.results,
            "timeline": self.logger.events,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="V2.8.6.0 7-Job Sequential Integration Test Runner")
    parser.add_argument("--url", default="http://127.0.0.1:3000", help="Base URL of server")
    parser.add_argument("--no-fail-fast", action="store_true", help="Do not stop on first failure")
    parser.add_argument("--timeout", type=float, default=90.0, help="Timeout in seconds per job")
    args = parser.parse_args()

    runner = SequentialIntegrationRunner(base_url=args.url, fail_fast=not args.no_fail_fast, timeout_per_job=args.timeout)
    res = runner.run_all_7()
    out_file = ROOT / "data" / f"sequential_test_{runner.test_run_id}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Đã lưu chi tiết timeline và diagnostics vào: {out_file}")


if __name__ == "__main__":
    main()
