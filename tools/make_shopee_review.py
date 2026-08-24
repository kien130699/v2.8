import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ROUTER9_BASE_URL = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
ROUTER9_API_KEY = os.getenv("9ROUTER_API_KEY") or os.getenv("ROUTER9_API_KEY")

FALLBACK_MODELS = [
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3-flash-preview",
    "gemini/gemini-3.7-flash",
    "gemini/gemini-3.6-flash"
]

def inspect_shopee(product_url: str):
    print("🔍 1. ĐANG CÀO DỮ LIỆU & ẢNH GỐC TỪ LINK SHOPEE...")
    try:
        payload = json.dumps({"url": product_url, "model": "", "force_refresh": False})
        res = requests.post("http://127.0.0.1:3000/engine/parenting/api/parenting/product/inspect", data=payload, headers={"Content-Type": "application/json"}, timeout=45)
        if res.status_code == 200:
            data = res.json()
            product = data.get("product") or {}
            pid = product.get("id") or "shopee_e85e439ae451ae"
            p_img = ROOT / "modules" / "parenting" / "outputs" / "_products" / pid / "product_main.jpg"
            print(f"✅ ĐÃ CÀO THÀNH CÔNG: {product.get('title')}")
            print(f"📸 ẢNH SẢN PHẨM GỐC: {p_img}")
            return product, str(p_img.resolve()) if p_img.exists() else None
    except Exception as e:
        print(f"⚠️ Inspect fallback: {e}")
        
    p_img = ROOT / "modules" / "parenting" / "outputs" / "_products" / "shopee_e85e439ae451ae" / "product_main.jpg"
    return {
        "title": "Bộ Cây Lau Nhà Tự Vắt Homepower BS-03, Chổi Lau Nhà 360 Độ Siêu Mỏng 4 Hệ Thống Làm Sạch Mới",
        "id": "shopee_e85e439ae451ae"
    }, str(p_img.resolve()) if p_img.exists() else None

