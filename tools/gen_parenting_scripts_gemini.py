import json
import os
import re
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ROUTER9_BASE_URL = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
ROUTER9_API_KEY = os.getenv("9ROUTER_API_KEY")

TOPICS = [
    {"id": 1, "topic": "Bé khóc ăn vạ đòi mua kẹo ở siêu thị", "template": "problem_solution"},
    {"id": 2, "topic": "Bé không chịu chia sẻ đồ chơi với bạn", "template": "mother_teaches_ai"},
    {"id": 3, "topic": "Bé sợ bóng tối và tưởng tượng có quái vật trong tủ", "template": "problem_solution"},
    {"id": 4, "topic": "Bé nghiện xem điện thoại khi ăn cơm", "template": "problem_solution"},
    {"id": 5, "topic": "Bé làm đổ cốc sữa ra bàn và sợ bị mẹ mắng", "template": "mother_teaches_ai"},
    {"id": 6, "topic": "Dạy bé tập nói tiếng Anh qua các con vật trong nhà", "template": "english_context"},
    {"id": 7, "topic": "Đồ chơi xe khủng long nuốt ô tô đường ray rèn phản xạ", "template": "unbox_play"},
    {"id": 8, "topic": "Bé lười dọn đồ chơi và biến việc nhà thành trò chơi vui", "template": "direct_demo"},
    {"id": 9, "topic": "Bé tập tự đi giày và học tính tự lập", "template": "mini_challenge"},
    {"id": 10, "topic": "Bảng bận rộn đa năng Busy Board rèn luyện vận động tinh", "template": "unbox_play"}
]

SYSTEM_PROMPT = """Bạn là chuyên gia biên kịch video ngắn hàng đầu cho kênh Mẹ & Bé (Phong cách hoạt hình 3D Pixar/Disney ấm áp, giàu cảm xúc, giáo dục tích cực).
Nhiệm vụ: Viết kịch bản video ngắn Facebook Reels / TikTok (32 giây - gồm đúng 4 scenes).

Yêu cầu nghiêm ngặt:
1. Nhân vật:
   - Mẹ: Khoảng 30 tuổi, giọng ấm áp, dịu dàng, kiên nhẫn, mặc trang phục thanh lịch gia đình.
   - Bé: Bé gái/bé trai 3-4 tuổi, ngây thơ, biểu cảm sống động, giọng nói dễ thương tự nhiên.
2. Cấu trúc 4 scenes (mỗi scene 8 giây):
   - Scene 1: Hook / Tình huống mâu thuẫn đời thường hoặc unbox.
   - Scene 2: Tương tác / Mẹ gợi mở hoặc hướng dẫn thực tế.
   - Scene 3: Chuyển biến tích cực / Bé học được hoặc hào hứng trải nghiệm.
   - Scene 4: Bài học ấm áp / Lời khuyên cho cha mẹ / Call to action.
3. Thoại: Tiếng Việt tự nhiên, ngắn gọn (mỗi lượt thoại dưới 15 từ), dễ thương.
4. Visual Prompt: Bắt buộc chi tiết bằng tiếng Anh theo phong cách 3D Pixar Animation, mô tả rõ hành động, biểu cảm khuôn mặt và ánh sáng điện ảnh ấm áp.

Trả về DUY NHẤT một JSON hợp lệ (không kèm giải thích ngoài JSON) theo mẫu:
{
  "title": "Tiêu đề video hấp dẫn kèm emoji",
  "topic": "Chủ đề",
  "template": "template_name",
  "target_duration": "32s",
  "scenes": [
    {
      "scene_id": 1,
      "visual_prompt": "Prompt tiếng Anh 3D Pixar style...",
      "dialogue": [
        {"speaker": "Bé", "text": "Câu thoại ngắn..."},
        {"speaker": "Mẹ", "text": "Câu thoại ngắn..."}
      ]
    },
    {
      "scene_id": 2,
      "visual_prompt": "Prompt tiếng Anh 3D Pixar style...",
      "dialogue": [
        {"speaker": "Mẹ", "text": "Câu thoại ngắn..."},
        {"speaker": "Bé", "text": "Câu thoại ngắn..."}
      ]
    },
    {
      "scene_id": 3,
      "visual_prompt": "Prompt tiếng Anh 3D Pixar style...",
      "dialogue": [
        {"speaker": "Bé", "text": "Câu thoại ngắn..."},
        {"speaker": "Mẹ", "text": "Câu thoại ngắn..."}
      ]
    },
    {
      "scene_id": 4,
      "visual_prompt": "Prompt tiếng Anh 3D Pixar style...",
      "dialogue": [
        {"speaker": "Mẹ", "text": "Câu thoại ngắn..."}
      ],
      "moral_or_cta": "Thông điệp đúc kết hoặc kêu gọi hành động cho cha mẹ"
    }
  ]
}
"""

