from __future__ import annotations
import asyncio
import json
import os
import random
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
import requests

ROOT = Path(__file__).resolve().parents[2]

FALLBACK_MODELS = [
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3-flash-preview",
    "gemini/gemini-3.7-flash",
    "gemini/gemini-3.6-flash"
]

class Adapter:
    def _generate_dynamic_script(self, product_info: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        router_base = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
        router_key = os.getenv("9ROUTER_API_KEY") or os.getenv("ROUTER9_API_KEY")
        
        product_name = product_info.get("title") or cfg.get("product_name") or "Bộ Cây Lau Nhà Tự Vắt Homepower BS-03 360 Độ"
        specs = product_info.get("specs") or {}
        specs_str = ", ".join(f"{k}: {v}" for k, v in specs.items()) if isinstance(specs, dict) else str(specs)
        style = cfg.get("review_style") or "kol_real_human"
        
        sys_prompt = f"""Bạn là đạo diễn sáng tạo kịch bản video ngắn viral hàng đầu trên TikTok / Facebook Reels (Chuyên ngành Review Đồ Gia Dụng Thông Minh Shopee).
Nhiệm vụ: Viết kịch bản video review 3 cảnh (mỗi cảnh 8s, tổng 24s) cho sản phẩm thật: '{product_name}'.
Thông số & Đặc điểm thật từ Shopee: {specs_str} (Màu sắc trắng xanh, thiết kế siêu mỏng 13cm luồn gầm tủ, đầu xoay 360 độ, hệ thống tự vắt ráo nước không chạm tay).
Phong cách: '{style}' (Nữ KOL Việt Nam trẻ trung, căn hộ sang chảnh, test thực tế cây lau nhà).

Yêu cầu kịch bản Veo 3:
- Ngôn ngữ: Lời thoại tiếng Việt tự nhiên, ngắn gọn, gắn liền với chuyển động thực tế.
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
      "image_prompt": "A stylish young Vietnamese woman in a bright modern luxury apartment living room, holding the exact uploaded white and cyan-blue self-wringing flat mop, photorealistic 8k, warm soft studio lighting. Preserve exact product design. --ar 9:16",
      "video_prompt": "A stylish young Vietnamese woman in a modern apartment holding the white and cyan-blue self-wringing mop. She smiles confidently at the camera and speaks Vietnamese with natural lip-sync: \\"Ai bảo lau nhà là cực thì xem ngay chân ái này nha!\\". Crisp 4k video, smooth natural body movement, bright clean cinematic lighting."
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
      "image_prompt": "The young Vietnamese woman standing in the gleaming clean living room, holding the mop with a radiant happy smile, thumbs up, luxury apartment background. --ar 9:16",
      "video_prompt": "The young Vietnamese woman smiling warmly in her sparkling clean living room, giving a playful wink and pointing down, speaking Vietnamese: \\"Sàn nhà bóng loáng nhàn tênh, link mình ghim ngay bên dưới nha!\\". Smooth cinematic camera pull back, vibrant 8k aesthetic."
    }}
  ]
}}"""
        if router_key:
            url = f"{router_base.rstrip('/')}/chat/completions"
            headers = {"Authorization": f"Bearer {router_key}", "Content-Type": "application/json"}
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
            "title": f"Review Cây Lau Nhà Tự Vắt Homepower 360 Độ ✨",
            "product": product_name,
            "hook": "Ai bảo lau nhà là cực thì xem ngay chân ái này nha!",
            "scenes": [
                {
                    "scene_id": 1,
                    "summary": "Mở đầu ấn tượng",
                    "dialogue": "Ai bảo lau nhà là cực thì xem ngay chân ái này nha!",
                    "image_prompt": "A stylish young Vietnamese woman in a bright modern apartment living room holding the exact uploaded white and cyan-blue self-wringing flat mop, photorealistic 8k, warm soft studio lighting. --ar 9:16",
                    "video_prompt": "A stylish young Vietnamese woman in a modern apartment holding the white and cyan-blue self-wringing mop. She smiles and speaks Vietnamese with natural lip-sync: \"Ai bảo lau nhà là cực thì xem ngay chân ái này nha!\". Clean 8k aesthetic, smooth movement."
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

    async def prepare(self, manager: Any, instance: dict[str, Any]) -> dict[str, Any]:
        cfg = instance.get("config", {})
        product_url = cfg.get("product_url") or "https://shopee.vn/B%E1%BB%99-C%C3%A2y-Lau-Nh%C3%A0-T%E1%BB%B1-V%E1%BA%AFt-Homepower-BS-03-Ch%E1%BB%95i-Lau-Nh%C3%A0-360-%C4%90%E1%BB%99-Si%C3%AAu-M%E1%BB%8Fng-4-H%E1%BB%87-Th%E1%BB%91ng-L%C3%A0m-S%E1%BA%A1ch-M%E1%BB%9Bi-i.86429796.25159024209"
        
        try:
            res = await manager.engine.call(
                "parenting", "POST", "/api/parenting/product/inspect",
                {"url": product_url, "model": "", "force_refresh": False},
                timeout=180
            )
            product_data = res.get("product") or {}
        except Exception:
            product_data = {
                "title": cfg.get("product_name") or "Bộ Cây Lau Nhà Tự Vắt Homepower BS-03 360 Độ",
                "id": "shopee_e85e439ae451ae"
            }
            
        pid = product_data.get("id") or "shopee_e85e439ae451ae"
        p_img = ROOT / "modules" / "parenting" / "outputs" / "_products" / pid / "product_main.jpg"
        if not p_img.exists():
            # Fallback to any available product image
            found = list(ROOT.glob("modules/parenting/outputs/_products/**/product_main.jpg"))
            if found:
                p_img = found[0]
                
        return {
            "product_data": product_data,
            "product_image_path": str(p_img.resolve()) if p_img.exists() else None
        }

    async def start(self, manager: Any, instance: dict[str, Any], resume_job_id: str | None = None) -> dict[str, Any]:
        prep = await self.prepare(manager, instance)
        product_data = prep.get("product_data") or {}
        product_img = prep.get("product_image_path")
        
        cfg = instance.get("config", {})
        script_data = await asyncio.to_thread(self._generate_dynamic_script, product_data, cfg)
        
        ingredients = []
        if product_img and Path(product_img).exists():
            ingredients.append({
                "role": "product",
                "assetTitle": product_data.get("title") or "Bộ Cây Lau Nhà Homepower BS-03",
                "path": product_img
            })
            
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
            
        run_uid = uuid.uuid4().hex[:8]
        run_jid = str(resume_job_id).strip() if resume_job_id else f"flow_shopee_{run_uid}"
            
        task_msg = {
            "type": "RUN_FLOW_JOB",
            "jobId": run_jid,
            "kind": "shopee_reviewer",
            "source": "beauty",
            "flow": {
                "imageModel": cfg.get("image_model", "Nano Banana 2"),
                "videoModel": cfg.get("video_model", "Veo 3.1 - Fast"),
                "videoDuration": "8s",
                "aspectRatio": "9:16",
                "imageConcurrency": 1,
                "videoConcurrency": 1
            },
            "ingredients": ingredients,
            "scenes": scenes,
            "resume": bool(resume_job_id),
            "checkpoints": db.get_scene_checkpoints(run_jid) if resume_job_id else [],
        }
        
        if manager.flow_broker:
            manager.flow_broker.enqueue("beauty", task_msg)
            
        return {
            "engine_run_id": run_jid,
            "engine_job_ids": [run_jid],
            "config": cfg,
            "script_data": script_data,
            "title": script_data.get("title"),
            "product_image_path": product_img,
            "scene_count": len(scenes)
        }

    async def wait(self, manager: Any, instance: dict[str, Any], started: dict[str, Any]) -> dict[str, Any]:
        run_jid = started.get("engine_run_id")
        cfg = started.get("config", {})
        scene_count = started.get("scene_count", 3)
        title = started.get("title") or "Review Đồ Gia Dụng Tiện Ích ✨"
        
        out_dir = ROOT / "modules" / "beauty" / "outputs" / "shopee_reviewer"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        broker = manager.flow_broker if hasattr(manager, "flow_broker") else None
        if not broker:
            raise RuntimeError("FlowBroker is not initialized in manager")

        timeout = int(cfg.get("video_timeout_sec") or cfg.get("timeout_sec") or 1800)
        # Event-driven wait (pure async, no filesystem polling)
        res = await broker.wait_job(run_jid, timeout=timeout, expected_scenes=scene_count)
        raw_clips = res.get("video_paths") or []
        if not raw_clips:
            raw_clips = broker.get_job_clips(run_jid)
            
        if not raw_clips:
            err = res.get("error") or "Không nhận được clip nào từ Flow Worker"
            raise RuntimeError(f"Job 7 render thất bại cho {run_jid}: {err}")

        final_merged = out_dir / f"{run_jid}_final.mp4"
        from core.ffmpeg_utils import merge_scene_videos
        merged_file = await merge_scene_videos(raw_clips, final_merged, timeout=120)
        video_paths = [str(merged_file.resolve())]
        
        cta = started.get("config", {}).get("cta_text") or "Link mua hàng chính hãng mình để ngay dưới phần bình luận nha!"
        caption = f"{title}\n\n{cta}"
        return {
            "title": title,
            "caption": caption,
            "video_paths": video_paths,
            "raw": {"jobId": run_jid, "clips": raw_clips}
        }
