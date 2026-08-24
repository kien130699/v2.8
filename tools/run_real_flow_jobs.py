from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:3000"
ACTIVE = {"queued", "waiting_flow", "waiting_engine", "dispatching", "preparing", "running", "rendering", "publish_queued", "publishing"}
OK = {"done_no_pages", "published", "dry_run_ok", "done", "rendered", "publish_queued"}
FAIL = {"failed", "interrupted", "partial_failed", "publish_failed"}


def request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {method} {path}: {raw}") from exc


def status() -> dict[str, Any]:
    return request("GET", "/api/status", timeout=5)


def wait_ready(wait_sec: int) -> None:
    deadline = time.time() + wait_sec
    last = None
    while time.time() < deadline:
        try:
            flow = status()["flow"]
            last = flow
            ext = bool(flow.get("extensionConnected")) and bool(flow.get("extensionCompatible"))
            beauty = bool((flow.get("sources") or {}).get("beauty", {}).get("connected"))
            parenting = bool((flow.get("sources") or {}).get("parenting", {}).get("connected"))
            print(f"READY_CHECK extension={ext} beauty={beauty} parenting={parenting}")
            if ext and beauty and parenting:
                return
        except Exception as exc:
            print(f"READY_CHECK error={exc}")
        time.sleep(3)
    raise RuntimeError("FLOW_WORKER/bridge chưa ready: " + json.dumps(last, ensure_ascii=False))


def ensure_beauty_persona() -> None:
    job = request("GET", "/api/jobs/2.1")
    cfg = job.get("config") or {}
    raw = str(cfg.get("persona_path") or "").strip()
    if raw and Path(raw).is_file():
        print(f"JOB2 persona OK: {raw}")
        return
    candidates = [
        ROOT / "modules" / "flow_content" / "outputs" / "personas" / "gym_a" / "persona_master_2048.jpg",
        Path(r"D:\YT\Code\V2.5\Flow_Content_Factory_V2_15_AUTO\server\outputs\personas\minh_anh_auto\persona_master_2048.jpg"),
    ]
    for path in candidates:
        if path.is_file():
            request("PATCH", "/api/jobs/2.1", {"config": {"persona_path": str(path.resolve())}})
            print(f"JOB2 persona set: {path}")
            return
    raise RuntimeError("Job2 thiếu persona thật. Import Persona Front trước.")


def run_job(instance_id: str) -> str:
    res = request("POST", f"/api/jobs/{instance_id}/run", {"trigger": "real-flow-runner"})
    run_id = str(res.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"Không có run_id: {res}")
    print(f"RUN {instance_id}: {run_id}")
    return run_id


def wait_run(run_id: str, wait_sec: int) -> dict[str, Any]:
    deadline = time.time() + wait_sec
    last = None
    while time.time() < deadline:
        run = request("GET", f"/api/runs/{run_id}", timeout=10)
        last = run
        status_value = str(run.get("status") or "")
        output = run.get("output") or {}
        videos = [str(x) for x in output.get("video_paths") or []]
        print(f"POLL {run_id}: {status_value} videos={len(videos)} error={str(run.get('error') or '')[:180]}")
        if videos:
            missing = [x for x in videos if not Path(x).is_file() or Path(x).stat().st_size < 1024]
            if not missing:
                return run
        if status_value in FAIL:
            raise RuntimeError(json.dumps(run, ensure_ascii=False, indent=2))
        time.sleep(5)
    raise RuntimeError("Run timeout: " + json.dumps(last, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", default="2.1,3.1")
    parser.add_argument("--ready-timeout", type=int, default=300)
    parser.add_argument("--run-timeout", type=int, default=7200)
    args = parser.parse_args()

    wait_ready(args.ready_timeout)
    jobs = [x.strip() for x in args.jobs.split(",") if x.strip()]
    if "2.1" in jobs:
        ensure_beauty_persona()
    results = []
    for job in jobs:
        run_id = run_job(job)
        results.append(wait_run(run_id, args.run_timeout))
    print("REAL_FLOW_JOBS_OK")
    for run in results:
        for video in (run.get("output") or {}).get("video_paths") or []:
            path = Path(str(video))
            print(f"{run['instance_id']} -> {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