def generate_review_script(product_info: dict[str, Any], style: str = "kol_real_human"):
    product_name = product_info.get("title") or "Bộ Cây Lau Nhà Tự Vắt Homepower BS-03 360 Độ"
    print(f"\n🎬 2. ĐANG GỌI AI ĐỂ VIẾT KỊCH BẢN REVIEW CHUẨN XÁC...")
    url = f"{ROUTER9_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {ROUTER9_API_KEY}", "Content-Type": "application/json"}
    
    sys_prompt = f"""Bạn là đạo diễn sáng tạo kịch bản video ngắn viral hàng đầu trên TikTok / Facebook Reels (Chuyên ngành Review Đồ Gia Dụng Thông Minh Shopee).
Nhiệm vụ: Viết kịch bản video review 3 cảnh (mỗi cảnh 8s, tổng 24s) cho sản phẩm thật: '{product_name}'.
Đặc điểm nhận diện thật: Cây lau nhà tự vắt Homepower BS-03 màu trắng xanh, thiết kế siêu mỏng 13cm luồn gầm, đầu xoay 360 độ, hệ thống thanh gạt tự vắt ráo nước không chạm tay.
Phong cách: '{style}' (Nữ KOL Việt Nam trẻ trung, căn hộ sang chảnh, test thực tế).

Yêu cầu kịch bản Veo 3:
- Ngôn ngữ: Lời thoại tiếng Việt tự nhiên, ngắn gọn, hấp dẫn, gắn liền với chuyển động thực tế.
- Scene 1 (0-8s): Giật hook gây tò mò về cây lau nhà tự vắt 360 độ siêu mỏng.
- Scene 2 (8-16s): Demo cận cảnh khả năng tự vắt không chạm tay và đầu chổi 360 độ luồn sâu gầm giường/gầm tủ.
- Scene 3 (16-24s): Sàn nhà sáng bóng không tì vết, bạn nữ tươi cười chia sẻ cảm nhận và kêu gọi bấm link mua.

Trả về DUY NHẤT một JSON hợp lệ:
{{
  "title": "Tiêu đề video cực hút kèm emoji",
  "product": "{product_name}",
  "hook": "Câu mở đầu giật gân",
  "scenes": [
    {{
      "scene_id": 1,
      "summary": "Mở đầu ấn tượng",
      "dialogue": "Ai bảo lau nhà là cực thì xem ngay chân ái này nha!",
      "image_prompt": "A stylish young Vietnamese woman in a modern luxury apartment living room, holding the uploaded white and cyan-blue self-wringing flat mop, photorealistic 8k, warm soft studio lighting. --ar 9:16",
      "video_prompt": "A stylish young Vietnamese woman in a modern apartment holding the white and cyan-blue self-wringing mop. She smiles confidently at the camera and speaks Vietnamese with natural lipsync: \\"Ai bảo lau nhà là cực thì xem ngay chân ái này nha!\\". Crisp 4k video, smooth natural body movement, bright clean cinematic lighting."
    }},
    {{
      "scene_id": 2,
      "summary": "Demo công năng tự vắt và xoay 360",
      "dialogue": "Tự vắt ráo nước một chạm, đầu chổi mỏng dính luồn sạch mọi ngóc ngách!",
      "image_prompt": "Close-up action shot of the white and cyan-blue ultra-thin 360-degree flat mop effortlessly sliding under low furniture and sofa, clean wooden floor, 8k photorealistic. --ar 9:16",
      "video_prompt": "Close-up shot of the young woman using the ultra-thin 360-degree flat mop to effortlessly clean under the sofa and sliding the self-wringer mechanism smoothly without touching dirty water. She says: \\"Tự vắt ráo nước một chạm, đầu chổi mỏng dính luồn sạch mọi ngóc ngách!\\". Realistic fluid motion, clear ambient sound."
    }},
    {{
      "scene_id": 3,
      "summary": "Kết quả sạch bóng và CTA",
      "dialogue": "Sàn nhà bóng loáng nhàn tênh, link mình ghim ngay bên dưới nha!",
      "image_prompt": "The young Vietnamese woman standing in the gleaming clean living room, holding the mop with a radiant happy smile, thumbs up, modern luxury backdrop. --ar 9:16",
      "video_prompt": "The young Vietnamese woman smiling warmly in her sparkling clean living room, giving a playful wink and pointing down, speaking Vietnamese: \\"Sàn nhà bóng loáng nhàn tênh, link mình ghim ngay bên dưới nha!\\". Smooth cinematic camera pull back, vibrant 8k aesthetic."
    }}
  ]
}}"""
    
    if ROUTER9_API_KEY:
        for model in FALLBACK_MODELS:
            try:
                payload = {
                    "model": model,
                    "temperature": 0.7,
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": f"Hãy viết kịch bản 3 cảnh review cho: '{product_name}'."}
                    ],
                    "stream": False,
                    "max_tokens": 2500
                }
                res = requests.post(url, headers=headers, json=payload, timeout=25)
                if res.status_code == 200:
                    content = res.json()["choices"][0]["message"]["content"]
                    raw = re.sub(r"^```(?:json)?", "", content.strip()).rstrip("`").strip()
                    s = raw.find("{")
                    e = raw.rfind("}")
                    if s != -1 and e != -1:
                        data = json.loads(raw[s:e+1])
                        if data.get("scenes") and len(data["scenes"]) >= 3:
                            return data
            except Exception:
                continue

    return {
        "title": f"Review Chân Ái Cây Lau Nhà Tự Vắt Homepower 360 Độ ✨",
        "product": product_name,
        "hook": "Ai bảo lau nhà là cực thì xem ngay chân ái này nha!",
        "scenes": [
            {
                "scene_id": 1,
                "summary": "Mở đầu ấn tượng",
                "dialogue": "Ai bảo lau nhà là cực thì xem ngay chân ái này nha!",
                "image_prompt": "A stylish young Vietnamese woman in a bright modern apartment living room holding the white and cyan-blue self-wringing flat mop, photorealistic 8k, warm soft studio lighting. --ar 9:16",
                "video_prompt": "A stylish young Vietnamese woman in a modern apartment holding the white and cyan-blue self-wringing flat mop. She smiles and speaks Vietnamese with natural lip-sync: \"Ai bảo lau nhà là cực thì xem ngay chân ái này nha!\". Clean 8k aesthetic, smooth movement."
            },
            {
                "scene_id": 2,
                "summary": "Demo công năng",
                "dialogue": "Đầu chổi 360 luồn sâu gầm tủ, tự vắt ráo nước không cần chạm tay!",
                "image_prompt": "Close-up of the ultra-thin 360-degree flat mop effortlessly gliding under sofa on wooden floor, clean modern home, 8k. --ar 9:16",
                "video_prompt": "Close-up of the woman pushing the 360-degree mop smoothly under a low couch and demonstrating the easy self-wringing slider. She says in Vietnamese: \"Đầu chổi 360 luồn sâu gầm tủ, tự vắt ráo nước không cần chạm tay!\". Realistic motion."
            },
            {
                "scene_id": 3,
                "summary": "Đoạn kết & CTA",
                "dialogue": "Sàn nhà sáng bóng nhàn tênh, link chính hãng mình để bên dưới nha!",
                "image_prompt": "The young Vietnamese woman smiling happily in the sparkling clean room, holding the mop, luxury apartment background. --ar 9:16",
                "video_prompt": "The woman standing in her sparkling clean room, smiling cheerfully and pointing down, saying in Vietnamese: \"Sàn nhà sáng bóng nhàn tênh, link chính hãng mình để bên dưới nha!\". Warm cinematic finish."
            }
        ]
    }

