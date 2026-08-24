import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv
import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ROUTER9_BASE_URL = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
ROUTER9_API_KEY = os.getenv("9ROUTER_API_KEY")
V28_URL = "http://127.0.0.1:3000"

FALLBACK_MODELS = [
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3-flash-preview",
    "gemini/gemini-3.7-flash",
    "gemini/gemini-3.6-flash"
]

SYSTEM_PROMPT = """Bạn là Chuyên gia Kỹ thuật Prompts AI Video hàng đầu cho kênh Thời Trang Nữ (Job 6: Phối Màu Thời Trang · Veo 3 Native Audio & Lip-Sync).
Nhiệm vụ: Tạo kịch bản và toàn bộ Prompts Video Veo 3 (có nhúng native Vietnamese dialogue) cho video 24s (3 clips x 8s).

Quy tắc bắt buộc:
1. Đa dạng hóa các cặp màu và trang phục:
   - Clip 1 (8s): Tone Nâu Mocha (Trắng vs Xanh Bơ) -> 4s sau nhảy cảnh sang Tone Xám Tiêu (Đen vs Đỏ Rượu).
   - Clip 2 (8s): Tone Tím Lavender (Trắng vs Vàng Bơ) -> 4s sau nhảy cảnh sang Tone Xanh Navy (Trắng vs Be Sáng).
   - Clip 3 (8s): Tone Cam Đất (Đen vs Xanh Rêu) -> 4s sau nhảy cảnh sang Đoạn Kết CTA Kêu Gọi Follow.
2. Nhúng trực tiếp thoại tiếng Việt vào prompt Veo 3:
   The girl on the left ... speaks in Vietnamese: '...'
   The girl on the right ... replies in Vietnamese: '...'

Trả về DUY NHẤT một JSON hợp lệ:
{
  "title": "Mẹo Phối Màu Thời Trang Chuẩn Gu 2026 ✨",
  "theme": "Mix & Match Phong Cách Tiểu Thư & Sang Chảnh",
  "total_duration": "24s",
  "clips": [
    {
      "clip_number": 1,
      "duration": "8s",
      "tones": "Tone Nâu Mocha sang Tone Xám Tiêu",
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio. Two stylish Vietnamese female fashion models standing side by side in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k. Both wearing tailored buttoned vests in rich mocha brown. Left girl in flowing white silk maxi skirt. Right girl in mint green silk maxi skirt holding a chic mini handbag. --ar 9:16",
      "veo3_video_prompt": "Vertical 9:16 fashion lookbook video, static full-body shot. Two stylish Vietnamese women standing in front of a white minimalist closet. In first 4 seconds, both wear tailored mocha brown vests. The girl on the left in white silk skirt points down and speaks in Vietnamese: 'Nâu với trắng nhìn nhã nhặn công sở...'. The girl on the right in mint green silk skirt places a hand on hip, turns slightly and replies in Vietnamese: '...nhưng nâu mà mix cùng xanh bơ là visual tiểu thư liền nha!'. At 4-second mark, outfits smoothly transition via quick jump-cut: both now wear tailored charcoal gray vests. Left girl in black silk skirt says in Vietnamese: 'Xám với đen trông an toàn, chỉn chu...'. Right girl in rich wine red silk skirt takes half a step forward and says in Vietnamese: '...còn xám đi cùng đỏ rượu nhìn quyền lực và đắt tiền gấp đôi!'. Realistic lip sync, natural body gestures, studio lighting."
    },
    {
      "clip_number": 2,
      "duration": "8s",
      "tones": "Tone Tím Lavender sang Tone Xanh Navy",
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio. Two beautiful Vietnamese young women standing side by side in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k. Both wearing tailored pastel lavender vests. Left girl in white silk maxi skirt. Right girl in soft butter yellow silk maxi skirt. --ar 9:16",
      "veo3_video_prompt": "Vertical 9:16 fashion video, static medium-full shot. Two Vietnamese women in front of a modern white closet. In first 4 seconds, both wear tailored lavender vests. Left girl in white silk skirt does cute gesture and speaks in Vietnamese: 'Tím với trắng nhìn dịu dàng nàng thơ...'. Right girl in butter yellow silk skirt touches hair and replies in Vietnamese: '...nhưng tím phối với vàng bơ mới đúng chất fashionista phá cách!'. At 4 seconds, outfits instantly cut to classic navy blue vests. Left girl in white silk skirt smiles and says in Vietnamese: 'Navy mix trắng thì thanh lịch...'. Right girl in light beige silk skirt poses holding bag and says in Vietnamese: '...còn navy đi với be sữa là chuẩn gu sang chảnh quốc tế luôn.'. Seamless lip sync and cloth motion."
    },
    {
      "clip_number": 3,
      "duration": "8s",
      "tones": "Tone Cam Đất sang Đoạn Kết CTA",
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio. Two chic Vietnamese female models standing side by side in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k. Both wearing tailored burnt terracotta orange vests. Left girl in black silk maxi skirt. Right girl in luxury olive green silk maxi skirt. --ar 9:16",
      "veo3_video_prompt": "Vertical 9:16 fashion video. In first 4 seconds, two Vietnamese women wear tailored terracotta orange vests. Left girl in black silk skirt strikes cool pose and says in Vietnamese: 'Cam đất với đen nhìn cá tính, hiện đại...'. Right girl in olive green silk skirt steps forward with radiant smile and says in Vietnamese: '...nhưng cam đất mix với xanh rêu thì thần thái sang chảnh đỉnh chóp!'. At 4-second mark, outfits smoothly switch back to vibrant red vests and matching silk skirts. Both girls look straight into camera with radiant smiles. Left girl says in Vietnamese: 'Lưu lại ngay mẹo này nha...'. Right girl finishes in Vietnamese: '...và follow tụi mình để mở khóa thêm 1000 công thức phối đồ xịn nhé!' as both girls warmly wave goodbye to camera. Perfect lip sync, crisp speech."
    }
  ]
}
"""

