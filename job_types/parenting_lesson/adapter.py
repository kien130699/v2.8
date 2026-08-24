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

PARENTING_TOPICS = [
    "Bé lười ăn rau và màn đối đáp siêu bất ngờ với mẹ",
    "Bé sợ bóng tối và phát hiện bí mật căn phòng ngủ",
    "Bé không chịu chia sẻ đồ chơi với bạn và bài học chia sẻ niềm vui",
    "Bé đòi mua đồ chơi ở siêu thị và tuyệt chiêu đàm phán của mẹ",
    "Bé tò mò tại sao người lớn phải đi làm mỗi ngày"
]

class Adapter:
    def _generate_dynamic_script(self, cfg: dict[str, Any]) -> dict[str, Any]:
        router_base = os.getenv("9ROUTER_BASE_URL") or os.getenv("ROUTER9_BASE_URL") or "http://127.0.0.1:20128/v1"
        router_key = os.getenv("9ROUTER_API_KEY") or os.getenv("ROUTER9_API_KEY")
        topic = cfg.get("topic") or random.choice(PARENTING_TOPICS)
        
        sys_prompt = """Bạn là chuyên gia biên kịch hoạt hình 3D Pixar/Disney hàng đầu cho kênh Mẹ & Bé (Phong cách hiện đại, lém lỉnh, đời thường, hài hước, dạy con thông minh không giáo điều).
Nhiệm vụ: Viết kịch bản video ngắn Facebook Reels / TikTok (32 giây - gồm 4 scenes x 8 giây) cho TEMPLATE 5: DẠY CON BÀI HỌC / QUY TẮC / TÌNH HUỐNG THỰC TẾ.

Yêu cầu nội dung:
- Chủ đề: Tình huống đời thường thực tế, bé lém lỉnh đối đáp thông minh, mẹ xử lý tinh tế đầy bất ngờ.
- Nhân vật: Mẹ (30 tuổi dịu dàng thông thái) & Bé (3-4 tuổi lém lỉnh, biểu cảm sống động).
- 4 scenes (mỗi scene 8s, total 32s):
  Scene 1 (0-8s): Tình huống bất ngờ / Bé đưa ra câu hỏi hoặc hành động bẻ lái.
  Scene 2 (8-16s): Mẹ tương tác bằng mẹo thực tế / thử thách hài hước.
  Scene 3 (16-24s): Bé tự nhận ra bài học và thực hành hào hứng.
  Scene 4 (24-32s): Kết thúc ấm áp, đập tay, nụ cười hạnh phúc + bài học đúc kết ngắn.

Trả về DUY NHẤT một JSON hợp lệ:
{
  "title": "Tiêu đề video cực viral kèm emoji",
  "topic": "Chủ đề tình huống",
  "lesson": "Bài học đúc kết ngắn gọn",
  "scenes": [
    {
      "scene_id": 1,
      "summary": "Tóm tắt cảnh 1",
      "image_prompt": "3D Pixar animation style, warm cinematic lighting. A gentle 30-year-old Asian mother and a cute 4-year-old little girl sitting in a cozy modern living room...",
      "video_prompt": "3D Pixar style family animation. 4-year-old cute girl asks a question while mother smiles and gently explains. Realistic natural body gestures, studio lighting."
    },
    {
      "scene_id": 2,
      "summary": "Tóm tắt cảnh 2",
      "image_prompt": "3D Pixar animation style, warm cinematic lighting. Mother showing a clever trick...",
      "video_prompt": "3D Pixar style animation. Mother interacting with girl, playful expressions, warm cinematic atmosphere."
    },
    {
      "scene_id": 3,
      "summary": "Tóm tắt cảnh 3",
      "image_prompt": "3D Pixar animation style, warm cinematic lighting. Girl trying happily...",
      "video_prompt": "3D Pixar style animation. Girl laughing and accomplishing the task, mother clapping happily."
    },
    {
      "scene_id": 4,
      "summary": "Tóm tắt cảnh 4",
      "image_prompt": "3D Pixar animation style, warm cinematic lighting. Mother and daughter high-five with big smiles...",
      "video_prompt": "3D Pixar style animation. Heartwarming moment, high-five and hug, glowing smile to camera."
    }
  ]
}"""
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
                            {"role": "user", "content": f"Hãy viết kịch bản 4 scenes (32s) cho chủ đề: '{topic}'."}
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
                            if data.get("scenes") and len(data["scenes"]) >= 4:
                                return data
                except Exception:
                    continue
                    
        # Fallback pool
        return {
            "title": f"Mẹo Dạy Con Thông Minh: {topic} ✨",
            "topic": topic,
            "lesson": "Lắng nghe và biến thử thách thành trò chơi vui cùng bé.",
            "scenes": [
                {
                    "scene_id": 1,
                    "summary": "Tình huống mở đầu",
                    "image_prompt": "A young Asian mother around 30 years old and a 4-year-old cute Asian girl in a cozy living room, premium 3D family animation film style, warm cinematic lighting. --ar 9:16",
                    "video_prompt": "3D animation video. In cozy room, cute little girl looks up and asks question. Mother listens with warm smile."
                },
                {
                    "scene_id": 2,
                    "summary": "Mẹ gợi ý giải pháp",
                    "image_prompt": "Mother gently demonstrating a fun interactive method to the little girl, 3D Pixar style, warm cinematic lighting. --ar 9:16",
                    "video_prompt": "3D animation. Mother points playfully and explains gently, girl looks interested with curiosity."
                },
                {
                    "scene_id": 3,
                    "summary": "Bé hào hứng thực hành",
                    "image_prompt": "Little girl happily trying the new routine, bright facial expression, 3D Pixar style. --ar 9:16",
                    "video_prompt": "3D animation. Girl smiling brightly and clapping hands, celebrating success."
                },
                {
                    "scene_id": 4,
                    "summary": "Đoạn kết gắn kết gia đình",
                    "image_prompt": "Mother and daughter high five with radiant smiles, heartwarming family moment, 3D Pixar style. --ar 9:16",
                    "video_prompt": "3D animation. Mother and daughter hugging warmly and waving to camera with joyful smiles."
                }
            ]
        }

    async def prepare(self, manager: Any, instance: dict[str, Any]) -> str:
        return await manager.engine.ensure_parenting_ready(instance["config"], instance["id"], instance["name"])

    async def start(self, manager: Any, instance: dict[str, Any], resume_job_id: str | None = None) -> dict[str, Any]:
        ref = await self.prepare(manager, instance)
        manager.set_engine_ref(instance["id"], ref)
        
        cfg = instance.get("config", {})
        script_data = await asyncio.to_thread(self._generate_dynamic_script, cfg)
        
        scenes = []
        for idx, sc in enumerate(script_data.get("scenes", []), 1):
            scenes.append({
                "index": idx,
                "serverSceneIndex": idx,
                "imagePrompt": sc.get("image_prompt") or sc.get("start_frame_image_prompt") or sc.get("visual_prompt", ""),
                "videoPrompt": sc.get("video_prompt") or sc.get("veo3_video_prompt", ""),
                "videoDuration": "8s",
                "aspectRatio": "9:16"
            })
            
        run_uid = uuid.uuid4().hex[:8]
        run_jid = str(resume_job_id).strip() if resume_job_id else f"flow_job5_{run_uid}"
            
        task_msg = {
            "type": "RUN_FLOW_JOB",
            "jobId": run_jid,
            "resume": bool(resume_job_id),
            "checkpoints": db.get_scene_checkpoints(run_jid) if resume_job_id else [],
            "kind": "parenting_lesson",
            "source": "parenting",
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
            manager.flow_broker.enqueue("parenting", task_msg)
            
        return {
            "engine_run_id": run_jid,
            "engine_job_ids": [run_jid],
            "config": cfg,
            "script_data": script_data,
            "title": script_data.get("title"),
            "scene_count": len(scenes)
        }

    async def wait(self, manager: Any, instance: dict[str, Any], started: dict[str, Any]) -> dict[str, Any]:
        run_jid = started.get("engine_run_id")
        scene_count = started.get("scene_count", 4)
        title = started.get("title") or "Mẹo Dạy Con Thông Minh Mỗi Ngày ✨"
        
        out_dir = ROOT / "modules" / "parenting" / "outputs" / "parenting_lesson"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        broker = manager.flow_broker if hasattr(manager, "flow_broker") else None
        if not broker:
            raise RuntimeError("FlowBroker is not initialized in manager")

        # Event-driven wait (pure async, no filesystem polling)
        res = await broker.wait_job(run_jid, timeout=600, expected_scenes=scene_count)
        raw_clips = res.get("video_paths") or []
        if not raw_clips:
            raw_clips = broker.get_job_clips(run_jid)
            
        if not raw_clips:
            err = res.get("error") or "Không nhận được clip nào từ Flow Worker"
            raise RuntimeError(f"Job 5 render thất bại cho {run_jid}: {err}")

        final_merged = out_dir / f"{run_jid}_final.mp4"
        from core.ffmpeg_utils import merge_scene_videos
        merged_file = await merge_scene_videos(raw_clips, final_merged, timeout=120)
        video_paths = [str(merged_file.resolve())]
        
        caption = "Những bài học đời thường bổ ích và mẹo nuôi dạy con thông minh, hạnh phúc. Follow kênh để nhận thêm nhiều video hay mỗi ngày!"
        return {
            "title": title,
            "caption": caption,
            "video_paths": video_paths,
            "raw": {"jobId": run_jid, "clips": raw_clips}
        }
