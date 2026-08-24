import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
flow_job_id = "flow_20260818_112333_29aa3534"
images_dir = ROOT / "modules" / "parenting" / "outputs" / "flow_images" / flow_job_id
outdir = ROOT / "modules" / "parenting" / "outputs" / "parenting" / flow_job_id
work = outdir / "work"
work.mkdir(parents=True, exist_ok=True)

img_files = sorted(images_dir.glob("*.jpg"))
print(f"Found {len(img_files)} generated scene images:")
for f in img_files:
    print(f"  {f.name} ({f.stat().st_size} bytes)")

normalized = []
per_scene = 7.5  # 30s total for 4 scenes

for idx, img in enumerate(img_files, 1):
    dst = work / f"segment_{idx:03d}.mp4"
    # Create animated zoom/pan video for each scene
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='min(zoom+0.0015,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(per_scene*30)}:s=1080x1920:fps=30,"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(img),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{per_scene:.3f}", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest",
        "-movflags", "+faststart", str(dst)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    normalized.append(dst)
    print(f"Rendered segment {idx}: {dst.name}")

concat = work / "concat.txt"
concat.write_text("\n".join("file '" + p.as_posix().replace("'", "'\\''") + "'" for p in normalized) + "\n", encoding="utf-8")

joined = work / "joined.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

final = outdir / "final_parenting.mp4"
shutil.copy2(joined, final)
print(f"SUCCESS! Final Unbox video rendered at: {final} ({final.stat().st_size} bytes)")

# Update SQLite databases
c1 = sqlite3.connect("modules/parenting/data/factory.sqlite3")
c1.execute("INSERT OR REPLACE INTO assets(id, job_id, scene_id, kind, local_path, title, created_at) VALUES('asset_final_3_11', ?, 0, 'final_video', ?, 'Unbox Xe Khung Long Final', datetime('now'))", (flow_job_id, str(final.resolve())))
c1.execute("UPDATE flow_jobs SET status='done', error=NULL WHERE id=?", (flow_job_id,))
c1.execute("UPDATE parenting_story_runs SET status='done', final_path=?, error=NULL, updated_at=datetime('now') WHERE flow_job_id=?", (str(final.resolve()), flow_job_id))
c1.commit()

c2 = sqlite3.connect("data/v28.sqlite3")
r = c2.execute("SELECT output_json FROM runs WHERE id='run_3_11_db4fa9e79e93'").fetchone()
import json
out = json.loads(r[0]) if r and r[0] else {}
out['video_paths'] = [str(final.resolve())]
out['final_path'] = str(final.resolve())
c2.execute("UPDATE runs SET status='done', output_json=?, finished_at=datetime('now') WHERE id='run_3_11_db4fa9e79e93'", (json.dumps(out),))
c2.commit()
print("Updated run_3_11_db4fa9e79e93 record in v28.sqlite3!")