def parse_router9_response(text: str) -> str:
    text = (text or "").strip()
    # Try standard JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            if not content:
                content = (((data.get("choices") or [{}])[0].get("delta") or {}).get("content") or "")
            if content:
                return content.strip()
    except Exception:
        pass

    # Parse SSE chunks
    parts = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                choice = (obj.get("choices") or [{}])[0] or {}
                delta = choice.get("delta") or {}
                msg = choice.get("message") or {}
                piece = delta.get("content") if isinstance(delta, dict) else None
                if piece is None and isinstance(msg, dict):
                    piece = msg.get("content")
                if piece:
                    parts.append(str(piece))
        except Exception:
            continue
    return "".join(parts).strip()

def clean_json_str(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    # Find outer curly braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end+1]
    return text

def generate_script_with_gemini(topic_info, model="gemini/gemini-3.7-flash"):
    url = f"{ROUTER9_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ROUTER9_API_KEY}",
        "Content-Type": "application/json"
    }
    user_prompt = f"Hãy viết kịch bản video ngắn chuẩn 4 scenes cho chủ đề: '{topic_info['topic']}', thể loại/template: '{topic_info['template']}'."
    
    payload = {
        "model": model,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "stream": True,
        "max_tokens": 2048
    }
    
    res = requests.post(url, headers=headers, json=payload, timeout=60)
    res.raise_for_status()
    raw_content = parse_router9_response(res.text)
    if not raw_content:
        raise ValueError(f"Không nhận được nội dung từ 9Router. Raw: {res.text[:200]}")
    
    cleaned = clean_json_str(raw_content)
    return json.loads(cleaned)

def main():
    print(f"=== BẮT ĐẦU GEN 10 KỊCH BẢN MẸ & BÉ QUA GEMINI (9ROUTER: {ROUTER9_BASE_URL}) ===")
    
    # 1. Ưu tiên model Gemini 3.6 Flash cực nhanh và chuẩn JSON
    models_to_try = [
        "gemini/gemini-3.6-flash",
        "gemini/gemini-3.7-flash",
        "gemini/gemini-3-flash-preview"
    ]
    active_model = "gemini/gemini-3.6-flash"
    for m in models_to_try:
        try:
            r = requests.post(f"{ROUTER9_BASE_URL.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {ROUTER9_API_KEY}"}, json={
                "model": m,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": True,
                "max_tokens": 10
            }, timeout=8)
            content = parse_router9_response(r.text)
            if content:
                active_model = m
                print(f"-> Đã kết nối thành công model: {active_model}\n")
                break
        except Exception:
            continue
            
    if not active_model:
        active_model = "gemini/gemini-3.7-flash"
        print(f"-> Dùng model mặc định: {active_model}\n")

    all_scripts = []
    output_dir = ROOT / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "generated_parenting_scripts_gemini.json"

    for idx, item in enumerate(TOPICS, 1):
        print(f"[{idx}/10] Đang gen: {item['topic']} ({item['template']})...", flush=True)
        t0 = time.time()
        try:
            script_data = generate_script_with_gemini(item, model=active_model)
            all_scripts.append(script_data)
            print(f"  [OK] Hoàn tất trong {time.time()-t0:.2f}s | Tiêu đề: {script_data.get('title')}")
        except Exception as exc:
            print(f"  [LỖI]: {exc}")
            # Fallback model nếu cần
            try:
                print("  -> Thử lại với gemini-3.6-flash...")
                script_data = generate_script_with_gemini(item, model="gemini/gemini-3.6-flash")
                all_scripts.append(script_data)
                print(f"  [OK Fallback] Tiêu đề: {script_data.get('title')}")
            except Exception as e2:
                print(f"  [LỖI LẦN 2]: {e2}")

    # Ghi toàn bộ kịch bản ra file JSON
    out_file.write_text(json.dumps(all_scripts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=======================================================")
    print(f"ĐÃ TẠO THÀNH CÔNG {len(all_scripts)} KỊCH BẢN!")
    print(f"Đã lưu kết quả tại: {out_file}")
    print(f"=======================================================")

if __name__ == "__main__":
    main()
