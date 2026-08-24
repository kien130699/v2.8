from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:3000"


def request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def try_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 5) -> Any | None:
    try:
        return request(method, path, payload, timeout)
    except Exception:
        return None


def start_server_if_needed() -> subprocess.Popen[str] | None:
    if try_request("GET", "/api/status", timeout=3):
        return None
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)
    env = os.environ.copy()
    env.setdefault("V28_PORT", "3000")
    env.setdefault("V28_EDGE_DEBUG_PORT", "9224")
    env["V28_SUPERVISED"] = "1"
    log = ROOT / "data" / "mini_attach_server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fp = log.open("a", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        [str(python), str(ROOT / "run_server.py")],
        cwd=str(ROOT),
        env=env,
        stdout=fp,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early code={proc.returncode}; log={log}")
        if try_request("GET", "/api/status", timeout=3):
            print(f"SERVER_STARTED pid={proc.pid} log={log}")
            return proc
        time.sleep(2)
    raise RuntimeError(f"server start timeout; pid={proc.pid}; log={log}")


def wait_ready(timeout_sec: int) -> None:
    deadline = time.time() + timeout_sec
    last = None
    while time.time() < deadline:
        status = try_request("GET", "/api/status", timeout=5)
        if status:
            flow = status.get("flow") or {}
            last = flow
            ext = bool(flow.get("extensionConnected")) and bool(flow.get("extensionCompatible"))
            print(f"READY extension={ext} version={(flow.get('extension') or {}).get('version')}")
            if ext:
                return
        time.sleep(3)
    raise RuntimeError("extension not ready: " + json.dumps(last, ensure_ascii=False))


def default_persona() -> Path:
    candidates = [
        Path(r"D:\YT\Code\V2.5\Flow_Content_Factory_V2_15_AUTO\server\outputs\personas\minh_anh_auto\persona_master_2048.jpg"),
        ROOT / "modules" / "flow_content" / "outputs" / "personas" / "gym_a" / "persona_master_2048.jpg",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("persona test image not found")


def create_mini_job(persona: Path) -> str:
    scene = {
        "sceneId": 1,
        "imagePrompt": (
            "Photorealistic adult woman, age 21+, same exact identity as reference. "
            "Vietnam cafe street, natural smartphone photo, full body, vertical 9:16, realistic anatomy, no text, no watermark."
        ),
        "videoPrompt": "",
        "inputImages": [{"path": str(persona), "name": "mini_persona_front", "role": "persona_front"}],
        "metadata": {
            "makeVideo": False,
            "mixedMotion": False,
            "sceneVideoPolicy": "PER_SCENE_V2",
            "sceneMediaMode": "IMAGE_ONLY",
            "miniAttachTest": True,
        },
    }
    flow = {
        "imageModel": "Nano Banana 2",
        "videoModel": "NONE",
        "imageConcurrency": 1,
        "videoConcurrency": 0,
        "submitPolicy": "IMAGE_ONLY",
        "autoDownloadVideo": False,
        "maxSubmitsPerMinute": 2,
        "submitGapMs": 1200,
        "aspectRatio": "9:16",
        "imageOutputs": "x1",
        "videoDuration": "8s",
        "videoOutputs": "x1",
        "imageTimeoutSec": 300,
        "videoTimeoutSec": 300,
    }
    res = request("POST", "/engine/beauty/api/flow/jobs", {"kind": "factory_v2_mix", "scenes": [scene], "flow": flow}, timeout=20)
    job_id = str(res.get("job_id") or "")
    if not job_id:
        raise RuntimeError("no job_id: " + json.dumps(res, ensure_ascii=False))
    print(f"MINI_JOB {job_id}")
    return job_id


def wait_job(job_id: str, timeout_sec: int) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        job = request("GET", f"/engine/beauty/api/flow/jobs/{job_id}", timeout=10)
        last = job
        result = job.get("result") or {}
        results = result.get("results") or []
        first = results[0] if results else {}
        print(
            "POLL",
            job_id,
            job.get("status"),
            "image=", first.get("imageState"),
            "video=", first.get("videoState"),
            "err=", str(first.get("error") or job.get("error") or "")[:220],
        )
        if job.get("status") in {"done", "partial_failed", "failed", "interrupted"}:
            return job
        time.sleep(5)
    raise RuntimeError("mini timeout: " + json.dumps(last, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", default="")
    parser.add_argument("--ready-timeout", type=int, default=120)
    parser.add_argument("--run-timeout", type=int, default=900)
    parser.add_argument("--keep-server", action="store_true")
    args = parser.parse_args()

    proc = start_server_if_needed()
    try:
        wait_ready(args.ready_timeout)
        persona = Path(args.persona) if args.persona else default_persona()
        print(f"PERSONA {persona}")
        job_id = create_mini_job(persona)
        job = wait_job(job_id, args.run_timeout)
        result = job.get("result") or {}
        rows = result.get("results") or []
        image_ok = bool(rows) and all(str(row.get("imageState") or "") == "SUCCESS" for row in rows)
        no_scene_error = not any(row.get("error") for row in rows)
        print("FINAL", json.dumps({"id": job.get("id"), "status": job.get("status"), "error": job.get("error"), "image_ok": image_ok, "result": result}, ensure_ascii=False, indent=2)[:6000])
        if image_ok and no_scene_error:
            print("MINI_ATTACH_IMAGE_OK")
            return 0
        return 2
    finally:
        if proc is not None and not args.keep_server:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
