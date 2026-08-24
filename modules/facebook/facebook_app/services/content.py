from __future__ import annotations

import json
import random
import re
from typing import Any

from facebook_app.services.llm_router import llm_router

CELEBRITIES: dict[str, dict[str, Any]] = {
    "Warren Buffett": {
        "id": "warren_buffett",
        "query": "Warren Buffett interview CNBC speaking conversation -Munger -documentary -shorts -motivation",
        "yt": "Warren Buffett interview CNBC speaking conversation -Munger",
        "exclude": ["charles munger", "charlie munger", "becoming warren buffett", "documentary", "reaction", "motivation"],
    },
    "Jack Ma": {
        "id": "jack_ma",
        "query": "Jack Ma interview speaking conference close up -motivation -compilation",
        "yt": "Jack Ma interview speaking conference",
        "exclude": ["motivation compilation", "reaction"],
    },
    "Steve Jobs": {
        "id": "steve_jobs",
        "query": "Steve Jobs interview speaking conference close up -documentary -compilation",
        "yt": "Steve Jobs interview speaking",
        "exclude": ["documentary", "compilation", "reaction"],
    },
    "Charlie Munger": {
        "id": "charlie_munger",
        "query": "Charlie Munger interview speaking close up -Buffett documentary",
        "yt": "Charlie Munger interview speaking",
        "exclude": ["documentary", "reaction"],
    },
    "Elon Musk": {
        "id": "elon_musk",
        "query": "Elon Musk interview speaking conference close up -shorts -compilation",
        "yt": "Elon Musk interview speaking conference",
        "exclude": ["shorts", "compilation", "reaction"],
    },
    "Bill Gates": {
        "id": "bill_gates",
        "query": "Bill Gates interview speaking conference close up -documentary -compilation",
        "yt": "Bill Gates interview speaking",
        "exclude": ["documentary", "compilation", "reaction"],
    },
    "Jackie Chan": {
        "id": "jackie_chan",
        "query": "Jackie Chan interview speaking close up -movie -trailer -compilation",
        "yt": "Jackie Chan interview speaking",
        "exclude": ["movie", "trailer", "compilation", "reaction"],
    },
}

FALLBACKS = [
    {
        "topic": "Đừng cố thay đổi người không muốn thay đổi",
        "segments": [
            {"zh": "不要浪费时间，试图去改变一个根本不想改变的人。", "vi": "Đừng lãng phí thời gian cố gắng thay đổi một người không muốn thay đổi."},
            {"zh": "真正的改变，只会发生在一个人自己愿意改变的时候。", "vi": "Sự thay đổi thật sự chỉ xảy ra khi chính họ muốn thay đổi."},
            {"zh": "你越想控制别人，就越容易失去自己的平静。", "vi": "Càng cố kiểm soát người khác, bạn càng dễ đánh mất sự bình yên của mình."},
            {"zh": "把精力收回来，用在自己身上，人生才会真正向前。", "vi": "Hãy thu năng lượng về cho chính mình, lúc đó cuộc đời mới thật sự tiến lên."},
        ],
    },
    {
        "topic": "Trưởng thành là bớt giải thích",
        "segments": [
            {"zh": "人成熟以后，会越来越少向别人解释自己。", "vi": "Khi trưởng thành, con người sẽ ngày càng ít giải thích bản thân với người khác."},
            {"zh": "理解你的人，不需要太多解释。", "vi": "Người hiểu bạn không cần quá nhiều lời giải thích."},
            {"zh": "不理解你的人，你解释得再多也没有用。", "vi": "Người không hiểu bạn, dù bạn giải thích bao nhiêu cũng không có tác dụng."},
            {"zh": "把时间留给重要的人，把安静留给自己。", "vi": "Hãy dành thời gian cho người quan trọng và giữ sự bình yên cho chính mình."},
        ],
    },
    {
        "topic": "Tiền mua được nhiều thứ nhưng không mua lại thời gian",
        "segments": [
            {"zh": "钱可以买到很多东西，但买不回已经失去的时间。", "vi": "Tiền có thể mua nhiều thứ, nhưng không mua lại được thời gian đã mất."},
            {"zh": "年轻时我们用时间换钱，后来才发现时间更贵。", "vi": "Khi trẻ ta dùng thời gian đổi lấy tiền, rồi sau đó mới nhận ra thời gian còn đắt hơn."},
            {"zh": "真正富有的人，懂得保护自己的时间。", "vi": "Người thật sự giàu có biết bảo vệ thời gian của mình."},
            {"zh": "不要把最宝贵的东西，浪费在不值得的事情上。", "vi": "Đừng lãng phí thứ quý giá nhất của bạn vào những việc không xứng đáng."},
        ],
    },
    {
        "topic": "Đừng kể mọi kế hoạch của bạn",
        "segments": [
            {"zh": "不要把你的每一个计划都告诉所有人。", "vi": "Đừng kể mọi kế hoạch của bạn cho tất cả mọi người."},
            {"zh": "真正重要的事情，往往是在安静中完成的。", "vi": "Những việc thật sự quan trọng thường được hoàn thành trong im lặng."},
            {"zh": "说得太早，会消耗你行动的力量。", "vi": "Nói quá sớm có thể làm tiêu hao sức mạnh hành động của bạn."},
            {"zh": "先做出结果，再让结果替你说话。", "vi": "Hãy tạo ra kết quả trước, rồi để kết quả lên tiếng thay bạn."},
        ],
    },
]


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-]+", "-", s.strip()).strip("-")
    return s[:80] or "video"


