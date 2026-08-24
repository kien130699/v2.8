import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "flow_content"))

import asyncio
from app import render_factory_v2, get_flow_job, get_factory_meta, _factory_expected_scene_sets, _available_videos_for_job, _available_images_for_job

job_id = "flow_20260818_113327_b44bb4e7"
job = get_flow_job(job_id) or {}
meta = get_factory_meta(job)
exp_i, exp_v = _factory_expected_scene_sets(job)
avail_v = _available_videos_for_job(job_id)
avail_i = _available_images_for_job(job_id)

print(f"Meta mode: {meta.get('mode')}")
print(f"Expected images: {exp_i}, videos: {exp_v}")
print(f"Avail videos count: {len(avail_v)}")
print(f"Avail images count: {len(avail_i)}")

try:
    asyncio.run(render_factory_v2(job_id))
    print("RENDER SUCCESS!")
except Exception as e:
    traceback.print_exc()
