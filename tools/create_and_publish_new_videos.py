import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]

def make_video_clip(img_path: Path, dst: Path, duration: float = 5.0, zoom_in: bool = True):
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='min(zoom+0.0012,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*30)}:s=1080x1920:fps=30,"
        "format=yuv420p"
    ) if zoom_in else (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='if(lte(zoom,1.0),1.15,max(1.001,zoom-0.0012))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*30)}:s=1080x1920:fps=30,"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(img_path),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{duration:.3f}", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest",
        "-movflags", "+faststart", str(dst)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

print("=== 1. TAO VIDEO MOI CHO PAGE MINH ANH (JOB 2) ===")
work_beauty = ROOT / "modules" / "flow_content" / "outputs" / "factory_v2" / "new_minhanh_run"
work_beauty.mkdir(parents=True, exist_ok=True)

img1 = ROOT / "data" / "job_assets" / "2_1" / "persona_pink_dress.jpg"
img2 = ROOT / "data" / "job_assets" / "2_1_sexy" / "concept_2_backless_penthouse.jpg"
img3 = ROOT / "data" / "job_assets" / "2_1" / "persona_red_silk.jpg"

clip1 = work_beauty / "clip1.mp4"
clip2 = work_beauty / "clip2.mp4"
clip3 = work_beauty / "clip3.mp4"

make_video_clip(img1, clip1, duration=5.5, zoom_in=True)
make_video_clip(img2, clip2, duration=5.5, zoom_in=False)
make_video_clip(img3, clip3, duration=5.5, zoom_in=True)

concat_b = work_beauty / "concat.txt"
concat_b.write_text(f"file '{clip1.as_posix()}'\nfile '{clip2.as_posix()}'\nfile '{clip3.as_posix()}'\n", encoding="utf-8")

final_beauty = work_beauty / "final_minhanh_new.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_b), "-c", "copy", str(final_beauty)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print(f"Video Minh Anh OK: {final_beauty} ({final_beauty.stat().st_size} bytes)")


print("\n=== 2. TAO VIDEO MOI CHO PAGE TRE EM THONG MINH (JOB 3) ===")
work_parenting = ROOT / "modules" / "parenting" / "outputs" / "parenting" / "new_treem_run"
work_parenting.mkdir(parents=True, exist_ok=True)

p_images_dir = ROOT / "modules" / "parenting" / "outputs" / "flow_images" / "flow_20260818_112333_29aa3534"
p_imgs = sorted(p_images_dir.glob("*.jpg"))
p_clips = []

for idx, p_img in enumerate(p_imgs, 1):
    p_clip = work_parenting / f"clip_{idx}.mp4"
    make_video_clip(p_img, p_clip, duration=6.5, zoom_in=(idx % 2 == 1))
    p_clips.append(p_clip)

concat_p = work_parenting / "concat.txt"
concat_p.write_text("\n".join(f"file '{c.as_posix()}'" for c in p_clips) + "\n", encoding="utf-8")

final_parenting = work_parenting / "final_treem_new.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_p), "-c", "copy", str(final_parenting)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print(f"Video Tre Em Thong Minh OK: {final_parenting} ({final_parenting.stat().st_size} bytes)")


print("\n=== 3. DANG VIDEO LEN FACEBOOK REELS ===")
import sys
sys.path.insert(0, str(ROOT))
from core import facebook, db

# 1. Page Minh Anh
run_id_1 = f"run_2_1_{uuid.uuid4().hex[:12]}"
ts = db.now_iso()
with sqlite3.connect(ROOT / "data" / "v28.sqlite3") as conn:
    conn.execute(
        "INSERT INTO runs(id, instance_id, template_id, engine, status, output_json, created_at, updated_at, finished_at) VALUES(?, '2.1', '2', 'beauty', 'done', ?, ?, ?, ?)",
        (run_id_1, json.dumps({"video_paths": [str(final_beauty)], "final_path": str(final_beauty)}), ts, ts, ts)
    )

pub_id_1 = facebook.enqueue_publish(
    run_id=run_id_1,
    page_id="111789830996371",
    video_path=str(final_beauty),
    title="Phong cách quyến rũ cùng Minh Anh ✨",
    description="Tự tin khoe trọn từng đường cong quyến rũ với đầm bodycon cực sang chảnh ✨ Bạn thích outfit nào nhất trong video này? 💕 #beauty #lifestyle #glamour #fashion #reels",
    dry_run=False
)
res1 = facebook.publish_one(pub_id_1)
print(f"Minh Anh Publish Result: {res1}")

# 2. Page Tre Em Thong Minh
run_id_2 = f"run_3_11_{uuid.uuid4().hex[:12]}"
with sqlite3.connect(ROOT / "data" / "v28.sqlite3") as conn:
    conn.execute(
        "INSERT INTO runs(id, instance_id, template_id, engine, status, output_json, created_at, updated_at, finished_at) VALUES(?, '3.11', '3', 'parenting', 'done', ?, ?, ?, ?)",
        (run_id_2, json.dumps({"video_paths": [str(final_parenting)], "final_path": str(final_parenting)}), ts, ts, ts)
    )

pub_id_2 = facebook.enqueue_publish(
    run_id=run_id_2,
    page_id="372902152584058",
    video_path=str(final_parenting),
    title="Đồ chơi xe khủng long đường ray cỡ lớn cho bé 🦖",
    description="Món đồ chơi siêu cuốn giúp bé thỏa sức khám phá, rèn luyện tư duy không gian và tương tác cực tốt cùng ba mẹ! 🦖🚗 Sắm ngay cho bé chơi mê say nhé ba mẹ ơi! #dochoithongminh #mevabe #unboxdochoi #dochoitreem #reels",
    dry_run=False
)
res2 = facebook.publish_one(pub_id_2)
print(f"Tre Em Thong Minh Publish Result: {res2}")
