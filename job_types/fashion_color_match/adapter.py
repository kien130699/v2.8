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

FASHION_THEMES = [
    {"top": "áo ghi lê cài nút dáng ôm", "bottom": "chân váy lụa suông dài", "style": "Mix & Match Phong Cách Tiểu Thư & Sang Chảnh"},
    {"top": "áo blazer croptop thanh lịch", "bottom": "quần ống suông cạp cao", "style": "Thời Trang Công Sở & Dạo Phố Hiện Đại"},
    {"top": "áo len dệt kim tone pastel", "bottom": "chân váy chữ A xếp ly", "style": "Tone Màu Ngọt Ngào Trẻ Trung Năng Động"},
    {"top": "áo dạ tweed cổ tròn tiểu thư", "bottom": "quần suông ống rộng thanh thoát", "style": "Gu Thời Trang Sang Trọng & Quyền Lực"}
]

FALLBACK_MODELS = [
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3-flash-preview",
    "gemini/gemini-3.7-flash",
    "gemini/gemini-3.6-flash"
]

class Adapter:
    def _generate_dynamic_script(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """Tự động gọi 9Router AI để sinh kịch bản thời trang mới lạ, đa dạng cho từng Job."""
        router_base = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
        router_key = os.getenv("9ROUTER_API_KEY") or os.getenv("ROUTER9_API_KEY")
        
        theme_pick = random.choice(FASHION_THEMES)
        top_name = cfg.get("top_item") or theme_pick["top"]
        bottom_name = cfg.get("bottom_item") or theme_pick["bottom"]
        style_name = theme_pick["style"]
        
        sys_prompt = f"""Bạn là Chuyên gia Kỹ thuật Prompts AI Video hàng đầu cho kênh Thời Trang Nữ (Job 6: Phối Màu Thời Trang · Veo 3 Native Audio & Lip-Sync).
Nhiệm vụ: Tạo kịch bản và toàn bộ Prompts Video Veo 3 (có nhúng native Vietnamese dialogue) cho video 24s (3 clips x 8s).
Trang phục chính: {top_name} kết hợp cùng {bottom_name}.
Phong cách chủ đạo: {style_name}.

Quy tắc bắt buộc:
1. Đa dạng hóa các cặp màu và bối cảnh lookbook studio 8k sắc nét:
   - Clip 1 (8s): 4s đầu cặp màu A (2 người mặc cùng kiểu áo nhưng phối 2 màu chân váy khác nhau để so sánh), 4s sau nhảy cảnh sang cặp màu B.
   - Clip 2 (8s): 4s đầu cặp màu C, 4s sau nhảy cảnh sang cặp màu D.
   - Clip 3 (8s): 4s đầu cặp màu E, 4s sau nhảy cảnh sang Đoạn Kết CTA Kêu Gọi Follow kênh.
2. Nhúng trực tiếp thoại tiếng Việt đối đáp tự nhiên vào prompt Veo 3:
   The girl on the left ... speaks in Vietnamese: '...'
   The girl on the right ... replies in Vietnamese: '...'

Trả về DUY NHẤT một JSON hợp lệ dạng:
{{
  "title": "Mẹo Phối Màu Thời Trang Chuẩn Gu 2026 ✨",
  "theme": "{style_name}",
  "total_duration": "24s",
  "clips": [
    {{
      "clip_number": 1,
      "duration": "8s",
      "tones": "Tên cặp màu 1",
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio...",
      "veo3_video_prompt": "Vertical 9:16 fashion lookbook video, static full-body shot. Two stylish Vietnamese women..."
    }},
    {{
      "clip_number": 2,
      "duration": "8s",
      "tones": "Tên cặp màu 2",
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio...",
      "veo3_video_prompt": "Vertical 9:16 fashion video, static medium-full shot..."
    }},
    {{
      "clip_number": 3,
      "duration": "8s",
      "tones": "Tên cặp màu 3 và Đoạn kết CTA",
      "start_frame_image_prompt": "A full-body vertical fashion shot, 9:16 aspect ratio...",
      "veo3_video_prompt": "Vertical 9:16 fashion video..."
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
                            {"role": "user", "content": f"Hãy tạo kịch bản JSON phối màu mới lạ cho {top_name} và {bottom_name}."}
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
                            if data.get("clips") and len(data["clips"]) >= 3:
                                return data
                except Exception:
                    continue
                    
        # Fallback pool nếu không có mạng / AI bận
        return {
            "title": f"Bí Quyết Phối Màu {top_name.title()} & {bottom_name.title()} ✨",
            "theme": style_name,
            "clips": self.build_veo3_prompts(cfg)
        }

    async def prepare(self, manager: Any, instance: dict[str, Any]) -> str:
        ref = instance.get("engine_ref") or f"fashion_{instance['id'].replace('.', '_')}"
        return ref

    async def start(self, manager: Any, instance: dict[str, Any], resume_job_id: str | None = None) -> dict[str, Any]:
        ref = await self.prepare(manager, instance)
        manager.set_engine_ref(instance["id"], ref)
        
        cfg = instance.get("config", {})
        
        # 1. Tự động sinh kịch bản mới bằng AI
        script_data = await asyncio.to_thread(self._generate_dynamic_script, cfg)
        
        scenes = []
        custom_title = script_data.get("title")
        for idx, c in enumerate(script_data.get("clips", []), 1):
            scenes.append({
                "index": idx,
                "serverSceneIndex": idx,
                "imagePrompt": c.get("start_frame_image_prompt") or c.get("start_frame_prompt", ""),
                "videoPrompt": c.get("veo3_video_prompt") or c.get("video_prompt", ""),
                "videoDuration": c.get("duration", "8s"),
                "aspectRatio": "9:16"
            })
            
        if not scenes:
            prompts = self.build_veo3_prompts(cfg)
            for idx, p in enumerate(prompts, 1):
                scenes.append({
                    "index": idx,
                    "serverSceneIndex": idx,
                    "imagePrompt": p["start_frame_prompt"],
                    "videoPrompt": p["video_prompt"],
                    "videoDuration": "8s",
                    "aspectRatio": "9:16"
                })
        else:
            prompts = scenes
            
        run_uid = uuid.uuid4().hex[:8]
        run_jid = str(resume_job_id).strip() if resume_job_id else f"flow_job6_{run_uid}"
            
        task_msg = {
            "type": "RUN_FLOW_JOB",
            "jobId": run_jid,
            "resume": bool(resume_job_id),
            "checkpoints": db.get_scene_checkpoints(run_jid) if resume_job_id else [],
            "kind": "fashion_color_match",
            "source": "beauty",
            "flow": {
                "imageModel": cfg.get("image_model", "Nano Banana 2"),
                "videoModel": cfg.get("video_model", "Veo 3.1 - Fast"),
                "videoDuration": "8s",
                "aspectRatio": "9:16",
                "imageConcurrency": 1,
                "videoConcurrency": 1
            },
            "scenes": scenes
        }
        
        if manager.flow_broker:
            manager.flow_broker.enqueue("beauty", task_msg)
            
        return {
            "engine_run_id": run_jid,
            "engine_job_ids": [run_jid],
            "prompts": prompts,
            "config": cfg,
            "custom_title": custom_title,
            "scene_count": len(scenes)
        }

    async def wait(self, manager: Any, instance: dict[str, Any], started: dict[str, Any]) -> dict[str, Any]:
        run_jid = started.get("engine_run_id")
        cfg = started.get("config", {})
        scene_count = started.get("scene_count", 3)
        custom_title = started.get("custom_title")
        
        out_dir = ROOT / "modules" / "beauty" / "outputs" / "fashion_color_match"
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
            raise RuntimeError(f"Job 6 render thất bại cho {run_jid}: {err}")

        final_merged = out_dir / f"{run_jid}_final.mp4"
        from core.ffmpeg_utils import merge_scene_videos
        merged_file = await merge_scene_videos(raw_clips, final_merged, timeout=120)
        video_paths = [str(merged_file.resolve())]
        
        title = custom_title or cfg.get("title", "Mẹo Phối Màu Áo Ghi Lê Cổ V & Chân Váy Lụa ✨")
        caption = "Hướng dẫn phối màu thời trang sành điệu chuẩn gu 2026. Follow để nhận thêm nhiều công thức mix & match đỉnh chóp!"
        return {
            "title": title,
            "caption": caption,
            "video_paths": video_paths,
            "raw": {"jobId": run_jid, "clips": raw_clips}
        }

    @staticmethod
    def build_veo3_prompts(cfg: dict[str, Any]) -> list[dict[str, str]]:
        top_name = cfg.get("top_item", "áo ghi lê cổ V cài nút dáng ôm")
        bottom_name = cfg.get("bottom_item", "chân váy lụa suông dài")
        studio = cfg.get("studio_backdrop", "in front of an elegant modern white wardrobe closet, soft high-end studio lighting, clean fashion lookbook aesthetic, photorealistic, 8k")
        cta = cfg.get("cta_text", "Bấm theo dõi tụi mình liền nha để gom thêm cả rổ mẹo phối đồ đỉnh chóp nghen!")

        return [
            {
                "clip_id": 1,
                "pair_name": "Đỏ sang Đen",
                "start_frame_prompt": f"A full-body vertical fashion shot, 9:16 aspect ratio. Two stylish Vietnamese female fashion models standing side by side {studio}. Both wearing tailored buttoned V-neck vests in bold red. The girl on the left wears a long flowing white silk maxi skirt. The girl on the right wears a long flowing beige silk maxi skirt holding a chic mini handbag. --ar 9:16",
                "video_prompt": f"Vertical 9:16 fashion lookbook video, static full-body shot. Two stylish Vietnamese women standing {studio}. In the first 4 seconds, both wear tailored red V-neck vests. The girl on the left in a white silk skirt points down and speaks in Vietnamese: \"Nhớ kỹ nha, đỏ đi với trắng thì nhìn ổn...\". The girl on the right in a beige silk skirt places a hand on her hip, turns slightly and replies in Vietnamese: \"...nhưng đỏ mà phối với be là auto sang chảnh nha!\". At the 4-second mark, their outfits smoothly transition via quick jump-cut: both now wear tailored black V-neck vests. The girl on the left in a white silk skirt adjusts her bag strap and says in Vietnamese: \"Đen với trắng thì trông sạch sẽ...\". The girl on the right in a rich brown silk skirt takes half a step forward and says in Vietnamese: \"...còn đen mà mix với nâu nhìn vừa xịn vừa đắt tiền liền!\". Realistic lip sync, natural body gestures, professional studio lighting."
            },
            {
                "clip_id": 2,
                "pair_name": "Hồng sang Xanh Dương",
                "start_frame_prompt": f"A full-body vertical fashion shot, 9:16 aspect ratio. Two beautiful Vietnamese young women standing side by side {studio}. Both wearing tailored pastel baby pink V-neck buttoned vests. The girl on the left wears a white silk maxi skirt. The girl on the right wears a smokey gray silk maxi skirt. --ar 9:16",
                "video_prompt": f"Vertical 9:16 fashion video, static medium-full shot. Two Vietnamese women {studio}. In the first 4 seconds, both wear tailored pastel pink V-neck vests. The girl on the left in a white silk skirt does a cute gesture and speaks in Vietnamese: \"Hồng với trắng nhìn cưng xỉu...\". The girl on the right in a smokey gray silk skirt brushes her hair back elegantly and replies in Vietnamese: \"...nhưng hồng đi cùng xám nhìn mới chuẩn gu sành điệu nè!\". At 4 seconds, outfits instantly cut and switch to classic blue V-neck vests. The girl on the left in a white silk skirt smiles vibrantly and says in Vietnamese: \"Xanh dương mix trắng thì siêu tươi tắn...\". The girl on the right in a light beige silk skirt poses gracefully holding her bag and says in Vietnamese: \"...còn xanh dương đi với be là đúng chất thanh lịch luôn.\". Seamless lip sync and fluid cloth motion."
            },
            {
                "clip_id": 3,
                "pair_name": "Xanh Lá sang Đoạn Kết CTA",
                "start_frame_prompt": f"A full-body vertical fashion shot, 9:16 aspect ratio. Two chic Vietnamese female models standing side by side {studio}. Both wearing tailored olive green V-neck buttoned vests. The girl on the left wears a black silk maxi skirt. The girl on the right wears a luxury cream white silk maxi skirt. --ar 9:16",
                "video_prompt": f"Vertical 9:16 fashion video. In the first 4 seconds, two Vietnamese women wear tailored olive green V-neck vests {studio}. The girl on the left in a black silk skirt strikes a cool confident pose and says in Vietnamese: \"Xanh lá với đen nhìn chất ngầu, cá tính...\". The girl on the right in a cream white silk skirt takes a graceful step forward with a glowing smile and says in Vietnamese: \"...nhưng xanh lá mà đi với màu kem thì visual sang miễn bàn!\". At the 4-second mark, outfits smoothly switch back to vibrant red V-neck vests and matching silk skirts. Both girls look straight into the camera with wide radiant smiles. The girl on the left says in Vietnamese: \"Bấm theo dõi tụi mình liền nha...\". The girl on the right finishes in Vietnamese: \"{cta}\" as both girls warmly wave goodbye to the camera. Perfect lip sync, crisp speech."
            }
        ]