def queue_to_server(script_data, product_img_path):
    print("\n🚀 3. ĐANG GỬI TÁC VỤ KÈM ẢNH GỐC SẢN PHẨM VÀO FLOW WORKER...")
    run_uid = uuid.uuid4().hex[:8]
    job_id = f"flow_shopee_{run_uid}"
    
    ingredients = []
    if product_img_path and Path(product_img_path).exists():
        ingredients.append({
            "role": "product",
            "assetTitle": script_data.get("product") or "Bộ Cây Lau Nhà Homepower BS-03",
            "path": product_img_path
        })
        print(f"📎 ĐÃ ĐÍNH KÈM REFERENCE IMAGE SẢN PHẨM THẬT: {product_img_path}")
    
    scenes = []
    for idx, sc in enumerate(script_data.get("scenes", []), 1):
        scenes.append({
            "index": idx,
            "serverSceneIndex": idx,
            "imagePrompt": sc.get("image_prompt", ""),
            "videoPrompt": sc.get("video_prompt", ""),
            "videoDuration": "8s",
            "aspectRatio": "9:16",
            "ingredients": ingredients
        })
        
    task_msg = {
        "type": "RUN_FLOW_JOB",
        "jobId": job_id,
        "kind": "shopee_reviewer",
        "source": "beauty",
        "flow": {
            "imageModel": "Nano Banana 2",
            "videoModel": "Veo 3.1 - Fast",
            "videoDuration": "8s",
            "aspectRatio": "9:16",
            "imageConcurrency": 1,
            "videoConcurrency": 1
        },
        "ingredients": ingredients,
        "scenes": scenes
    }
    
    try:
        r = requests.post("http://127.0.0.1:3000/api/flow/enqueue", json=task_msg, timeout=10)
        if r.status_code == 200:
            print(f"✅ Đã gửi thành công vào Server Queue! Job ID: {job_id}")
            return job_id
    except Exception:
        pass
        
    return job_id

def main():
    parser = argparse.ArgumentParser(description="Tạo video review Shopee bằng AI kèm ảnh sản phẩm gốc")
    parser.add_argument("--url", default="https://shopee.vn/B%E1%BB%99-C%C3%A2y-Lau-Nh%C3%A0-T%E1%BB%B1-V%E1%BA%AFt-Homepower-BS-03-Ch%E1%BB%95i-Lau-Nh%C3%A0-360-%C4%90%E1%BB%99-Si%C3%AAu-M%E1%BB%8Fng-4-H%E1%BB%87-Th%E1%BB%91ng-L%C3%A0m-S%E1%BA%A1ch-M%E1%BB%9Bi-i.86429796.25159024209")
    parser.add_argument("--style", default="kol_real_human")
    args = parser.parse_args()
    
    product_data, img_path = inspect_shopee(args.url)
    script = generate_review_script(product_data, args.style)
    
    print("\n" + "="*70)
    print(f"📌 TIÊU ĐỀ: {script.get('title')}")
    print(f"📌 SẢN PHẨM: {script.get('product')}")
    print(f"📌 HOOK: \"{script.get('hook')}\"")
    print("="*70)
    
    for sc in script.get("scenes", []):
        print(f"\n🎬 [CẢNH {sc.get('scene_id')}/3] - {sc.get('summary')}")
        print(f"  🗣️ Lời thoại: \"{sc.get('dialogue', '')}\"")
        print(f"  🖼️ Image Prompt: {sc.get('image_prompt')}")
        print(f"  🎥 Veo3 Video Prompt: {sc.get('video_prompt')}")
        
    print("\n" + "="*70)
    job_id = queue_to_server(script, img_path)
    print(f"🎉 TÁC VỤ ĐÃ SẴN SÀNG TRÊN FLOW VỚI MÃ: {job_id}")

if __name__ == "__main__":
    main()
