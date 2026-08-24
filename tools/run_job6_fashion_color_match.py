import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from job_types.fashion_color_match.adapter import Adapter

def generate_lookbook_job6(custom_cfg=None):
    cfg = {
        "top_item": "áo ghi lê cổ V cài nút dáng ôm",
        "bottom_item": "chân váy lụa suông dài",
        "studio_backdrop": "in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k",
        "cta_text": "Bấm theo dõi tụi mình liền nha để gom thêm cả rổ mẹo phối đồ đỉnh chóp nghen!"
    }
    if custom_cfg:
        cfg.update(custom_cfg)
        
    adapter = Adapter()
    prompts = adapter.build_veo3_prompts(cfg)
    
    print("================================================================================")
    print("✨ JOB 6: PHỐI MÀU THỜI TRANG (VEO 3 NATIVE AUDIO & LIP-SYNC 100% AUTO) ✨")
    print("================================================================================\n")
    
    for p in prompts:
        print(f"🎬 [CLIP {p['clip_id']}] · Cặp màu: {p['pair_name']} (8 Giây)")
        print(f"🖼️ 1. Start Frame Prompt (Nano Banana 2 / Imagen 3):")
        print(f"   {p['start_frame_prompt']}\n")
        print(f"🎥 2. Veo 3 Video Prompt (Chuyển động + Lời thoại Native Vietnamese nhúng thẳng):")
        print(f"   {p['video_prompt']}\n")
        print("--------------------------------------------------------------------------------\n")

    # Lưu ra file data/job6_current_batch.json
    out_file = ROOT / "data" / "job6_current_batch.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ Đã lưu toàn bộ prompts của Job 6 vào: {out_file}")

if __name__ == "__main__":
    generate_lookbook_job6()