def _choose_celebrity(page: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    pool = page.get("celebrity_pool") or []
    if isinstance(pool, str):
        try:
            pool = json.loads(pool)
        except Exception:
            pool = []
    valid = [name for name in pool if name in CELEBRITIES]
    name = random.choice(valid or list(CELEBRITIES.keys()))
    return name, CELEBRITIES[name]


def _base_script(page: dict[str, Any], item: dict[str, Any], celebrity_name: str, celeb: dict[str, Any]) -> dict[str, Any]:
    mode = page.get("output_mode") or "reel_9_16"
    width, height = (1080, 1920) if mode == "reel_9_16" else (1080, 1080)
    topic = item["topic"]
    return {
        "title": _slug(topic),
        "output": {"width": width, "height": height},
        "voice": {
            "language": "zh-CN",
            "edge_voice": "zh-CN-YunxiNeural",
            "fallback_voices": ["zh-CN-YunyangNeural", "zh-CN-XiaoxiaoNeural"],
            "rate": "+0%",
        },
        "subtitle_language": "vi-VN",
        "narration_segments": item["segments"],
        "style": {
            "mode": "reference",
            "broll_clip_count": 2,
            "motion_min_score": 2.0,
            "motion_attempts": 6,
            "broll_queries": item.get("broll_queries") or [
                "city night aerial drone traffic cinematic moving lights",
                "modern city night aerial drone traffic timelapse cinematic",
            ],
        },
        "celebrity": {
            "enabled": True,
            "required": True,
            "name": celebrity_name,
            "source_mode": "search",
            "folder": f"input/celebrity_verified_v26/{celeb['id']}",
            "search_provider": "google_serper",
            "search_query": celeb["query"],
            "ytsearch_query": celeb["yt"],
            "search_results": 15,
            "search_download_count": 4,
            "refresh_search": False,
            "min_source_duration": 20,
            "max_source_duration": 7200,
            "duration_min": 4.0,
            "duration_max": 5.0,
            "start_padding": 20.0,
            "avoid_recent": 80,
            "audio_mode": "visual_only",
            "mute_original_audio": True,
            "start_strategy": "early",
            "start_window_max": 600,
            "crop_bottom_fraction": 0.22,
            "require_name_in_title": True,
            "name_position_max": 45,
            "exclude_title_keywords": celeb["exclude"],
            "face_verify": True,
            "face_min_hits": 3,
            "face_min_score": 35.0,
            "face_scan_start_min": 25,
            "face_scan_end_max": 900,
            "face_scan_max_windows": 30,
        },
        "scenes": [{"text": "fallback", "query": "city night aerial drone traffic"}],
        "factory_meta": {
            "topic": topic,
            "page_theme": page.get("theme", "life"),
            "caption_vi": item.get("caption_vi") or topic,
        },
    }


def _generate_via_llm(page: dict[str, Any], celebrity_name: str, model_override: str | None = None, v28_config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not llm_router.configured():
        return None
    prompt = f"""Bạn là biên tập viên video Facebook ngắn. Hãy tạo MỘT nội dung mới cho page chủ đề '{page.get('theme','life')}'.
Định dạng kênh: voice tiếng Trung phổ thông, subtitle tiếng Việt. Nhân vật hình ảnh mở đầu: {celebrity_name}. Không được viết như thể nhân vật thật đã nói câu đó; đây là lời dẫn/triết lý tổng hợp.
Trả về JSON thuần, không markdown, đúng schema:
{{"topic":"...","caption_vi":"...","broll_queries":["English query 1","English query 2"],"segments":[{{"zh":"...","vi":"..."}},{{"zh":"...","vi":"..."}},{{"zh":"...","vi":"..."}},{{"zh":"...","vi":"..."}}]}}
Yêu cầu: 4 đoạn; tổng voice khoảng 18-28 giây; tiếng Trung tự nhiên; phụ đề Việt súc tích; không gán câu nói giả cho người nổi tiếng; tránh lặp ý sáo rỗng."""
    extra = str((v28_config or {}).get("script_prompt") or "").strip()
    if extra:
        prompt += "\n\nYÊU CẦU RIÊNG CỦA JOB V2.8:\n" + extra
    data, meta = llm_router.chat_json(
        [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        model_override,
        temperature=0.9,
    )
    if not isinstance(data.get("segments"), list) or len(data["segments"]) < 3:
        raise ValueError("LLM JSON thiếu segments")
    data["_llm_meta"] = meta
    return data


def build_script(page: dict[str, Any], model_override: str | None = None, v28_config: dict[str, Any] | None = None) -> dict[str, Any]:
    celebrity_name, celeb = _choose_celebrity(page)
    item = None
    try:
        item = _generate_via_llm(page, celebrity_name, model_override, v28_config)
    except Exception:
        item = None
    if not item:
        item = random.choice(FALLBACKS)
    script = _base_script(page, item, celebrity_name, celeb)
    llm_meta = item.get("_llm_meta") if isinstance(item, dict) else None
    if llm_meta:
        script.setdefault("factory_meta", {})["llm_model"] = llm_meta.get("model", "")
        script["factory_meta"]["llm_latency_ms"] = llm_meta.get("latency_ms", 0)
    cfg = dict(v28_config or {})
    if cfg:
        # Per Job content settings. Execution/Facebook remain owned by V2.8 core.
        if cfg.get("broll_queries"):
            script.setdefault("style", {})["broll_queries"] = [str(x).strip() for x in cfg.get("broll_queries") or [] if str(x).strip()]
        for src_key, dst_key in (("broll_clip_count","broll_clip_count"),("motion_min_score","motion_min_score"),("motion_attempts","motion_attempts")):
            if cfg.get(src_key) is not None: script.setdefault("style", {})[dst_key] = cfg[src_key]
        celeb = script.setdefault("celebrity", {})
        if cfg.get("search_query_template"):
            celeb["search_query"] = str(cfg["search_query_template"]).replace("{celebrity}", celebrity_name)
        for k in ("audio_mode","mute_original_audio","face_verify","face_min_hits","face_min_score"):
            if cfg.get(k) is not None: celeb[k] = cfg[k]
        if cfg.get("hook_duration_min") is not None: celeb["duration_min"] = float(cfg["hook_duration_min"])
        if cfg.get("hook_duration_max") is not None: celeb["duration_max"] = float(cfg["hook_duration_max"])
        voice = script.setdefault("voice", {})
        if cfg.get("voice_language"): voice["language"] = cfg["voice_language"]
        if cfg.get("edge_voice"): voice["edge_voice"] = cfg["edge_voice"]
        if cfg.get("tts_rate"): voice["rate"] = cfg["tts_rate"]
        if cfg.get("subtitle_language"): script["subtitle_language"] = cfg["subtitle_language"]
        script["subtitle_style"] = {
            "font": cfg.get("subtitle_font") or "Arial",
            "font_size": int(cfg.get("subtitle_font_size") or 58),
            "max_words": int(cfg.get("subtitle_max_words") or 7),
            "max_chars": int(cfg.get("subtitle_max_chars") or 38),
        }
        script.setdefault("factory_meta", {})["v28_config_applied"] = True
    return script
