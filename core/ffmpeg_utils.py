from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence


import uuid
from typing import Any


def validate_video_clip(clip: str | Path) -> dict[str, Any]:
    clip_p = Path(clip).resolve()
    if not clip_p.exists() or clip_p.stat().st_size < 1024:
        return {"valid": False, "error": f"File missing or too small ({clip_p.stat().st_size if clip_p.exists() else 0} bytes)"}
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size:stream=width,height,r_frame_rate,codec_name",
            "-of", "json",
            str(clip_p)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, text=True)
        if res.returncode != 0:
            return {"valid": True, "warning": f"ffprobe code {res.returncode}"}
        import json
        info = json.loads(res.stdout or "{}")
        duration = float(info.get("format", {}).get("duration", 0) or 0)
        streams = info.get("streams", [])
        v_stream = next((s for s in streams if s.get("width")), None)
        if not v_stream or duration <= 0:
            return {"valid": False, "error": f"Invalid video stream or duration <= 0 (duration={duration})"}
        return {
            "valid": True,
            "duration": duration,
            "width": v_stream.get("width"),
            "height": v_stream.get("height"),
            "codec": v_stream.get("codec_name"),
            "fps": v_stream.get("r_frame_rate")
        }
    except Exception as exc:
        return {"valid": True, "warning": str(exc)}


def _sync_merge_ffmpeg(clips: list[Path], out_file: Path, timeout: float = 120) -> Path:
    out_file = Path(out_file).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if not clips:
        raise ValueError("No video clips provided for merge")

    temp_out = out_file.parent / f"{out_file.stem}_tmp_{uuid.uuid4().hex[:6]}.mp4"
    concat_txt = out_file.parent / f"{out_file.stem}_concat_{uuid.uuid4().hex[:6]}.txt"

    try:
        if len(clips) == 1:
            # Single clip: fast copy
            shutil.copy2(clips[0], temp_out)
            if temp_out.exists() and temp_out.stat().st_size > 1024:
                shutil.move(str(temp_out), str(out_file))
                return out_file

        concat_txt.write_text("\n".join(f"file '{c.resolve().as_posix()}'" for c in clips) + "\n", encoding="utf-8")

        # Step 1: Try stream copy concat (fastest, lossless)
        copy_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt),
            "-c", "copy",
            str(temp_out)
        ]

        try:
            res = subprocess.run(
                copy_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False
            )
            if res.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 1024:
                shutil.move(str(temp_out), str(out_file))
                return out_file
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"FFmpeg copy merge timed out after {timeout}s")
        except Exception:
            pass

        # Clean temp before fallback attempt
        if temp_out.exists():
            try:
                temp_out.unlink()
            except Exception:
                pass

        # Step 2: Fallback re-encode concat (handles mismatched framerates, codecs, timebases)
        reencode_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(temp_out)
        ]

        try:
            res = subprocess.run(
                reencode_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False
            )
            if res.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 1024:
                shutil.move(str(temp_out), str(out_file))
                return out_file
            err_msg = res.stderr.decode("utf-8", errors="replace")[-500:] if res.stderr else "Unknown error"
            raise RuntimeError(f"FFmpeg re-encode merge failed (code {res.returncode}): {err_msg}")
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"FFmpeg re-encode merge timed out after {timeout}s")
    finally:
        # Cleanup temporary files
        if temp_out.exists():
            try:
                temp_out.unlink()
            except Exception:
                pass
        if concat_txt.exists():
            try:
                concat_txt.unlink()
            except Exception:
                pass


async def merge_scene_videos(video_paths: Sequence[str | Path], output_path: str | Path, timeout: float = 120) -> Path:
    clips = []
    for p in video_paths:
        p_obj = Path(p).resolve()
        val = validate_video_clip(p_obj)
        if val.get("valid", True) and p_obj.exists() and p_obj.is_file() and p_obj.stat().st_size > 1024:
            clips.append(p_obj)

    if not clips:
        raise FileNotFoundError(f"None of the provided {len(video_paths)} clips exist on disk")

    out_file = Path(output_path).resolve()
    return await asyncio.to_thread(_sync_merge_ffmpeg, clips, out_file, timeout)
