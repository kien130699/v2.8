import json
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv
import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ROUTER9_BASE_URL = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
ROUTER9_API_KEY = os.getenv("9ROUTER_API_KEY")

SYSTEM_PROMPT = """Bạn là Chuyên gia Kỹ thuật Prompts AI Video hàng đầu cho kênh Thời Trang Nữ (Job 6: Phối Màu Thời Trang · Veo 3 Native Audio).
Nhiệm vụ: Viết kịch bản và xuất toàn bộ prompts (Start Frame + Veo 3 Native Audio) cho video 24s (3 clips x 8s).

Quy tắc bắt buộc:
1. Clip 1 (8s): Tone Đỏ (Trắng vs Be) nhảy cảnh ở 4s sang Tone Đen (Trắng vs Nâu).
2. Clip 2 (8s): Tone Hồng (Trắng vs Xám) nhảy cảnh ở 4s sang Tone Xanh Dương (Trắng vs Be).
3. Clip 3 (8s): Tone Xanh Lá (Đen vs Kem) nhảy cảnh ở 4s sang Tone Đỏ (CTA).
4. Nhúng thẳng thoại tiếng Việt vào prompt Veo 3:
   The girl on the left ... speaks in Vietnamese: '...'
   The girl on the right ... replies in Vietnamese: '...'

Trả về DUY NHẤT một JSON hợp lệ:
{
  "title": "Mẹo Phối Màu Áo Ghi Lê Cổ V & Chân Váy Lụa ✨",
  "theme": "Áo Ghi Lê Cổ V & Chân Váy Lụa",
  "total_duration": "24s",
  "clips": [
    {
      "clip_number": 1,
      "duration": "8s",
      "tones": "Tone Đỏ sang Tone Đen",
      "dialogue": {
        "left_0_4s": "Nhớ kỹ nha, đỏ đi với trắng thì nhìn ổn...",
        "right_0_4s": "...nhưng đỏ mà phối với be là auto sang chảnh nha!",
        "left_4_8s": "Đen với trắng thì trông sạch sẽ...",
        "right_4_8s": "...còn đen mà mix với nâu nhìn vừa xịn vừa đắt tiền liền!"
      },
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio. Two stylish Vietnamese female fashion models standing side by side in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k. Both wearing tailored buttoned V-neck vests in bold red. The girl on the left wears a long flowing white silk maxi skirt. The girl on the right wears a long flowing beige silk maxi skirt holding a chic mini handbag. --ar 9:16",
      "veo3_video_prompt": "Vertical 9:16 fashion lookbook video, static full-body shot. Two stylish Vietnamese women standing in front of a white closet. In first 4 seconds, both wear tailored red V-neck vests. The girl on the left in white silk skirt points down and speaks in Vietnamese: 'Nhớ kỹ nha, đỏ đi với trắng thì nhìn ổn...'. The girl on the right in beige silk skirt places a hand on hip, turns slightly and replies in Vietnamese: '...nhưng đỏ mà phối với be là auto sang chảnh nha!'. At 4-second mark, outfits smoothly transition via quick jump-cut: both now wear tailored black V-neck vests. Left girl in white silk skirt adjusts bag strap and says in Vietnamese: 'Đen với trắng thì trông sạch sẽ...'. Right girl in rich brown silk skirt takes half a step forward and says in Vietnamese: '...còn đen mà mix với nâu nhìn vừa xịn vừa đắt tiền liền!'. Realistic lip sync, natural body gestures, studio lighting."
    },
    {
      "clip_number": 2,
      "duration": "8s",
      "tones": "Tone Hồng sang Tone Xanh Dương",
      "dialogue": {
        "left_0_4s": "Hồng với trắng nhìn cưng xỉu...",
        "right_0_4s": "...nhưng hồng đi cùng xám nhìn mới chuẩn gu sành điệu nè!",
        "left_4_8s": "Xanh dương mix trắng thì siêu tươi tắn...",
        "right_4_8s": "...còn xanh dương đi với be là đúng chất thanh lịch luôn."
      },
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio. Two beautiful Vietnamese young women standing side by side in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k. Both wearing tailored pastel baby pink V-neck buttoned vests. The girl on the left wears a white silk maxi skirt. The girl on the right wears a smokey gray silk maxi skirt. --ar 9:16",
      "veo3_video_prompt": "Vertical 9:16 fashion video, static medium-full shot. Two Vietnamese women in front of a modern white closet. In first 4 seconds, both wear tailored pastel pink V-neck vests. Left girl in white silk skirt does cute gesture and speaks in Vietnamese: 'Hồng với trắng nhìn cưng xỉu...'. Right girl in smokey gray silk skirt touches hair and replies in Vietnamese: '...nhưng hồng đi cùng xám nhìn mới chuẩn gu sành điệu nè!'. At 4 seconds, outfits instantly cut to classic blue V-neck vests. Left girl in white silk skirt smiles and says in Vietnamese: 'Xanh dương mix trắng thì siêu tươi tắn...'. Right girl in light beige silk skirt poses holding bag and says in Vietnamese: '...còn xanh dương đi với be là đúng chất thanh lịch luôn.'. Seamless lip sync and cloth motion."
    },
    {
      "clip_number": 3,
      "duration": "8s",
      "tones": "Tone Xanh Lá sang Đoạn Kết CTA",
      "dialogue": {
        "left_0_4s": "Xanh lá với đen nhìn chất ngầu, cá tính...",
        "right_0_4s": "...nhưng xanh lá mà đi với màu kem thì visual sang miễn bàn!",
        "cta": "Bấm theo dõi tụi mình liền nha để gom thêm cả rổ mẹo phối đồ đỉnh chóp nghen!"
      },
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio. Two chic Vietnamese female models standing side by side in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k. Both wearing tailored olive green V-neck buttoned vests. The girl on the left wears a black silk maxi skirt. The girl on the right wears a luxury cream white silk maxi skirt. --ar 9:16",
      "veo3_video_prompt": "Vertical 9:16 fashion video. In first 4 seconds, two Vietnamese women wear tailored olive green V-neck vests. Left girl in black silk skirt strikes cool pose and says in Vietnamese: 'Xanh lá với đen nhìn chất ngầu, cá tính...'. Right girl in cream white silk skirt steps forward and says in Vietnamese: '...nhưng xanh lá mà đi với màu kem thì visual sang miễn bàn!'. At 4-second mark, outfits smoothly switch back to vibrant red V-neck vests and matching silk skirts. Both girls look straight into camera with radiant smiles. Left girl says in Vietnamese: 'Bấm theo dõi tụi mình liền nha...'. Right girl finishes in Vietnamese: '...để gom thêm cả rổ mẹo phối đồ đỉnh chóp nghen!' as both girls warmly wave goodbye to camera. Perfect lip sync, crisp speech."
    }
  ],
  "subtitle_overlay": [
    {"time": "0:00-0:04", "text": "Đỏ + Trắng: Ổn | Đỏ + Be: Auto Sang Chảnh"},
    {"time": "0:04-0:08", "text": "Đen + Trắng: Sạch Sẽ | Đen + Nâu: Cực Xịn & Đắt Tiền"},
    {"time": "0:08-0:12", "text": "Hồng + Trắng: Cưng Xỉu | Hồng + Xám: Chuẩn Gu Sành Điệu"},
    {"time": "0:12-0:16", "text": "Xanh + Trắng: Tươi Tắn | Xanh + Be: Chuẩn Thanh Lịch"},
    {"time": "0:16-0:20", "text": "Xanh Lá + Đen: Cá Tính | Xanh Lá + Kem: Visual Cực Sang"},
    {"time": "0:20-0:24", "text": "FOLLOW ĐỂ XEM THÊM MẸO PHỐI ĐỒ ĐỈNH NHA!"}
  ]
}
"""

