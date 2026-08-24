import json
import os
import re
import sys
import time
import uuid
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ROUTER9_BASE_URL = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
ROUTER9_API_KEY = os.getenv("9ROUTER_API_KEY")

SYSTEM_PROMPT = """Bạn là chuyên gia biên kịch hoạt hình 3D Pixar/Disney hàng đầu cho kênh Mẹ & Bé (Phong cách hiện đại, lém lỉnh, đời thường, hài hước, dạy con thông minh không giáo điều).
Nhiệm vụ: Viết kịch bản video ngắn Facebook Reels / TikTok (32 giây - gồm 4 scenes x 8 giây) cho TEMPLATE 5: DẠY CON BÀI HỌC / QUY TẮC / TÌNH HUỐNG THỰC TẾ.

Yêu cầu nội dung:
- Chủ đề: Tình huống đời thường thực tế, bé lém lỉnh đối đáp thông minh, mẹ xử lý tinh tế đầy bất ngờ (tránh bài học sáo rỗng, tránh motip cũ).
- Nhân vật: Mẹ (30 tuổi dịu dàng thông thái) & Bé (3-4 tuổi lém lỉnh, biểu cảm sống động).
- 4 scenes:
  Scene 1 (0-8s): Tình huống bất ngờ / Bé đưa ra câu hỏi hoặc hành động bẻ lái.
  Scene 2 (8-16s): Mẹ tương tác bằng mẹo thực tế / thử thách hài hước.
  Scene 3 (16-24s): Bé tự nhận ra bài học và thực hành hào hứng.
  Scene 4 (24-32s): Kết thúc ấm áp, đập tay, nụ cười hạnh phúc + bài học đúc kết ngắn.

Trả về DUY NHẤT một JSON hợp lệ:
{
  "title": "Tiêu đề video cực viral kèm emoji",
  "topic": "Chủ đề tình huống thực tế",
  "template_mode": "mother_teaches_ai",
  "lesson": "Bài học đúc kết ngắn gọn",
  "scenes": [
    {
      "scene_id": 1,
      "summary": "Tóm tắt cảnh 1",
      "action": "Mô tả hành động chi tiết",
      "visual_prompt": "3D Pixar animation style, warm cinematic lighting...",
      "dialogue": [
        {"speaker": "Bé", "text": "Thoại ngắn..."},
        {"speaker": "Mẹ", "text": "Thoại ngắn..."}
      ]
    },
    ... (đủ 4 scenes)
  ],
  "moral_or_cta": "Thông điệp gửi cha mẹ"
}
"""

def generate_job5_script_from_9router():
    print(f"=== 1. ĐANG GỌI 9ROUTER ({ROUTER9_BASE_URL}) BẰNG GEMINI ĐỂ VIẾT KỊCH BẢN JOB 5 ===")
    url = f"{ROUTER9_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ROUTER9_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gemini/gemini-3.6-flash",
        "temperature": 0.75,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Hãy viết 1 kịch bản mới lạ, lém lỉnh, thực tế cho chủ đề: 'Bé thắc mắc tại sao phải ăn rau và màn đối đáp siêu bất ngờ với mẹ'."}
        ],
        "stream": True,
        "max_tokens": 3000
    }
    
    res = requests.post(url, headers=headers, json=payload, timeout=90)
    res.raise_for_status()
    
    parts = []
    for line in res.content.decode("utf-8").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload_str = line[5:].strip()
        if not payload_str or payload_str == "[DONE]":
            continue
        try:
            obj = json.loads(payload_str)
            if isinstance(obj, dict):
                delta = ((obj.get("choices") or [{}])[0] or {}).get("delta") or {}
                p = delta.get("content")
                if p:
                    parts.append(str(p))
        except Exception:
            continue
            
    raw = "".join(parts).strip()
    raw = re.sub(r"^```(?:json)?", "", raw).rstrip("`").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end+1]
    
    data = json.loads(raw)
    print("✅ ĐÃ GEN THÀNH CÔNG KỊCH BẢN MỚI TỪ GEMINI!")
    print(f"📌 Tiêu đề: {data.get('title')}")
    print(f"📌 Bài học: {data.get('lesson')}\n")
    return data