def generate_script_9router():
    print(f"=== 1. ĐANG GỌI 9ROUTER ({ROUTER9_BASE_URL}) BẰNG GEMINI ĐỂ GEN KỊCH BẢN MỚI JOB 6 ===")
    url = f"{ROUTER9_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ROUTER9_API_KEY}",
        "Content-Type": "application/json"
    }
    
    last_err = None
    for model in FALLBACK_MODELS:
        try:
            print(f"  -> Thử model: {model}...")
            payload = {
                "model": model,
                "temperature": 0.5,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "Tạo kịch bản chuẩn JSON và toàn bộ prompts Veo 3 Native Audio cho chủ đề phối màu thời trang mới lạ 2026."}
                ],
                "stream": True,
                "max_tokens": 2500
            }
            res = requests.post(url, headers=headers, json=payload, timeout=60)
            if res.status_code == 429:
                print(f"     [429 Rate Limit] Model {model} bận, tự động chuyển model tiếp theo...")
                time.sleep(1)
                continue
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
            print(f"✅ ĐÃ GEN THÀNH CÔNG TỪ [{model}]!")
            print(f"📌 Tiêu đề: {data.get('title')}")
            print(f"📌 Chủ đề: {data.get('theme')}\n")
            return data
        except Exception as e:
            last_err = e
            print(f"     [Lỗi {model}]: {e}")
            continue

    cached_file = ROOT / "data" / "job6_demo_script.json"
    if cached_file.exists():
        print("⚠️ 9Router tạm thời bận, tự động nạp kịch bản đã sinh trước đó từ cache!")
        return json.loads(cached_file.read_text(encoding="utf-8"))
    raise RuntimeError(f"Tất cả model 9Router đều lỗi: {last_err}")

def run_job6_on_server(script_data):
    print("=== 2. KIỂM TRA TRẠNG THÁI SERVER V2.8 & FLOW EXTENSION ===")
    try:
        r = requests.get(f"{V28_URL}/api/status", timeout=3)
        r.raise_for_status()
        st = r.json()
        flow = st.get("flow", {})
        ext_online = bool(flow.get("extensionConnected"))
        print(f"✅ Server V2.8 đang chạy tại: {V28_URL}")
        print(f"🔌 Trạng thái Flow Extension: {'ĐÃ KẾT NỐI (ONLINE)' if ext_online else 'CHƯA KẾT NỐI (OFFLINE)'}")
    except Exception as e:
        print(f"❌ Không kết nối được Server V2.8: {e}")
        print("   Hãy đảm bảo đã chạy START.bat để bật server ở cổng 3000.")
        return

    # Lưu kịch bản vào file pending
    queue_file = ROOT / "data" / "flow_job6_pending_tasks.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(json.dumps(script_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print("\n=== 3. KÍCH HOẠT RUN JOB 6.1 TRÊN SERVER V2.8 ===")
    try:
        res = requests.post(f"{V28_URL}/api/jobs/6.1/run", json={"trigger": "manual_tool"}, timeout=10)
        res.raise_for_status()
        run_info = res.json()
        run_id = run_info.get("run_id")
        print(f"✅ ĐÃ KÍCH HOẠT THÀNH CÔNG JOB 6.1 TRÊN SERVER!")
        print(f"📌 Run ID: {run_id}")
        print(f"📌 Status: {run_info.get('status')}")
        print("\n🚀 Lệnh đã vào hàng đợi của Server V2.8. Flow Extension trên trình duyệt sẽ nhận task và bắt đầu chạy Veo 3 trên Google Flow!")
        
        # Poll status trong 15s để xem Flow Extension nhận job
        print("\n⏳ Đang theo dõi trạng thái hàng đợi Flow...")
        for _ in range(5):
            time.sleep(2)
            st_res = requests.get(f"{V28_URL}/api/status", timeout=3)
            if st_res.status_code == 200:
                fl = st_res.json().get("flow", {})
                active = fl.get("active")
                pending_count = len(fl.get("pending", []))
                print(f"   -> Flow Active Task: {active.get('jobId') if active else 'None'} | Pending: {pending_count}")
                if active:
                    print(f"🔥 Flow Extension ĐANG CHẠY RENDER TASK: {active.get('jobId')}!")
                    break
    except Exception as e:
        print(f"❌ Lỗi khi trigger Job 6.1: {e}")

if __name__ == "__main__":
    script = generate_script_9router()
    run_job6_on_server(script)