def generate():
    url = f"{ROUTER9_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ROUTER9_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gemini/gemini-3.6-flash",
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Tạo kịch bản chuẩn JSON và toàn bộ prompts Veo 3 Native Audio cho chủ đề Áo Ghi Lê Cổ V và Chân Váy Lụa."}
        ],
        "stream": True,
        "max_tokens": 3000
    }
    
    print(f"=== ĐANG GỌI 9ROUTER ({ROUTER9_BASE_URL}) BẰNG GEMINI 3.6 FLASH ===")
    res = requests.post(url, headers=headers, json=payload, timeout=90)
    res.raise_for_status()
    
    # Parse SSE text
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
    out_file = ROOT / "data" / "job6_demo_script.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print("\n✅ ĐÃ TẠO THÀNH CÔNG KỊCH BẢN VÀ PROMPTS CHO JOB 6 TỪ 9ROUTER!\n")
    print(f"📌 Tiêu đề video: {data.get('title')}")
    print(f"📌 Chủ đề trang phục: {data.get('theme')}")
    print(f"📌 Thời lượng: {data.get('total_duration')}\n")
    
    for c in data.get("clips", []):
        print(f"================================================================================")
        print(f"🎬 [CLIP {c.get('clip_number')}] ({c.get('duration')}) · {c.get('tones')}")
        print(f"================================================================================")
        print(f"🖼️ 1. Start Frame Image Prompt (Ảnh 9:16):")
        print(f"   {c.get('start_frame_image_prompt')}\n")
        print(f"🎥 2. Veo 3 Video Prompt (Native Vietnamese Speech & Lip-Sync):")
        print(f"   {c.get('veo3_video_prompt')}\n")
        
    print(f"📁 Toàn bộ JSON đã được lưu tại: {out_file}")

if __name__ == "__main__":
    generate()
