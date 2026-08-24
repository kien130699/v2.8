from __future__ import annotations

import asyncio
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import db
from core.job_manager import JobManager


class FakeBroker:
    def __init__(self) -> None:
        self.sources = {
            "beauty": SimpleNamespace(connected=True),
            "parenting": SimpleNamespace(connected=True),
        }

    def extension_ready(self) -> bool:
        return True


def make_clip(path: Path, label: str, seconds: float = 2.2) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    color = {
        "celebrity": "0x3657c9",
        "beauty": "0xb14d8a",
        "parenting": "0x36a166",
    }.get(label, "0x444444")
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=540x960:d={seconds}:r=30",
        "-vf", f"drawtext=text='{label}':x=40:y=80:fontsize=48:fontcolor=white",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not path.exists() or path.stat().st_size < 4096:
        raise AssertionError(f"bad video output: {path}")
    return str(path.resolve())


class FakeEngine:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def ensure_celebrity_profile(self, instance_id, name, config, engine_ref):
        return engine_ref or "fake_celebrity"

    async def run_celebrity(self, ref, config):
        return {"engine_run_id": "fake_celebrity_run", "engine_job_ids": ["fake_celebrity_job"]}

    async def wait_celebrity(self, job_id):
        video = make_clip(self.output_dir / "celebrity.mp4", "celebrity")
        return {"video_paths": [video], "title": "Celebrity smoke", "caption": "ok", "raw": {"job_id": job_id}}

    async def ensure_beauty_profile(self, instance_id, name, config, engine_ref):
        return engine_ref or "fake_beauty"

    async def run_beauty(self, ref, config):
        return {"engine_run_id": "fake_beauty_run", "engine_job_ids": ["fake_beauty_job"]}

    async def wait_beauty(self, job_ids):
        video = make_clip(self.output_dir / "beauty.mp4", "beauty")
        return {"video_paths": [video], "title": "Beauty smoke", "caption": "ok", "raw": {"job_ids": job_ids}}

    async def run_parenting(self, config, instance_id, name):
        return {"engine_run_id": "fake_parenting_run", "engine_job_ids": ["fake_parenting_job"]}

    async def wait_parenting(self, run_id):
        video = make_clip(self.output_dir / "parenting.mp4", "parenting")
        return {"video_paths": [video], "title": "Parenting smoke", "caption": "ok", "raw": {"run_id": run_id}}


def make_persona(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (768, 1376), (80, 120, 160)).save(path, quality=92)


async def run_one(manager: JobManager, instance_id: str) -> dict:
    queued = manager.run_instance(instance_id, trigger="video-output-smoke")
    claimed = manager._claim_next_queued_run(99)
    assert claimed and claimed["id"] == queued["run_id"], (instance_id, queued, claimed)
    await manager._execute_run(queued["run_id"])
    run = manager.get_run(queued["run_id"])
    assert run and run["status"] == "done_no_pages", run
    videos = (run.get("output") or {}).get("video_paths") or []
    assert videos, run
    for raw in videos:
        path = Path(raw)
        assert path.exists() and path.stat().st_size >= 4096, raw
    return {"instance_id": instance_id, "run_id": run["id"], "videos": videos}


async def main() -> None:
    original_db = db.DB_PATH
    with tempfile.TemporaryDirectory(prefix="v28-video-smoke-") as temp:
        tmp = Path(temp)
        db.DB_PATH = tmp / "v28.sqlite3"
        try:
            db.init_db()
            output_dir = ROOT / "data" / "video_output_smoke"
            shutil.rmtree(output_dir, ignore_errors=True)
            manager = JobManager(FakeEngine(output_dir), FakeBroker())
            manager.load_plugins()
            persona = tmp / "persona.jpg"
            make_persona(persona)
            manager.update_instance("2.1", {"config": {"persona_path": str(persona)}})
            results = [
                await run_one(manager, "1.1"),
                await run_one(manager, "2.1"),
                await run_one(manager, "3.1"),
            ]
        finally:
            db.DB_PATH = original_db
    print("VIDEO_OUTPUT_SMOKE OK")
    for item in results:
        print(f"{item['instance_id']} -> {item['videos'][0]}")


if __name__ == "__main__":
    asyncio.run(main())
