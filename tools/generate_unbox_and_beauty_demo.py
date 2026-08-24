from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

BASE = "http://127.0.0.1:3000"

def generate_demo():
    print("=== 1. DEMO CHUC NANG UNBOX SAN PHAM (UNBOX PLAY) ===")
    
    # Kịch bản sản phẩm mẫu: Xe khủng long nuốt ô tô
    unbox_demo_data = {
        "product_id": "shopee_dino_car_track",
        "product_name": "Đồ chơi xe khủng long nuốt ô tô đường ray phiên bản mới",
        "origin_url": "https://shopee.vn/do-choi-xe-khung-long-nuot-o-to-i.489501653.27171031836",
        "affiliate_url": "https://s.shopee.vn/demo_unbox_dino_track",
        "template": "unbox_play",
        "duration": "32s (4 clips x 8s)",
        "scenes": [
            {
                "scene_number": 1,
                "title": "Clip 1: Mở Hộp & Gặp Gỡ Khủng Long (Unboxing Moment)",
                "time": "0s - 8s",
                "image_prompt": "Young Asian mother around 30 and cute 4-year-old Asian girl sitting on a cozy living room rug, happily unboxing a large vibrant green dinosaur toy car track box, pristine product packaging with colorful dinosaur art, warm soft lighting, cinematic 3D family animation style.",
                "video_prompt": "The mother and little girl eagerly open the large cardboard box together, pulling out the big friendly green dinosaur toy head, the little girl claps and smiles with excitement, smooth camera push-in, natural cheerful movement, 8s.",
                "dialogue": [
                    {"speaker": "Bé", "text": "Oa mẹ ơi, hộp quà to quá! Là bạn khủng long xanh nè!"},
                    {"speaker": "Mẹ", "text": "Đúng rồi con, hôm nay hai mẹ con mình cùng đập hộp đồ chơi mới nha!"}
                ]
            },
            {
                "scene_number": 2,
                "title": "Clip 2: Khám Phá & Lắp Ráp Đường Ray (Assembling & Parts)",
                "time": "8s - 16s",
                "image_prompt": "Close-up shot of mother's gentle hands clicking colorful plastic track pieces into the back of the green dinosaur toy, smooth rounded safe ABS plastic parts, 6 mini alloy racing cars arranged neatly beside, bright warm playroom background, 3D animation render.",
                "video_prompt": "Mother smoothly snaps the track tail into place and tests the spring lever while the child watches attentively, hands holding a small red racing car, camera rotates gently around the toy setup, 8s.",
                "dialogue": [
                    {"speaker": "Mẹ", "text": "Khung ray nhựa ABS bo tròn rất êm tay nhé, lắp vào cực kỳ dễ dàng luôn."},
                    {"speaker": "Bé", "text": "Để con chuẩn bị đoàn xe tí hon xếp hàng chuẩn bị chạy nha mẹ!"}
                ]
            },
            {
                "scene_number": 3,
                "title": "Clip 3: Trải Nghiệm & Khủng Long Nuốt Xe (Interactive Play)",
                "time": "16s - 24s",
                "image_prompt": "Excited little girl pushing the giant green dinosaur forward, dinosaur opening its jaws and swallowing mini cars as they roll through its belly track and slide out the tail ramp, dynamic action shot, joyful expressions, warm ambient playroom light.",
                "video_prompt": "The dinosaur rolls forward smoothly over the floor, mouth chomping and scooping up 3 mini cars in sequence, cars zooming through the internal track and ejecting out the back, child laughing joyfully, 8s.",
                "dialogue": [
                    {"speaker": "Bé", "text": "Nhìn này mẹ ơi! Bạn khủng long nuốt chửng xe rồi phóng vèo ra sau luôn!"},
                    {"speaker": "Mẹ", "text": "Xe chạy mượt ghê con ha, không dùng pin mà chạy bon bon cả ngày."}
                ]
            },
            {
                "scene_number": 4,
                "title": "Clip 4: Đánh Giá & Lời Khuyên Cho Phụ Huynh (Review & CTA)",
                "time": "24s - 32s",
                "image_prompt": "Mother holding the dinosaur toy neatly folded with all cars stored inside its belly compartment, smiling gently towards the camera with child playing happily in background, cozy clean room, premium warm commercial look.",
                "video_prompt": "Mother demonstrates storing all 6 mini cars inside the dinosaur body neatly, smiling and nodding approvingly to the camera, caption text gently appearing, child waving happily, 8s.",
                "dialogue": [
                    {"speaker": "Mẹ", "text": "Chơi xong cất gọn xe vào bụng khủng long siêu tiện. Món quà vừa rèn phản xạ vừa giúp bé hạn chế xem điện thoại. Ba mẹ tham khảo link bên dưới nhé!"}
                ]
            }
        ],
        "caption_and_affiliate": (
            "🔥 ĐẬP HỘP XE KHỦNG LONG NUỐT Ô TÔ ĐƯỜNG RAY SIÊU CUỐN CHO BÉ YÊU!\n"
            "✨ Chất liệu nhựa ABS bền đẹp, không góc cạnh sắc nhọn.\n"
            "✨ Tự động nuốt xe và trượt ray không cần dùng pin, kèm sẵn 6 xe hợp kim.\n"
            "👉 Link săn sale chính hãng Shopee tại đây ba mẹ nhé: https://s.shopee.vn/demo_unbox_dino_track\n"
            "#dochoitreem #unboxdochoi #xekhunglong #mevabe #shopeeaffiliate"
        )
    }

    print("\n--- KET QUA KICH BAN UNBOX PLAY ---")
    print(json.dumps(unbox_demo_data, ensure_ascii=False, indent=2))

    print("\n\n=== 2. TEST GAI XINH (JOB 2 - BEAUTY) VOI PERSONA D:\\YT\\Code\\1b94221d-e3d4-435f-91a2-f7688811b8bd.png ===")
    
    persona_path = r"D:\YT\Code\1b94221d-e3d4-435f-91a2-f7688811b8bd.png"
    if not os.path.exists(persona_path):
        print(f"Lỗi: Không tìm thấy file persona tại {persona_path}")
        return

    # Upload persona lên Job 2.1 trên server
    with open(persona_path, "rb") as f:
        r = requests.post(
            f"{BASE}/api/jobs/2.1/assets/persona_path",
            files={"file": ("persona_pink_dress.png", f, "image/png")}
        )
    print("Upload persona lên Job 2.1:", r.status_code, r.json().get("ok"))

    # Lấy thông tin Job 2.1 sau khi cập nhật
    r_job = requests.get(f"{BASE}/api/jobs/2.1")
    job_info = r_job.json()
    print("Job 2.1 Name:", job_info.get("name"))
    print("Persona path lưu trong config:", job_info.get("config", {}).get("persona_path"))

    # Tạo mẫu prompt Image & Video tương thích với Persona này
    beauty_workflow_demo = {
        "persona_visual_summary": "Attractive East Asian woman, long wavy dark hair, elegant pink bodycon halter mini dress, glamorous curvy silhouette, high heels, luxury lifestyle aesthetic.",
        "generated_scenes": [
            {
                "scene": 1,
                "location": "Ban công căn hộ Penthouse nhìn ra Landmark 81 & Skyline Sài Gòn hoàng hôn",
                "outfit": "Đầm bodycon halter màu hồng phấn ôm dáng sang trọng",
                "image_prompt": "1girl, glamorous East Asian woman with long wavy dark hair in elegant pink halter bodycon mini dress, standing on a luxury modern high-rise balcony, golden hour skyline of Saigon with Landmark 81 in background, photorealistic 8k, cinematic lighting, 9:16 portrait.",
                "video_prompt": "The woman gently turns towards the camera, smiling warmly as gentle breeze moves her hair, soft golden hour sunlight highlighting the modern city skyline behind her, smooth slow motion movement, 8s."
            },
            {
                "scene": 2,
                "location": "Phố đi bộ Nguyễn Huệ / Quán cà phê cao cấp Thảo Điền TP.HCM",
                "outfit": "Váy ngắn quyến rũ kết hợp phụ kiện tinh tế",
                "image_prompt": "1girl, glamorous East Asian woman with long wavy hair in stylish pink dress, walking gracefully along upscale city walkway with warm ambient evening lights, bokeh background, elegant posture, 9:16.",
                "video_prompt": "Walking slowly forward with confident graceful steps, holding a small chic handbag, glancing back with an alluring smile, smooth gimbal camera tracking, 8s."
            }
        ]
    }
    print("\n--- KET QUA WORKFLOW GAI XINH (BEAUTY I2V) ---")
    print(json.dumps(beauty_workflow_demo, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    generate_demo()