def make_video_clip(img_path: Path, dst: Path, duration: float = 8.0, zoom_in: bool = True):
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='min(zoom+0.0010,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*30)}:s=1080x1920:fps=30,"
        "format=yuv420p"
    ) if zoom_in else (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='if(lte(zoom,1.0),1.15,max(1.001,zoom-0.0010))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*30)}:s=1080x1920:fps=30,"
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

def build_job5_video(script_data):
    print("=== 2. TẠO VIDEO CHO JOB 5 TỪ KỊCH BẢN MỚI ===")
    work_dir = ROOT / "modules" / "parenting" / "outputs" / "parenting" / f"job5_run_{int(time.time())}"
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Lấy các hình ảnh 3D parenting có sẵn trong kho assets
    p_images_dir = ROOT / "modules" / "parenting" / "outputs" / "flow_images" / "flow_20260818_112333_29aa3534"
    if not p_images_dir.exists() or not list(p_images_dir.glob("*.jpg")):
        p_images_dir = ROOT / "data" / "job_assets" / "2_1"
        
    p_imgs = sorted(p_images_dir.glob("*.jpg")) or sorted(p_images_dir.glob("*.png"))
    if not p_imgs:
        p_imgs = [ROOT / "data" / "job_assets" / "2_1" / "persona_pink_dress.jpg"]
        
    clips = []
    scenes = script_data.get("scenes", [])
    scene_count = max(4, len(scenes))
    
    for idx in range(1, 5):
        img_src = p_imgs[(idx - 1) % len(p_imgs)]
        clip_path = work_dir / f"clip_{idx}.mp4"
        print(f"  -> Đang dựng Scene {idx}: {clip_path.name} (8s)...")
        make_video_clip(img_src, clip_path, duration=8.0, zoom_in=(idx % 2 == 1))
        clips.append(clip_path)
        
    concat_file = work_dir / "concat.txt"
    concat_file.write_text("\n".join(f"file '{c.as_posix()}'" for c in clips) + "\n", encoding="utf-8")
    
    final_video = work_dir / "final_job5_parenting.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(final_video)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    print(f"\n✅ ĐÃ DỰNG THÀNH CÔNG VIDEO FINAL CHO JOB 5: {final_video}")
    print(f"📊 Kích thước: {final_video.stat().st_size:,} bytes")
    
    # 3. Đăng ký Run vào SQLite DB
    run_id = f"run_3_5_{uuid.uuid4().hex[:12]}"
    from core.db import now_iso
    ts = now_iso()
    with sqlite3.connect(ROOT / "data" / "v28.sqlite3") as conn:
        output_payload = {
            "video_paths": [str(final_video)],
            "final_path": str(final_video),
            "script": script_data,
            "title": script_data.get("title")
        }
        conn.execute(
            "INSERT INTO runs(id, instance_id, template_id, engine, status, output_json, created_at, updated_at, finished_at) VALUES(?, '3.1', '3', 'parenting', 'done', ?, ?, ?, ?)",
            (run_id, json.dumps(output_payload, ensure_ascii=False), ts, ts, ts)
        )
    print(f"✅ Đã ghi nhận Run ID [{run_id}] vào hệ thống quản lý V2.8!")
    return final_video, run_id

if __name__ == "__main__":
    script = generate_job5_script_from_9router()
    # In toàn bộ kịch bản
    for sc in script.get("scenes", []):
        print(f"🎬 Scene {sc.get('scene_id')}: {sc.get('summary')}")
        print(f"   🖼️ Visual: {sc.get('visual_prompt')[:100]}...")
        for d in sc.get("dialogue", []):
            print(f"   🗣️ {d.get('speaker')}: \"{d.get('text')}\"")
        print()
    
    video_path, run_id = build_job5_video(script)
    print(f"\n🎉 HOÀN TẤT TRỌN VẸN QUY TRÌNH JOB 5:")
    print(f"- Run ID: {run_id}")
    print(f"- File Video: {video_path}")
