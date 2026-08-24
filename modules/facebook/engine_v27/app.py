from __future__ import annotations

import hashlib
import html
import json
import os
import random
import shutil
import time
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlparse
from pathlib import Path
from typing import Any

# Windows may inherit an ANSI console encoding (cp1252/charmap).  Force UTF-8 for
# engine logs so Vietnamese exception/status text cannot crash the render process.
def _configure_utf8_stdio() -> None:
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        if _stream is None or not hasattr(_stream, "reconfigure"):
            continue
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

_configure_utf8_stdio()

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
WORK_DIR = ROOT / "work"
DATA_DIR = ROOT / "data"
SCRIPT_FILE = ROOT / "script.json"
ENV_FILE = ROOT / ".env"
CELEBRITY_HISTORY_FILE = DATA_DIR / "celebrity_history.json"

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_VIDEO_SEARCH = "https://api.pexels.com/v1/videos/search"
PIXABAY_VIDEO_SEARCH = "https://pixabay.com/api/videos/"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

_FACE_DETECTOR_CACHE = None
_YUNET_MODEL_ATTEMPTED = False
_YUNET_MODEL_CACHE: Path | None = None


def log(msg: str) -> None:
    print(f"[BROLL V2.6] {msg}", flush=True)


def run(cmd: list[str]) -> None:
    log("$ " + " ".join(str(x) for x in cmd))
    subprocess.run(cmd, check=True, cwd=ROOT, timeout=300)


def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(
            f"KhÃ´ng tÃ¬m tháº¥y {name}. HÃ£y cÃ i FFmpeg vÃ  thÃªm thÆ° má»¥c bin vÃ o PATH."
        )


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    return float(result.stdout.strip())


def ffprobe_has_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def download_file(url: str, dest: Path) -> None:
    """Robust HTTP downloader for stock footage.

    Handles transient TLS/SSL failures seen on large Pexels files by retrying,
    resuming partial downloads with Range when supported, and finally falling
    back to the system curl executable on Windows.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    headers_base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
    last_error: Exception | None = None

    for attempt in range(1, 5):
        try:
            existing = part.stat().st_size if part.exists() else 0
            headers = dict(headers_base)
            mode = "wb"
            if existing > 0:
                headers["Range"] = f"bytes={existing}-"
                mode = "ab"

            log(f"    HTTP download láº§n {attempt}/4" + (f" | resume={existing/1024/1024:.1f}MB" if existing else ""))
            with requests.get(
                url, headers=headers, stream=True,
                timeout=(15, 120), allow_redirects=True,
            ) as r:
                r.raise_for_status()

                # Server ignored Range -> restart from zero instead of corrupt append.
                if existing > 0 and r.status_code != 206:
                    existing = 0
                    mode = "wb"

                expected = int(r.headers.get("Content-Length") or 0)
                wrote = 0
                with part.open(mode) as f:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        wrote += len(chunk)

                # If Content-Length is available, reject obviously truncated body.
                if expected and wrote < expected:
                    raise IOError(f"download thiáº¿u dá»¯ liá»‡u: {wrote}/{expected} bytes")

            part.replace(dest)
            return
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            log(f"    táº£i lá»—i: {type(exc).__name__}: {exc}")
            time.sleep(min(8, attempt * 2))

    # Windows ships curl.exe by default; it is often more tolerant of flaky TLS.
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        log("    requests váº«n lá»—i -> fallback curl --retry-all-errors")
        cmd = [
            curl, "-L", "--fail", "--silent", "--show-error",
            "--retry", "5", "--retry-delay", "2", "--retry-all-errors",
            "--connect-timeout", "20", "--max-time", "600",
            "-o", str(part), url,
        ]
        result = subprocess.run(cmd, cwd=ROOT, timeout=300)
        if result.returncode == 0 and part.exists() and part.stat().st_size > 1024:
            part.replace(dest)
            return

    try:
        part.unlink(missing_ok=True)
    except Exception:
        pass
    raise RuntimeError(f"Táº£i video tháº¥t báº¡i sau retry/fallback: {last_error}")



def choose_pexels_file(
    video: dict[str, Any],
    target_aspect: float = 1.0,
) -> dict[str, Any] | None:
    files = [
        f
        for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("link")
    ]
    if not files:
        return None

    def score(f: dict[str, Any]) -> tuple[float, float]:
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        if not w or not h:
            return (-9999.0, -9999.0)
        aspect = w / h
        short_side = min(w, h)
        long_side = max(w, h)

        # Output is only 1080-ish. Prefer 720p-1080p source over unnecessary 4K.
        quality = 3.0 if short_side >= 720 else (1.0 if short_side >= 540 else -2.0)
        aspect_score = -abs(aspect - target_aspect) * 8.0
        oversize_penalty = max(0.0, (long_side - 1920) / 800.0)
        target_pixels = 1920 * 1080
        pixel_distance = -abs((w * h) - target_pixels) / target_pixels
        return (quality + aspect_score - oversize_penalty, pixel_distance)

    return max(files, key=score)


def search_pexels(
    query: str,
    api_key: str,
    used_ids: set[str],
    orientation: str | None = None,
    target_aspect: float = 1.0,
) -> dict[str, Any] | None:
    params: dict[str, Any] = {
        "query": query,
        "size": "medium",
        "locale": "en-US",
        "per_page": 30,
    }
    if orientation:
        params["orientation"] = orientation

    r = requests.get(
        PEXELS_VIDEO_SEARCH,
        headers={"Authorization": api_key},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    videos = r.json().get("videos", [])
    random.shuffle(videos)

    candidates: list[tuple[tuple[float, int], dict[str, Any], dict[str, Any]]] = []
    for video in videos:
        vid = f"pexels:{video.get('id')}"
        if vid in used_ids:
            continue
        f = choose_pexels_file(video, target_aspect=target_aspect)
        if not f:
            continue
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        aspect = w / h if h else 99.0
        score = (-abs(aspect - target_aspect), w * h)
        candidates.append((score, video, f))

    if not candidates:
        return None

    # Pick from top candidates, not always the exact same first result.
    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[: min(8, len(candidates))]
    _, video, f = random.choice(top)
    return {
        "provider": "pexels",
        "id": f"pexels:{video['id']}",
        "download_url": f["link"],
        "source_page": video.get("url", ""),
        "creator": (video.get("user") or {}).get("name", ""),
        "width": f.get("width"),
        "height": f.get("height"),
        "duration": video.get("duration"),
    }


def search_pixabay(
    query: str,
    api_key: str,
    used_ids: set[str],
    target_aspect: float = 1.0,
) -> dict[str, Any] | None:
    if not api_key:
        return None
    params = {
        "key": api_key,
        "q": query,
        "lang": "en",
        "video_type": "film",
        "safesearch": "true",
        "order": "popular",
        "per_page": 30,
    }
    r = requests.get(PIXABAY_VIDEO_SEARCH, params=params, timeout=30)
    r.raise_for_status()
    hits = r.json().get("hits", [])
    random.shuffle(hits)

    candidates: list[tuple[tuple[float, int], dict[str, Any], dict[str, Any]]] = []
    for hit in hits:
        vid = f"pixabay:{hit.get('id')}"
        if vid in used_ids:
            continue
        variants = hit.get("videos") or {}
        files = [v for v in variants.values() if isinstance(v, dict) and v.get("url")]
        if not files:
            continue

        def file_score(f: dict[str, Any]) -> tuple[float, int]:
            w = int(f.get("width") or 0)
            h = int(f.get("height") or 0)
            if not w or not h:
                return (-9999.0, 0)
            aspect = w / h
            return (-abs(aspect - target_aspect), w * h)

        f = max(files, key=file_score)
        candidates.append((file_score(f), hit, f))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[: min(8, len(candidates))]
    _, hit, f = random.choice(top)
    return {
        "provider": "pixabay",
        "id": f"pixabay:{hit['id']}",
        "download_url": f["url"],
        "source_page": hit.get("pageURL", ""),
        "creator": hit.get("user", ""),
        "width": f.get("width"),
        "height": f.get("height"),
        "duration": hit.get("duration"),
    }


def get_stock_video(
    query: str,
    pexels_key: str,
    pixabay_key: str,
    used_ids: set[str],
    orientation: str | None = None,
    target_aspect: float = 1.0,
) -> dict[str, Any]:
    errors: list[str] = []

    if pexels_key:
        try:
            item = search_pexels(
                query, pexels_key, used_ids,
                orientation=orientation,
                target_aspect=target_aspect,
            )
            if item:
                return item
        except Exception as e:
            errors.append(f"Pexels: {e}")

    if pixabay_key:
        try:
            item = search_pixabay(
                query, pixabay_key, used_ids,
                target_aspect=target_aspect,
            )
            if item:
                return item
        except Exception as e:
            errors.append(f"Pixabay: {e}")

    raise RuntimeError(
        f"KhÃ´ng tÃ¬m Ä‘Æ°á»£c B-roll cho query={query!r}. " + " | ".join(errors)
    )


def make_approx_srt(text: str, duration: float, srt: Path, max_words: int = 6) -> None:
    words = text.split()
    if not words:
        raise RuntimeError("Narration rá»—ng, khÃ´ng thá»ƒ táº¡o subtitle.")

    groups = [words[i : i + max_words] for i in range(0, len(words), max_words)]
    weights = [max(1, len(" ".join(g))) for g in groups]
    total_weight = sum(weights)

    def fmt(sec: float) -> str:
        sec = max(0.0, sec)
        hh = int(sec // 3600)
        mm = int((sec % 3600) // 60)
        ss = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        if ms >= 1000:
            ss += 1
            ms = 0
        return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

    out: list[str] = []
    t = 0.0
    for idx, (group, weight) in enumerate(zip(groups, weights), start=1):
        d = duration * weight / total_weight
        start = t
        end = min(duration, t + d)
        out.extend([str(idx), f"{fmt(start)} --> {fmt(end)}", " ".join(group), ""])
        t = end
    srt.write_text("\n".join(out), encoding="utf-8")


def try_edge_tts_once(text: str, voice: str, rate: str, mp3: Path, srt: Path) -> tuple[bool, str]:
    text_file = WORK_DIR / "tts_input.txt"
    text_file.write_text(text, encoding="utf-8")
    for f in (mp3, srt):
        if f.exists():
            f.unlink()

    cmd = [
        sys.executable,
        "-m",
        "edge_tts",
        f"--voice={voice}",
        f"--rate={rate}",
        "--file",
        str(text_file),
        "--write-media",
        str(mp3),
        "--write-subtitles",
        str(srt),
    ]
    log("$ " + " ".join(str(x) for x in cmd))
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=120)
    ok = (
        result.returncode == 0
        and mp3.exists()
        and mp3.stat().st_size > 1000
        and srt.exists()
        and srt.stat().st_size > 10
    )
    detail = (result.stderr or result.stdout or "").strip()
    return ok, detail


def make_tts(
    text: str,
    voice: str,
    rate: str,
    mp3: Path,
    srt: Path,
    mode: str = "auto",
    retries: int = 3,
    retry_delay: float = 3.0,
    fallback_voices: list[str] | None = None,
) -> str:
    mode = (mode or "auto").strip().lower()
    manual_voice = INPUT_DIR / "voice.mp3"

    if mode == "manual":
        if not manual_voice.exists():
            raise RuntimeError("TTS_MODE=manual nhÆ°ng thiáº¿u input/voice.mp3")
        shutil.copy2(manual_voice, mp3)
        duration = ffprobe_duration(mp3)
        make_approx_srt(text, duration, srt)
        log(f"DÃ¹ng voice thá»§ cÃ´ng: {manual_voice} ({duration:.2f}s)")
        return "manual"

    voices: list[str] = []
    for v in [voice, *(fallback_voices or [])]:
        v = v.strip()
        if v and v not in voices:
            voices.append(v)

    last_error = ""
    for v in voices:
        for attempt in range(1, max(1, retries) + 1):
            log(f"Edge TTS voice={v} | láº§n {attempt}/{max(1, retries)}")
            ok, detail = try_edge_tts_once(text, v, rate, mp3, srt)
            if ok:
                log(f"Edge TTS OK vá»›i voice={v}")
                return "edge"
            last_error = detail or "NoAudioReceived / khÃ´ng nháº­n Ä‘Æ°á»£c audio"
            tail = last_error.splitlines()[-1] if last_error else "unknown error"
            log(f"Edge TTS tháº¥t báº¡i: {tail}")
            if attempt < max(1, retries):
                time.sleep(max(0.0, retry_delay))

    if mode == "auto" and manual_voice.exists():
        log("Edge TTS lá»—i -> fallback sang input/voice.mp3")
        shutil.copy2(manual_voice, mp3)
        duration = ffprobe_duration(mp3)
        make_approx_srt(text, duration, srt)
        return "manual"

    raise RuntimeError(
        "Edge TTS khÃ´ng tráº£ audio sau nhiá»u láº§n thá»­.\n"
        f"Lá»—i cuá»‘i: {last_error[-1200:]}\n\n"
        "CÃCH CHáº Y TIáº¾P: Ä‘áº·t MP3 táº¡i input\\voice.mp3, TTS_MODE=manual rá»“i cháº¡y láº¡i."
    )



def _split_subtitle_text(text: str, max_words: int = 7, max_chars: int = 38) -> list[str]:
    """Split Vietnamese subtitle text into readable short chunks."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    for word in words:
        candidate = " ".join([*cur, word]).strip()
        if cur and (len(cur) >= max_words or len(candidate) > max_chars):
            chunks.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
        if cur and re.search(r"[.!?â€¦,:;]$", cur[-1]) and len(cur) >= 3:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))

    # Avoid an ugly 1-2 word subtitle flash at the end. Rebalance the last two chunks.
    if len(chunks) >= 2 and len(chunks[-1].split()) <= 2:
        combined = chunks[-2].split() + chunks[-1].split()
        if len(combined) >= 4:
            cut = (len(combined) + 1) // 2
            left = " ".join(combined[:cut])
            right = " ".join(combined[cut:])
            chunks[-2:] = [left, right]
    return chunks


def _append_vi_subtitles(
    entries: list[tuple[float, float, str]],
    vi_text: str,
    start: float,
    duration: float,
    max_words: int,
    max_chars: int,
) -> None:
    chunks = _split_subtitle_text(vi_text, max_words=max_words, max_chars=max_chars)
    if not chunks:
        return
    weights = [max(1, len(re.sub(r"\\s+", "", x))) for x in chunks]
    total = sum(weights)
    t = start
    for i, (chunk, weight) in enumerate(zip(chunks, weights)):
        d = duration * weight / total
        end = start + duration if i == len(chunks) - 1 else t + d
        entries.append((t, end, chunk))
        t = end


def _write_srt_entries(entries: list[tuple[float, float, str]], path: Path) -> None:
    out: list[str] = []
    for idx, (start, end, text) in enumerate(entries, start=1):
        out.extend([
            str(idx),
            f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}",
            text.strip(),
            "",
        ])
    path.write_text("\n".join(out), encoding="utf-8")


def concat_audio_files(inputs: list[Path], dest: Path) -> None:
    if not inputs:
        raise RuntimeError("KhÃ´ng cÃ³ audio segment Ä‘á»ƒ ná»‘i.")
    if len(inputs) == 1:
        shutil.copy2(inputs[0], dest)
        return
    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    labels = "".join(f"[{i}:a]" for i in range(len(inputs)))
    fc = f"{labels}concat=n={len(inputs)}:v=0:a=1[aout]"
    cmd += [
        "-filter_complex", fc,
        "-map", "[aout]",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(dest),
    ]
    run(cmd)


def make_chinese_voice_vietnamese_subs(
    segments: list[dict[str, Any]],
    voice: str,
    rate: str,
    mp3: Path,
    srt: Path,
    mode: str,
    retries: int,
    retry_delay: float,
    fallback_voices: list[str],
    max_words: int,
    max_chars: int,
) -> str:
    """Generate Chinese narration while producing Vietnamese subtitles on the same timeline."""
    cleaned: list[tuple[str, str]] = []
    for seg in segments:
        zh = str(seg.get("zh") or seg.get("voice_zh") or "").strip()
        vi = str(seg.get("vi") or seg.get("subtitle_vi") or "").strip()
        if zh and vi:
            cleaned.append((zh, vi))
    if not cleaned:
        raise RuntimeError("script.json cáº§n narration_segments vá»›i cáº£ trÆ°á»ng zh vÃ  vi.")

    mode = (mode or "auto").strip().lower()
    if mode == "manual":
        manual = INPUT_DIR / "voice.mp3"
        if not manual.exists():
            raise RuntimeError("TTS_MODE=manual nhÆ°ng thiáº¿u input/voice.mp3 (voice Trung).")
        shutil.copy2(manual, mp3)
        duration = ffprobe_duration(mp3)
        vi_all = " ".join(vi for _, vi in cleaned)
        make_approx_srt(vi_all, duration, srt, max_words=max_words)
        return "manual"

    audio_parts: list[Path] = []
    srt_entries: list[tuple[float, float, str]] = []
    cursor = 0.0
    providers: list[str] = []

    try:
        for idx, (zh, vi) in enumerate(cleaned, start=1):
            seg_mp3 = WORK_DIR / f"voice_zh_{idx:03d}.mp3"
            seg_srt_zh = WORK_DIR / f"voice_zh_{idx:03d}.srt"
            provider = make_tts(
                zh,
                voice,
                rate,
                seg_mp3,
                seg_srt_zh,
                mode="edge" if mode in {"auto", "edge"} else mode,
                retries=retries,
                retry_delay=retry_delay,
                fallback_voices=fallback_voices,
            )
            providers.append(provider)
            d = ffprobe_duration(seg_mp3)
            audio_parts.append(seg_mp3)
            _append_vi_subtitles(
                srt_entries, vi, cursor, d,
                max_words=max_words, max_chars=max_chars,
            )
            cursor += d
    except Exception as exc:
        manual = INPUT_DIR / "voice.mp3"
        if mode == "auto" and manual.exists():
            log(f"Edge TTS Trung lỗi -> fallback sang input/voice.mp3: {exc}")
            shutil.copy2(manual, mp3)
            duration = ffprobe_duration(mp3)
            vi_all = " ".join(vi for _, vi in cleaned)
            make_approx_srt(vi_all, duration, srt, max_words=max_words)
            return "manual_fallback"
        raise

    concat_audio_files(audio_parts, mp3)
    _write_srt_entries(srt_entries, srt)
    return "edge_zh" if all(x == "edge" for x in providers) else "+".join(sorted(set(providers)))


def compact_srt(path: Path, max_words: int = 7, max_chars: int = 38) -> None:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    if not raw:
        return

    blocks = re.split(r"\n\s*\n", raw)
    items: list[dict[str, str]] = []
    for block in blocks:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        start, end = lines[1].split(" --> ", 1)
        text = " ".join(lines[2:]).strip()
        if text:
            items.append({"start": start, "end": end, "text": text})

    if not items:
        return

    merged: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for item in items:
        if cur is None:
            cur = dict(item)
        else:
            candidate = (cur["text"] + " " + item["text"]).strip()
            cur_words = len(cur["text"].split())
            next_words = len(item["text"].split())
            should_break = (
                cur_words + next_words > max_words
                or len(candidate) > max_chars
                or bool(re.search(r"[.!?â€¦,:;]$", cur["text"]))
            )
            if should_break:
                merged.append(cur)
                cur = dict(item)
            else:
                cur["text"] = candidate
                cur["end"] = item["end"]
    if cur is not None:
        merged.append(cur)

    out: list[str] = []
    for i, item in enumerate(merged, start=1):
        out.extend([str(i), f'{item["start"]} --> {item["end"]}', item["text"], ""])
    path.write_text("\n".join(out), encoding="utf-8")


def parse_srt_time(t: str) -> float:
    hh, mm, rest = t.strip().replace(",", ".").split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(rest)


def fmt_srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    hh = int(sec // 3600)
    mm = int((sec % 3600) // 60)
    ss = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ss += 1
        ms = 0
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def shift_srt(path: Path, offset: float) -> None:
    if offset <= 0:
        return
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    lines: list[str] = []
    for line in raw.splitlines():
        if " --> " in line:
            a, b = line.split(" --> ", 1)
            line = f"{fmt_srt_time(parse_srt_time(a)+offset)} --> {fmt_srt_time(parse_srt_time(b)+offset)}"
        lines.append(line)
    path.write_text("\n".join(lines), encoding="utf-8")


def srt_time_to_ass(t: str) -> str:
    hh, mm, rest = t.strip().replace(",", ".").split(":")
    ss = float(rest)
    return f"{int(hh)}:{int(mm):02d}:{ss:05.2f}"


def srt_to_ass(
    srt_path: Path,
    ass_path: Path,
    font_name: str,
    font_size: int,
    alignment: int,
    margin_v: int,
    width: int = 1080,
    height: int = 1920,
) -> None:
    raw = srt_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    blocks = re.split(r"\n\s*\n", raw) if raw else []
    events: list[tuple[str, str, str]] = []
    for block in blocks:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        start, end = lines[1].split(" --> ", 1)
        event_text = " ".join(lines[2:]).replace("{", "(").replace("}", ")")
        events.append((srt_time_to_ass(start), srt_time_to_ass(end), event_text))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H70000000,-1,0,0,0,100,100,0,0,3,12,0,{alignment},70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = "".join(
        f"Dialogue: 0,{start},{end},Default,,0,0,0,,{event_text}\n"
        for start, end, event_text in events
    )
    ass_path.write_text(header + body, encoding="utf-8-sig")


def process_clip(
    src: Path,
    dest: Path,
    duration: float,
    darken: float,
    start_time: float = 0.0,
    loop: bool = True,
    width: int = 1080,
    height: int = 1920,
    crop_bottom_fraction: float = 0.0,
) -> None:
    crop_bottom_fraction = max(0.0, min(0.35, float(crop_bottom_fraction)))
    pre_crop = (
        f"crop=iw:ih*{1.0-crop_bottom_fraction:.4f}:0:0,"
        if crop_bottom_fraction > 0.001 else ""
    )
    vf = (
        pre_crop +
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"eq=brightness={darken}:saturation=0.95,"
        "fps=30,setsar=1,format=yuv420p"
    )
    cmd = ["ffmpeg", "-y"]
    if start_time > 0:
        cmd += ["-ss", f"{start_time:.3f}"]
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += [
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
        "-an",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    run(cmd)


def motion_score(
    src: Path,
    start_time: float,
    duration: float,
    sample_fps: float = 2.0,
) -> float:
    """Estimate real movement from frame-to-frame luma differences using FFmpeg only."""
    sample_duration = max(1.0, min(float(duration), 6.0))
    w, h = 160, 90
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{max(0.0, start_time):.3f}",
        "-i", str(src),
        "-t", f"{sample_duration:.3f}",
        "-vf", f"fps={sample_fps},scale={w}:{h}:flags=fast_bilinear,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, cwd=ROOT, timeout=60)
    if result.returncode != 0 or not result.stdout:
        return 0.0
    frame_size = w * h
    data = result.stdout
    frames = [
        data[i:i + frame_size]
        for i in range(0, len(data) - frame_size + 1, frame_size)
    ]
    if len(frames) < 2:
        return 0.0
    diffs: list[float] = []
    for a, b in zip(frames, frames[1:]):
        total = sum(abs(x - y) for x, y in zip(a, b))
        diffs.append(total / frame_size)
    return sum(diffs) / len(diffs) if diffs else 0.0


def best_motion_window(
    src: Path,
    clip_duration: float,
    trials: int = 5,
) -> tuple[float, float]:
    source_duration = ffprobe_duration(src)
    clip_duration = min(max(0.5, clip_duration), source_duration)
    max_start = max(0.0, source_duration - clip_duration)
    if max_start <= 0.05:
        return 0.0, motion_score(src, 0.0, clip_duration)

    starts = [max_start * i / max(1, trials - 1) for i in range(max(2, trials))]
    best_start, best_score = 0.0, -1.0
    for s in starts:
        score = motion_score(src, s, clip_duration)
        if score > best_score:
            best_start, best_score = s, score
    return best_start, best_score


def concat_clips(clips: list[Path], dest: Path) -> None:
    concat_file = WORK_DIR / "concat.txt"
    with concat_file.open("w", encoding="utf-8") as f:
        for clip in clips:
            escaped = str(clip.resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(dest),
        ]
    )


def distribute_durations(total: float, scenes: list[dict[str, Any]]) -> list[float]:
    if not scenes:
        return []
    weights = [max(1, len((s.get("text") or "").strip())) for s in scenes]
    s = sum(weights)
    raw = [total * w / s for w in weights]
    min_d = 2.5
    if total >= min_d * len(scenes):
        fixed = [max(min_d, d) for d in raw]
        scale = total / sum(fixed)
        return [d * scale for d in fixed]
    return raw


def resolve_root_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def list_video_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "celebrity"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def strip_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def cache_extension_from_url(url: str, mime: str = "") -> str:
    ext = Path(unquote(urlparse(url).path)).suffix.lower()
    if ext in VIDEO_EXTS or ext in {".ogv", ".ogg"}:
        return ext
    mime = (mime or "").lower()
    if "webm" in mime:
        return ".webm"
    if "ogg" in mime or "ogv" in mime:
        return ".ogv"
    if "quicktime" in mime:
        return ".mov"
    return ".mp4"


def download_celebrity_url(url: str, folder: Path, name_hint: str, metadata: dict[str, Any] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    meta = metadata or {}
    ext = cache_extension_from_url(url, str(meta.get("mime") or ""))
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    dest = folder / f"remote_{slugify(name_hint)}_{digest}{ext}"
    if dest.exists() and dest.stat().st_size > 1024:
        return dest
    log(f"Táº£i celebrity source -> {dest.name}")
    download_file(url, dest)
    try:
        ffprobe_duration(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"URL khÃ´ng pháº£i video FFmpeg Ä‘á»c Ä‘Æ°á»£c: {url}")
    sidecar = dest.with_suffix(dest.suffix + ".json")
    write_json(sidecar, {"url": url, **meta})
    return dest


def commons_file_title_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if "commons.wikimedia.org" not in parsed.netloc.lower():
        return None
    path = unquote(parsed.path)
    marker = "/wiki/"
    if marker not in path:
        return None
    title = path.split(marker, 1)[1].replace("_", " ")
    if title.lower().startswith("file:"):
        return title
    return None


def choose_wikimedia_media(vinfo: dict[str, Any]) -> tuple[str, str, int, int] | None:
    derivatives = vinfo.get("derivatives") or []
    candidates: list[tuple[float, str, str, int, int]] = []
    for d in derivatives:
        src = d.get("src") or d.get("url")
        if not src:
            continue
        typ = str(d.get("type") or "")
        w = int(d.get("width") or 0)
        h = int(d.get("height") or 0)
        # Æ¯u tiÃªn MP4/QuickTime 360-720p Ä‘á»ƒ cache khÃ´ng quÃ¡ náº·ng.
        mp4_bonus = 5.0 if ("mp4" in typ.lower() or str(src).lower().endswith(".mp4")) else 0.0
        target_w = 720
        size_score = -abs((w or target_w) - target_w) / 500.0
        candidates.append((mp4_bonus + size_score, str(src), typ, w, h))
    if candidates:
        _, src, typ, w, h = max(candidates, key=lambda x: x[0])
        return src, typ, w, h
    src = vinfo.get("url")
    mime = str(vinfo.get("mime") or "")
    if src and mime.startswith("video/"):
        return str(src), mime, int(vinfo.get("width") or 0), int(vinfo.get("height") or 0)
    return None


def wikimedia_file_info(title: str) -> dict[str, Any] | None:
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "titles": title,
        "prop": "videoinfo",
        "viprop": "url|mime|size|derivatives|extmetadata",
        "viextmetadatalanguage": "en",
        "viextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|UsageTerms|ImageDescription",
    }
    r = requests.get(WIKIMEDIA_API, params=params, timeout=30, headers={"User-Agent": "BrollVideoV2.3/2.3"})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or []
    if not pages:
        return None
    page = pages[0]
    infos = page.get("videoinfo") or []
    if not infos:
        return None
    vi = infos[0]
    media = choose_wikimedia_media(vi)
    if not media:
        return None
    media_url, mime, width, height = media
    extmeta = vi.get("extmetadata") or {}
    def mv(key: str) -> str:
        x = extmeta.get(key) or {}
        return strip_html(str(x.get("value") or ""))
    return {
        "provider": "wikimedia_commons",
        "title": page.get("title") or title,
        "pageid": page.get("pageid"),
        "media_url": media_url,
        "mime": mime,
        "width": width,
        "height": height,
        "source_page": "https://commons.wikimedia.org/wiki/" + str(page.get("title") or title).replace(" ", "_"),
        "license": mv("LicenseShortName"),
        "license_url": mv("LicenseUrl"),
        "artist": mv("Artist"),
        "credit": mv("Credit"),
        "usage_terms": mv("UsageTerms"),
        "description": mv("ImageDescription"),
    }


def search_wikimedia_celebrity(query: str, max_results: int = 12) -> list[dict[str, Any]]:
    search_q = f'{query} filetype:video'
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "search",
        "srsearch": search_q,
        "srnamespace": "6",
        "srlimit": str(max(1, min(max_results, 30))),
    }
    r = requests.get(WIKIMEDIA_API, params=params, timeout=30, headers={"User-Agent": "BrollVideoV2.3/2.3"})
    r.raise_for_status()
    results = (r.json().get("query") or {}).get("search") or []
    out: list[dict[str, Any]] = []
    for item in results:
        title = str(item.get("title") or "")
        if not title.lower().startswith("file:"):
            continue
        try:
            info = wikimedia_file_info(title)
            if info:
                out.append(info)
        except Exception as exc:
            log(f"Wikimedia bá» qua {title}: {exc}")
    return out


def ensure_url_sources(cfg: dict[str, Any], folder: Path, name: str) -> list[Path]:
    raw_urls: list[str] = []
    one = str(cfg.get("url") or "").strip()
    if one:
        raw_urls.append(one)
    for u in cfg.get("urls") or []:
        u = str(u).strip()
        if u and u not in raw_urls:
            raw_urls.append(u)
    added: list[Path] = []
    for url in raw_urls:
        commons_title = commons_file_title_from_url(url)
        if commons_title:
            info = wikimedia_file_info(commons_title)
            if not info:
                raise RuntimeError(f"KhÃ´ng resolve Ä‘Æ°á»£c Wikimedia video: {url}")
            added.append(download_celebrity_url(info["media_url"], folder, name, info))
            continue
        # Chá»‰ tá»± táº£i direct media URL. Trang YouTube/TikTok/etc khÃ´ng Ä‘Æ°á»£c giáº£ Ä‘á»‹nh lÃ  file video trá»±c tiáº¿p.
        ext = Path(unquote(urlparse(url).path)).suffix.lower()
        if ext not in VIDEO_EXTS and ext not in {".ogv", ".ogg"}:
            raise RuntimeError(
                "celebrity.url pháº£i lÃ  direct video URL (.mp4/.webm/...) hoáº·c Wikimedia Commons File URL. "
                "Náº¿u lÃ  YouTube/TikTok, hÃ£y táº£i clip mÃ  báº¡n cÃ³ quyá»n sá»­ dá»¥ng vÃ  Ä‘áº·t vÃ o folder local."
            )
        added.append(download_celebrity_url(url, folder, name, {"provider": "direct_url"}))
    return added



SERPER_VIDEOS_API = "https://google.serper.dev/videos"
SERPER_SEARCH_API = "https://google.serper.dev/search"


def _is_http_url(value: str) -> bool:
    value = (value or "").strip()
    return value.startswith("http://") or value.startswith("https://")


def _normalize_search_result_url(value: str) -> str:
    """Return only a real absolute URL. Never pass Serper/Google /goto redirects to yt-dlp."""
    value = html.unescape((value or "").strip())
    if not value:
        return ""

    # Some search providers may return a Google redirect URL with a readable q/url target.
    if _is_http_url(value):
        parsed = urlparse(value)
        if parsed.path in {"/url", "/goto"}:
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query)
            for key in ("url", "q"):
                target = (qs.get(key) or [""])[0]
                if _is_http_url(target):
                    return target
        return value

    # Relative /goto?url=CAES... values returned by Google Videos are opaque tokens,
    # not downloadable URLs. Reject them and use the web-search fallback instead.
    return ""


def _pick_serper_row_url(row: dict[str, Any]) -> str:
    # videoUrl/url are checked first for forward compatibility; current Serper docs use link.
    for key in ("videoUrl", "url", "link"):
        url = _normalize_search_result_url(str(row.get(key) or ""))
        if url:
            return url
    return ""


def _parse_duration_text(value: str) -> float:
    value = (value or "").strip()
    if not value:
        return 0.0
    parts = value.split(":")
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return 0.0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return 0.0


def _serper_headers() -> dict[str, str]:
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Thiáº¿u SERPER_API_KEY trong .env.")
    return {"X-API-KEY": api_key, "Content-Type": "application/json"}


def _serper_payload(query: str, max_results: int) -> dict[str, Any]:
    return {
        "q": query,
        "num": max(1, min(int(max_results), 20)),
        "gl": os.getenv("SERPER_GL", "us").strip() or "us",
        "hl": os.getenv("SERPER_HL", "en").strip() or "en",
    }


def serper_video_search(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Google Videos via Serper. Invalid relative /goto links are intentionally ignored."""
    try:
        r = requests.post(
            SERPER_VIDEOS_API,
            json=_serper_payload(query, max_results),
            timeout=30,
            headers=_serper_headers(),
        )
        if r.status_code in {401, 403, 400}:
            log("Serper Videos lỗi/hết quota -> chuyển fallback")
            return []
        r.raise_for_status()
        rows = r.json().get("videos") or []
    except Exception as exc:
        log(f"Serper Videos search exception: {exc}")
        return []
    out: list[dict[str, Any]] = []
    skipped_redirects = 0
    for row in rows:
        raw_link = str(row.get("link") or "").strip()
        link = _pick_serper_row_url(row)
        if not link:
            if raw_link.startswith("/goto?"):
                skipped_redirects += 1
            continue
        out.append({
            "provider": "google_serper_video",
            "title": str(row.get("title") or "").strip(),
            "link": link,
            "snippet": str(row.get("snippet") or "").strip(),
            "channel": str(row.get("channel") or row.get("source") or "").strip(),
            "duration_text": str(row.get("duration") or "").strip(),
            "date": str(row.get("date") or "").strip(),
            "position": row.get("position"),
            "image_url": str(row.get("imageUrl") or "").strip(),
        })
    if skipped_redirects:
        log(f"Serper Videos trả {skipped_redirects} link /goto không dùng được -> chuyển fallback Google Web Search.")
    return out


def serper_web_video_search(query: str, name: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Use normal Google web SERP to recover real YouTube/Vimeo URLs when Videos returns /goto tokens."""
    web_query = f'site:youtube.com/watch "{name}" interview speaking conversation {query}'
    try:
        r = requests.post(
            SERPER_SEARCH_API,
            json=_serper_payload(web_query, max_results),
            timeout=30,
            headers=_serper_headers(),
        )
        if r.status_code in {401, 403, 400}:
            log("Serper Web Search lỗi/hết quota -> chuyển fallback ytsearch")
            return []
        r.raise_for_status()
        rows = r.json().get("organic") or []
    except Exception as exc:
        log(f"Serper Web Search exception: {exc}")
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        link = _pick_serper_row_url(row)
        if not link:
            continue
        host = urlparse(link).netloc.lower()
        if not any(x in host for x in ("youtube.com", "youtu.be", "vimeo.com")):
            continue
        out.append({
            "provider": "google_serper_web",
            "title": str(row.get("title") or "").strip(),
            "link": link,
            "snippet": str(row.get("snippet") or "").strip(),
            "channel": str(row.get("source") or "").strip(),
            "duration_text": "",
            "date": str(row.get("date") or "").strip(),
            "position": row.get("position"),
            "image_url": str(row.get("imageUrl") or "").strip(),
        })
    return out


def ytdlp_search_results(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    """Last-resort direct YouTube search. Does not download until a candidate passes metadata checks."""
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Thiáº¿u yt-dlp. Cháº¡y: .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    n = max(1, min(int(max_results), 15))
    opts = _yt_dlp_options("%(id)s.%(ext)s")
    opts["skip_download"] = True
    opts["extract_flat"] = "in_playlist"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    except Exception as exc:
        if opts.get("cookiesfrombrowser") or _is_cookie_browser_error(exc):
            log(f"yt-dlp search cookie browser lỗi -> retry không cookie: {exc}")
            opts.pop("cookiesfrombrowser", None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
        else:
            raise
    entries = (info or {}).get("entries") or []
    out: list[dict[str, Any]] = []
    for pos, entry in enumerate(entries, start=1):
        if not entry:
            continue
        vid = str(entry.get("id") or "").strip()
        link = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        if not _is_http_url(link) and vid:
            link = f"https://www.youtube.com/watch?v={vid}"
        if not _is_http_url(link):
            continue
        out.append({
            "provider": "ytsearch",
            "title": str(entry.get("title") or "").strip(),
            "link": link,
            "snippet": str(entry.get("description") or "").strip(),
            "channel": str(entry.get("channel") or entry.get("uploader") or "").strip(),
            "duration_text": "",
            "position": pos,
            "image_url": "",
        })
    return out


def score_google_video_result(item: dict[str, Any], name: str) -> int:
    hay = " ".join([
        str(item.get("title") or ""),
        str(item.get("snippet") or ""),
        str(item.get("channel") or ""),
    ]).lower()
    score = 0
    for token in [x for x in re.findall(r"[a-z0-9]+", name.lower()) if len(x) > 2]:
        if token in hay:
            score += 10
    for kw in ("interview", "speaking", "talk", "conversation", "speech", "answers", "discusses"):
        if kw in hay:
            score += 4
    for bad in ("reaction", "explained", "documentary", "compilation", "motivation edit", "shorts remix"):
        if bad in hay:
            score -= 5
    link = str(item.get("link") or "").lower()
    if "youtube.com/watch" in link or "youtu.be/" in link:
        score += 3
    if "youtube.com/shorts/" in link:
        score -= 8

    # Prefer manageable interview clips over 1-3 hour meetings when duration is known.
    seconds = _parse_duration_text(str(item.get("duration_text") or ""))
    if 30 <= seconds <= 20 * 60:
        score += 7
    elif 20 * 60 < seconds <= 60 * 60:
        score += 2
    elif seconds > 60 * 60:
        score -= 3
    return score


def _yt_dlp_options(outtmpl: str, quiet: bool = True) -> dict[str, Any]:
    # Original celebrity audio is never used in V2.4; 480p is enough for a 3-5s square hook
    # and keeps cached source files much smaller than full 720p interviews.
    opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "format": "bestvideo[ext=mp4][vcodec^=avc1][height<=480]/best[ext=mp4][vcodec^=avc1][height<=480]/bestvideo[height<=480]/best[height<=480]",
        "noplaylist": True,
        "quiet": quiet,
        "no_warnings": quiet,
        "socket_timeout": 15,
        "retries": 1,
        "fragment_retries": 1,
        "overwrites": False,
    }
    browser = os.getenv("YT_DLP_COOKIES_FROM_BROWSER", "").strip()
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    return opts



def _is_cookie_browser_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "cookie" in text and ("browser" in text or "database" in text or "could not copy" in text)
def inspect_video_url_with_ytdlp(url: str) -> dict[str, Any]:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Thiáº¿u yt-dlp. Cháº¡y: .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc
    if not _is_http_url(url):
        raise RuntimeError(f"URL khÃ´ng há»£p lá»‡, bá» qua: {url!r}")
    opts = _yt_dlp_options("%(id)s.%(ext)s")
    opts["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if not opts.get("cookiesfrombrowser") or not _is_cookie_browser_error(exc):
            raise
        log(f"yt-dlp cookie browser lỗi -> retry không cookie: {exc}")
        opts.pop("cookiesfrombrowser", None)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    if info and info.get("_type") == "playlist":
        entries = [x for x in (info.get("entries") or []) if x]
        info = entries[0] if entries else None
    if not info:
        raise RuntimeError("yt-dlp khÃ´ng Ä‘á»c Ä‘Æ°á»£c metadata URL.")
    return dict(info)


def download_google_video_result(
    item: dict[str, Any], folder: Path, name: str, cfg: dict[str, Any]
) -> Path:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Thiáº¿u yt-dlp. Cháº¡y: .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    url = _normalize_search_result_url(str(item.get("link") or ""))
    if not url:
        raise RuntimeError("Search result khÃ´ng cÃ³ URL video tháº­t (Ä‘Ã£ bá» /goto redirect).")

    info = inspect_video_url_with_ytdlp(url)
    if bool(info.get("is_live")) or str(info.get("live_status") or "").lower() in {"is_live", "is_upcoming"}:
        raise RuntimeError("Bá» qua livestream/upcoming.")
    duration = float(info.get("duration") or 0)
    min_source = float(cfg.get("min_source_duration", 8) or 8)
    max_source = float(cfg.get("max_source_duration", 7200) or 7200)
    if duration and duration < min_source:
        raise RuntimeError(f"Video nguá»“n quÃ¡ ngáº¯n: {duration:.1f}s")
    if duration and duration > max_source:
        raise RuntimeError(f"Video nguá»“n quÃ¡ dÃ i: {duration/60:.1f} phÃºt > {max_source/60:.1f} phÃºt")

    folder.mkdir(parents=True, exist_ok=True)
    vid = str(info.get("id") or hashlib.sha1(url.encode()).hexdigest()[:12])
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", vid)[:80]
    prefix = folder / f"google_{slugify(name)}_{safe_id}"

    existing = [p for p in folder.glob(prefix.name + ".*") if p.suffix.lower() in VIDEO_EXTS]
    if existing:
        return existing[0]

    log(f"yt-dlp táº£i celebrity: {item.get('title') or url}")
    opts = _yt_dlp_options(str(prefix) + ".%(ext)s", quiet=False)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        if not opts.get("cookiesfrombrowser") or not _is_cookie_browser_error(exc):
            raise
        log(f"yt-dlp cookie browser lỗi -> retry tải không cookie: {exc}")
        opts.pop("cookiesfrombrowser", None)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

    files = [p for p in folder.glob(prefix.name + ".*") if p.suffix.lower() in VIDEO_EXTS]
    if not files:
        raise RuntimeError("yt-dlp cháº¡y xong nhÆ°ng khÃ´ng tÃ¬m tháº¥y file video output.")
    dest = max(files, key=lambda p: p.stat().st_size)
    ffprobe_duration(dest)
    sidecar = dest.with_suffix(dest.suffix + ".json")
    write_json(sidecar, {
        "provider": str(item.get("provider") or "google_search"),
        "google_result": item,
        "source_url": url,
        "title": info.get("title") or item.get("title"),
        "uploader": info.get("uploader") or info.get("channel") or item.get("channel"),
        "webpage_url": info.get("webpage_url") or url,
        "duration": info.get("duration"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "note": "Original celebrity audio is forcibly muted by V2.4 renderer. Verify reuse rights for your use case.",
    })
    return dest


def google_cached_files(folder: Path) -> list[Path]:
    out: list[Path] = []
    for p in list_video_files(folder):
        sidecar = p.with_suffix(p.suffix + ".json")
        meta = read_json(sidecar, {}) if sidecar.exists() else {}
        if isinstance(meta, dict) and (
            str(meta.get("provider") or "").startswith("google_")
            or str(meta.get("provider") or "") == "ytsearch"
        ):
            out.append(p)
    return out


def _download_ranked_candidates(
    results: list[dict[str, Any]], folder: Path, name: str, cfg: dict[str, Any], wanted: int
) -> tuple[list[Path], list[str]]:
    results = list(results)

    # V2.6: reject known wrong-person/documentary results before any download.
    exclude_title_keywords = [
        str(x).strip().lower()
        for x in (cfg.get("exclude_title_keywords") or [])
        if str(x).strip()
    ]
    if exclude_title_keywords:
        filtered_ex = []
        for item in results:
            title_l = str(item.get("title") or "").lower()
            if any(bad in title_l for bad in exclude_title_keywords):
                log(f"Bá» result do title blacklist: {item.get('title')}")
                continue
            filtered_ex.append(item)
        results = filtered_ex

    # Name must appear reasonably early in the title. This rejects titles such as
    # 'Charles Munger Interview ... with Warren Buffett'.
    name_pos_max = int(cfg.get("name_position_max", 35) or 35)
    if name_pos_max >= 0:
        name_l = name.lower().strip()
        filtered_pos = []
        for item in results:
            title_l = str(item.get("title") or "").lower()
            pos = title_l.find(name_l)
            if pos < 0 or pos > name_pos_max:
                continue
            filtered_pos.append(item)
        if filtered_pos:
            results = filtered_pos

    if bool(cfg.get("require_name_in_title", True)):
        tokens = [x for x in re.findall(r"[a-z0-9]+", name.lower()) if len(x) > 2]
        filtered = []
        for item in results:
            title = str(item.get("title") or "").lower()
            if tokens and all(t in title for t in tokens):
                filtered.append(item)
        if filtered:
            results = filtered
        else:
            log("KhÃ´ng cÃ³ result nÃ o chá»©a Ä‘áº§y Ä‘á»§ tÃªn celebrity trong TITLE -> giá»¯ danh sÃ¡ch gá»‘c Ä‘á»ƒ fallback.")
    results.sort(key=lambda x: score_google_video_result(x, name), reverse=True)
    added: list[Path] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in results:
        if len(added) >= wanted:
            break
        url = _normalize_search_result_url(str(item.get("link") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            path = download_google_video_result(item, folder, name, cfg)
            added.append(path)
            log(f"Celebrity cached: {path.name}")
        except Exception as exc:
            errors.append(f"{item.get('title')}: {exc}")
            log(f"Bá» candidate: {item.get('title')} | {exc}")
    return added, errors


def ensure_google_search_source(cfg: dict[str, Any], folder: Path, name: str) -> list[Path]:
    refresh = bool(cfg.get("refresh_search", False))
    cached = google_cached_files(folder)
    if cached and not refresh:
        log(f"Celebrity cache cÃ³ {len(cached)} clip -> khÃ´ng search láº¡i.")
        return cached

    query = str(cfg.get("search_query") or f'"{name}" interview speaking').strip()
    max_results = int(cfg.get("search_results", 10) or 10)
    wanted = max(1, int(cfg.get("search_download_count", 2) or 2))
    all_errors: list[str] = []

    # 1) Google Videos through Serper. Works when direct result URLs are returned.
    log(f"1/3 Google Videos/Serper: {query!r}")
    try:
        results = serper_video_search(query, max_results=max_results)
        added, errors = _download_ranked_candidates(results, folder, name, cfg, wanted)
        all_errors.extend(errors)
        cached = google_cached_files(folder)
        if len(cached) >= wanted:
            return cached
    except Exception as exc:
        all_errors.append(f"Google Videos: {exc}")
        log(f"Google Videos lá»—i -> fallback Web Search: {exc}")

    # 2) Normal Google web search via Serper. Organic results use real absolute URLs.
    log("2/3 Google Web Search: tÃ¬m URL YouTube tháº­t...")
    try:
        results = serper_web_video_search(query, name, max_results=max_results)
        need = max(0, wanted - len(google_cached_files(folder)))
        added, errors = _download_ranked_candidates(results, folder, name, cfg, need)
        all_errors.extend(errors)
        cached = google_cached_files(folder)
        if cached:
            return cached
    except Exception as exc:
        all_errors.append(f"Google Web Search: {exc}")
        log(f"Google Web Search lá»—i -> fallback ytsearch: {exc}")

    # 3) Last fallback: yt-dlp's built-in YouTube search, so the job does not die because
    # Serper/Google changed an internal redirect format.
    log("3/3 yt-dlp ytsearch fallback...")
    try:
        yt_query = str(cfg.get("ytsearch_query") or f"{name} interview speaking conversation").strip()
        results = ytdlp_search_results(yt_query, max_results=max_results)
        need = max(1, wanted - len(google_cached_files(folder)))
        _, errors = _download_ranked_candidates(results, folder, name, cfg, need)
        all_errors.extend(errors)
    except Exception as exc:
        all_errors.append(f"ytsearch: {exc}")

    final = google_cached_files(folder)
    if not final:
        tail = all_errors[-3:] if all_errors else ["khÃ´ng cÃ³ candidate phÃ¹ há»£p"]
        raise RuntimeError("KhÃ´ng táº£i Ä‘Æ°á»£c celebrity video. " + " | ".join(tail))
    return final

def ensure_search_source(cfg: dict[str, Any], folder: Path, name: str) -> list[Path]:
    provider = str(cfg.get("search_provider") or "google_serper").strip().lower()
    if provider in {"google", "google_serper", "serper"}:
        return ensure_google_search_source(cfg, folder, name)

    if provider not in {"wikimedia", "wikimedia_commons"}:
        raise RuntimeError("V2.2 search_provider há»— trá»£: google_serper hoáº·c wikimedia")

    refresh = bool(cfg.get("refresh_search", False))
    cached = list_video_files(folder)
    if cached and not refresh:
        log(f"Search cache cÃ³ sáºµn {len(cached)} clip -> khÃ´ng gá»i máº¡ng.")
        return cached

    query = str(cfg.get("search_query") or f"{name} speaking interview").strip()
    max_results = int(cfg.get("search_results", 10) or 10)
    log(f"Search Wikimedia Commons: {query!r}")
    results = search_wikimedia_celebrity(query, max_results=max_results)
    if not results and query.lower() != name.lower():
        log(f"KhÃ´ng tháº¥y -> thá»­ láº¡i: {name!r}")
        results = search_wikimedia_celebrity(name, max_results=max_results)
    if not results:
        return []

    name_tokens = [x for x in re.findall(r"[a-z0-9]+", name.lower()) if len(x) > 2]
    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        hay = (str(item.get("title") or "") + " " + str(item.get("description") or "")).lower()
        match = sum(1 for t in name_tokens if t in hay)
        mp4 = 1 if "mp4" in str(item.get("mime") or "").lower() or str(item.get("media_url") or "").lower().endswith(".mp4") else 0
        area = int(item.get("width") or 0) * int(item.get("height") or 0)
        return (match, mp4, area)
    results.sort(key=score, reverse=True)

    downloads = int(cfg.get("search_download_count", 1) or 1)
    added: list[Path] = []
    for info in results[: max(1, downloads)]:
        try:
            added.append(download_celebrity_url(info["media_url"], folder, name, info))
            log(f"Wikimedia cached: {info.get('title')} | {info.get('license') or 'license n/a'}")
        except Exception as exc:
            log(f"KhÃ´ng táº£i Ä‘Æ°á»£c {info.get('title')}: {exc}")
    return added


def celebrity_history() -> list[dict[str, Any]]:
    data = read_json(CELEBRITY_HISTORY_FILE, [])
    return data if isinstance(data, list) else []


def segment_signature(path: Path, start: float, duration: float) -> str:
    # bucket 0.5s Ä‘á»ƒ cÃ¡c Ä‘oáº¡n gáº§n nhau váº«n Ä‘Æ°á»£c coi lÃ  láº·p.
    return f"{path.resolve()}|{round(start * 2) / 2:.1f}|{round(duration * 2) / 2:.1f}"



def _ensure_yunet_model() -> Path | None:
    """Download OpenCV Zoo YuNet face detector once; fallback to Haar if unavailable."""
    global _YUNET_MODEL_ATTEMPTED, _YUNET_MODEL_CACHE
    if _YUNET_MODEL_ATTEMPTED:
        return _YUNET_MODEL_CACHE
    _YUNET_MODEL_ATTEMPTED = True
    model_dir = DATA_DIR / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model = model_dir / "face_detection_yunet_2023mar.onnx"
    if model.exists() and model.stat().st_size > 100_000:
        _YUNET_MODEL_CACHE = model
        return model

    urls = [
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ]
    for url in urls:
        try:
            log("Táº£i model kiá»ƒm tra khuÃ´n máº·t YuNet (~230 KB)...")
            r = requests.get(url, timeout=30, headers={"User-Agent": "BrollVideoV2.5/2.5"})
            r.raise_for_status()
            if len(r.content) < 100_000:
                continue
            tmp = model.with_suffix(".onnx.tmp")
            tmp.write_bytes(r.content)
            tmp.replace(model)
            _YUNET_MODEL_CACHE = model
            return model
        except Exception as exc:
            log(f"YuNet download fallback: {exc}")
    return None


def _load_face_detectors():
    """Prefer YuNet DNN; keep Haar as a no-network fallback. Cached per process."""
    global _FACE_DETECTOR_CACHE
    if _FACE_DETECTOR_CACHE is not None:
        return _FACE_DETECTOR_CACHE
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Thiáº¿u opencv-python-headless Ä‘á»ƒ kiá»ƒm tra khuÃ´n máº·t celebrity. "
            "Cháº¡y láº¡i run.bat Ä‘á»ƒ cÃ i requirements.txt."
        ) from exc

    yunet = None
    model = _ensure_yunet_model()
    if model is not None and hasattr(cv2, "FaceDetectorYN_create"):
        try:
            yunet = cv2.FaceDetectorYN_create(
                str(model), "", (320, 320), 0.70, 0.3, 5000
            )
        except Exception as exc:
            log(f"YuNet load lá»—i -> dÃ¹ng Haar fallback: {exc}")
            yunet = None

    frontal_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    profile_path = str(Path(cv2.data.haarcascades) / "haarcascade_profileface.xml")
    frontal = cv2.CascadeClassifier(frontal_path)
    profile = cv2.CascadeClassifier(profile_path)
    _FACE_DETECTOR_CACHE = (cv2, yunet, frontal, profile)
    return _FACE_DETECTOR_CACHE


def _frame_face_score(frame, cv2, yunet, frontal, profile) -> tuple[float, bool]:
    """Favor one clear, large face near center. YuNet first, Haar fallback."""
    if frame is None:
        return 0.0, False
    h, w = frame.shape[:2]
    if h <= 0 or w <= 0:
        return 0.0, False

    max_w = 720
    if w > max_w:
        scale = max_w / w
        frame = cv2.resize(frame, (max_w, max(1, int(h * scale))))
        h, w = frame.shape[:2]

    boxes: list[tuple[float, float, float, float, float]] = []
    if yunet is not None:
        try:
            yunet.setInputSize((w, h))
            _, faces = yunet.detect(frame)
            if faces is not None:
                for row in faces:
                    x, y, fw, fh = [float(v) for v in row[:4]]
                    conf = float(row[-1]) if len(row) else 1.0
                    boxes.append((x, y, fw, fh, conf))
        except Exception:
            boxes = []

    if not boxes:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        min_side = max(36, int(min(w, h) * 0.08))
        if not frontal.empty():
            for x, y, fw, fh in frontal.detectMultiScale(
                gray, scaleFactor=1.08, minNeighbors=5, minSize=(min_side, min_side)
            ):
                boxes.append((float(x), float(y), float(fw), float(fh), 0.75))
        if not profile.empty():
            for x, y, fw, fh in profile.detectMultiScale(
                gray, scaleFactor=1.08, minNeighbors=5, minSize=(min_side, min_side)
            ):
                boxes.append((float(x), float(y), float(fw), float(fh), 0.70))

    if not boxes:
        return 0.0, False

    best = 0.0
    for x, y, fw, fh, conf in boxes:
        area_ratio = (fw * fh) / float(w * h)
        if area_ratio < 0.010:
            continue
        cx = (x + fw / 2) / w
        cy = (y + fh / 2) / h
        center_dist = ((cx - 0.5) ** 2 + (cy - 0.43) ** 2) ** 0.5
        center_bonus = max(0.0, 1.0 - center_dist / 0.68)
        # Reference is a close-up head/shoulders shot; reward larger faces heavily.
        size_bonus = min(1.0, area_ratio / 0.10)
        score = 52.0 * size_bonus + 30.0 * center_bonus + 18.0 * max(0.0, min(1.0, conf))
        best = max(best, score)
    return best, best > 0.0

def celebrity_face_window_score(
    src: Path,
    start_time: float,
    duration: float,
    samples: int = 4,
) -> tuple[float, int, float]:
    """Check several frames inside a 3-5s hook; reject graphics/B-roll with no clear face."""
    cv2, yunet, frontal, profile = _load_face_detectors()
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return 0.0, 0, 0.0

    per_frame: list[float] = []
    hits = 0
    try:
        for i in range(samples):
            frac = 0.15 + (0.70 * i / max(1, samples - 1))
            t = max(0.0, start_time + duration * frac)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                per_frame.append(0.0)
                continue
            score, found = _frame_face_score(frame, cv2, yunet, frontal, profile)
            per_frame.append(score)
            hits += int(found)
    finally:
        cap.release()

    avg = sum(per_frame) / max(1, len(per_frame))
    consistency = hits / max(1, samples)
    final = avg * (0.55 + 0.45 * consistency)
    return final, hits, consistency


def best_celebrity_talking_window(
    src: Path,
    clip_duration: float,
    cfg: dict[str, Any],
) -> tuple[float, float, int, float]:
    """Scan source efficiently for a 3-5s window containing a clear talking-head face."""
    source_duration = ffprobe_duration(src)
    clip_duration = min(max(1.0, clip_duration), source_duration)

    padding = max(0.0, float(cfg.get("start_padding", 5.0) or 0.0))
    scan_min = max(padding, float(cfg.get("face_scan_start_min", 25.0) or 0.0))
    scan_max_cfg = float(cfg.get("face_scan_end_max", 600.0) or 600.0)
    scan_max = min(max(0.0, source_duration - clip_duration - 0.1), scan_max_cfg)
    if scan_max < scan_min:
        scan_min = min(padding, max(0.0, source_duration - clip_duration))
        scan_max = max(scan_min, source_duration - clip_duration)

    max_windows = max(6, int(cfg.get("face_scan_max_windows", 24) or 24))
    if scan_max <= scan_min + 0.1:
        starts = [scan_min]
    else:
        count = min(max_windows, max(6, int((scan_max - scan_min) / 10.0) + 1))
        starts = [
            scan_min + (scan_max - scan_min) * i / max(1, count - 1)
            for i in range(count)
        ]
    if scan_max > scan_min + 1:
        for _ in range(min(6, max_windows // 4)):
            starts.append(random.uniform(scan_min, scan_max))

    cv2, yunet, frontal, profile = _load_face_detectors()
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return scan_min, 0.0, 0, 0.0

    best = (scan_min, 0.0, 0, 0.0)
    samples = 4
    try:
        for start in starts[: max_windows + 6]:
            frame_scores: list[float] = []
            hits = 0
            for i in range(samples):
                frac = 0.12 + (0.76 * i / max(1, samples - 1))
                t = max(0.0, start + clip_duration * frac)
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    frame_scores.append(0.0)
                    continue
                score, found = _frame_face_score(frame, cv2, yunet, frontal, profile)
                frame_scores.append(score)
                hits += int(found)
            avg = sum(frame_scores) / max(1, len(frame_scores))
            consistency = hits / samples
            combined = avg * (0.50 + 0.50 * consistency)
            # Hard penalty when the face appears only briefly (intro/logo/B-roll transitions).
            if hits < int(cfg.get("face_min_hits", 3) or 3):
                combined *= 0.20
            if combined > best[1]:
                best = (start, combined, hits, consistency)
    finally:
        cap.release()
    return best

def record_celebrity_use(item: dict[str, Any], keep: int = 300) -> None:
    if not item:
        return
    history = celebrity_history()
    history.append({
        "time": int(time.time()),
        "name": item.get("name"),
        "file": str(Path(item["source"]).resolve()),
        "start_time": round(float(item.get("start_time") or 0), 3),
        "duration": round(float(item.get("duration") or 0), 3),
        "signature": item.get("signature"),
    })
    write_json(CELEBRITY_HISTORY_FILE, history[-max(20, keep):])


def pick_celebrity_segment(cfg: dict[str, Any]) -> dict[str, Any] | None:
    if not cfg or not bool(cfg.get("enabled", True)):
        return None

    name = str(cfg.get("name") or "Celebrity").strip()
    folder = resolve_root_path(str(cfg.get("folder") or f"input/celebrity/{slugify(name)}"))
    folder.mkdir(parents=True, exist_ok=True)
    source_mode = str(cfg.get("source_mode") or "auto").strip().lower()
    if source_mode not in {"auto", "local", "url", "search"}:
        raise RuntimeError("celebrity.source_mode chá»‰ nháº­n: auto/local/url/search")

    files = list_video_files(folder)
    resolved_mode = "local"

    if source_mode == "url":
        ensure_url_sources(cfg, folder, name)
        files = list_video_files(folder)
        resolved_mode = "url"
    elif source_mode == "search":
        ensure_search_source(cfg, folder, name)
        files = list_video_files(folder)
        resolved_mode = "search"
    elif source_mode == "auto" and not files:
        if str(cfg.get("url") or "").strip() or (cfg.get("urls") or []):
            ensure_url_sources(cfg, folder, name)
            files = list_video_files(folder)
            resolved_mode = "url"
        if not files:
            try:
                ensure_search_source(cfg, folder, name)
                files = list_video_files(folder)
                resolved_mode = "search"
            except Exception as exc:
                log(f"Celebrity online search lá»—i: {exc}")

    if not files:
        if bool(cfg.get("required", False)):
            raise RuntimeError(
                f"KhÃ´ng cÃ³ celebrity video cho {name}. Folder: {folder}\n"
                "V2.5 Ä‘Ã£ thá»­ theo source_mode. Vá»›i Google search hÃ£y thÃªm SERPER_API_KEY vÃ o .env."
            )
        return None

    min_d = max(0.5, float(cfg.get("duration_min", 3.0)))
    max_d = max(min_d, float(cfg.get("duration_max", 5.0)))
    avoid_recent = max(0, int(cfg.get("avoid_recent", 50) or 0))
    face_verify = bool(cfg.get("face_verify", True))
    face_min_score = float(cfg.get("face_min_score", 35.0) or 35.0)
    face_min_hits = max(1, int(cfg.get("face_min_hits", 3) or 3))

    candidates: list[tuple[Path, float]] = []
    for pth in files:
        try:
            d = ffprobe_duration(pth)
            if d >= max(1.0, min_d):
                candidates.append((pth, d))
        except Exception:
            continue
    if not candidates:
        if bool(cfg.get("required", False)):
            raise RuntimeError(f"KhÃ´ng cÃ³ clip celebrity há»£p lá»‡ trong {folder}")
        return None

    recent_sigs = {str(x.get("signature") or "") for x in celebrity_history()[-avoid_recent:]} if avoid_recent else set()
    ranked: list[dict[str, Any]] = []

    # V2.5: scan every cached source and select a real talking-head window.
    # This rejects finance graphics, logos, B-roll and intro slates that contain no clear face.
    for src, source_duration in candidates:
        duration = min(random.uniform(min_d, max_d), source_duration)
        sidecar = src.with_suffix(src.suffix + ".json")
        source_meta = read_json(sidecar, {}) if sidecar.exists() else {}

        if face_verify:
            try:
                start_time, verify_score, hits, consistency = best_celebrity_talking_window(src, duration, cfg)
            except Exception as exc:
                log(f"Face verify bá» {src.name}: {exc}")
                continue
            log(
                f"Face scan {src.name}: start={start_time:.1f}s | score={verify_score:.1f} | "
                f"face_hits={hits}/4"
            )
            if hits < face_min_hits or verify_score < face_min_score:
                log(f"Bá»Ž {src.name}: khÃ´ng tháº¥y talking-head Ä‘á»§ rÃµ.")
                continue
        else:
            start_padding = max(0.0, float(cfg.get("start_padding", 0.25)))
            max_start = max(0.0, source_duration - duration - start_padding)
            start_time = random.uniform(start_padding, max_start) if max_start > start_padding else 0.0
            verify_score, hits, consistency = 0.0, 0, 0.0

        sig = segment_signature(src, start_time, duration)
        item = {
            "source": src,
            "source_duration": source_duration,
            "duration": duration,
            "start_time": start_time,
            "name": name,
            "audio_mode": "visual_only",
            "source_mode": resolved_mode,
            "signature": sig,
            "source_meta": source_meta if isinstance(source_meta, dict) else {},
            "face_verify_score": round(float(verify_score), 3),
            "face_hits": int(hits),
            "face_consistency": round(float(consistency), 3),
            "recent_penalty": 25.0 if sig in recent_sigs else 0.0,
        }
        ranked.append(item)

    if not ranked:
        if bool(cfg.get("required", False)):
            raise RuntimeError(
                f"ÄÃ£ táº£i celebrity source nhÆ°ng khÃ´ng tÃ¬m tháº¥y Ä‘oáº¡n 3-5s cÃ³ máº·t ngÆ°á»i rÃµ cho {name}. "
                "HÃ£y tÄƒng search_download_count hoáº·c Ä‘á»•i search_query."
            )
        return None

    ranked.sort(
        key=lambda x: float(x.get("face_verify_score") or 0.0) - float(x.get("recent_penalty") or 0.0),
        reverse=True,
    )
    best = ranked[0]
    log(
        f"CHá»ŒN celebrity hook: {Path(best['source']).name} | start={best['start_time']:.1f}s | "
        f"face_score={best.get('face_verify_score')} | audio=MUTED"
    )
    return best

def extract_or_make_hook_audio(src: Path, start: float, duration: float, dest: Path) -> None:
    if ffprobe_has_audio(src):
        run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(src),
                "-t",
                f"{duration:.3f}",
                "-vn",
                "-af",
                "aresample=48000",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(dest),
            ]
        )
    else:
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-t",
                f"{duration:.3f}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(dest),
            ]
        )


def concat_audio(first: Path, second: Path, dest: Path) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(first),
            "-i",
            str(second),
            "-filter_complex",
            "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a0];"
            "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a1];"
            "[a0][a1]concat=n=2:v=0:a=1[a]",
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(dest),
        ]
    )


def burn_and_mux(
    silent_video: Path,
    voice_audio: Path,
    ass_subtitles: Path,
    final_path: Path,
    music_path: Path | None,
    music_volume: float,
) -> None:
    ass_rel = ass_subtitles.resolve().relative_to(ROOT).as_posix()
    vf = f"ass={ass_rel}"

    if music_path and music_path.exists():
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(silent_video),
                "-i",
                str(voice_audio),
                "-stream_loop",
                "-1",
                "-i",
                str(music_path),
                "-filter_complex",
                f"[1:a]volume=1.0[voice];[2:a]volume={music_volume}[music];"
                "[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-vf",
                vf,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(final_path),
            ]
        )
    else:
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(silent_video),
                "-i",
                str(voice_audio),
                "-vf",
                vf,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(final_path),
            ]
        )



def split_reference_durations(total: float, count: int) -> list[float]:
    count = max(1, int(count))
    if count == 1:
        return [total]
    base = total / count
    out = [base] * count
    out[-1] += total - sum(out)
    return out


def pick_dynamic_broll(
    query: str,
    duration: float,
    pexels_key: str,
    pixabay_key: str,
    used_ids: set[str],
    width: int,
    height: int,
    min_motion_score: float,
    attempts: int,
    darken: float,
    idx: int,
) -> tuple[Path, dict[str, Any]]:
    target_aspect = width / height
    orientation = "landscape" if width >= height else "portrait"
    last_item: dict[str, Any] | None = None
    best: tuple[float, Path, dict[str, Any], float] | None = None

    for attempt in range(1, max(1, attempts) + 1):
        item = get_stock_video(
            query,
            pexels_key,
            pixabay_key,
            used_ids,
            orientation=orientation,
            target_aspect=target_aspect,
        )
        used_ids.add(item["id"])
        last_item = item

        src = WORK_DIR / f"source_{idx:03d}_{attempt:02d}.mp4"
        log(f"  candidate {attempt}: {item['provider']} {item['id']} -> táº£i")
        try:
            download_file(item["download_url"], src)
        except Exception as exc:
            log(f"  candidate táº£i tháº¥t báº¡i -> Ä‘á»•i clip khÃ¡c: {exc}")
            continue

        try:
            start, score = best_motion_window(src, duration, trials=5)
        except Exception as exc:
            log(f"  motion scan lá»—i: {exc}")
            score, start = 0.0, 0.0

        log(f"  motion={score:.2f} | start={start:.2f}s")
        if best is None or score > best[0]:
            best = (score, src, item, start)

        if score >= min_motion_score:
            dst = WORK_DIR / f"clip_{idx:03d}.mp4"
            process_clip(
                src, dst, duration, darken,
                start_time=start, loop=False,
                width=width, height=height,
            )
            item = {**item, "motion_score": round(score, 3), "start_time": round(start, 3)}
            return dst, item

    if best is None:
        raise RuntimeError(f"KhÃ´ng tÃ¬m Ä‘Æ°á»£c B-roll cho {query!r}")

    # Use the best candidate only as a fallback, but log the low motion.
    score, src, item, start = best
    log(f"  Cáº¢NH BÃO: khÃ´ng Ä‘áº¡t motion_min={min_motion_score:.2f}; dÃ¹ng clip tá»‘t nháº¥t motion={score:.2f}")
    dst = WORK_DIR / f"clip_{idx:03d}.mp4"
    process_clip(
        src, dst, duration, darken,
        start_time=start, loop=False,
        width=width, height=height,
    )
    item = {**item, "motion_score": round(score, 3), "start_time": round(start, 3)}
    return dst, item

def main() -> None:
    load_dotenv(ENV_FILE)
    require_binary("ffmpeg")
    require_binary("ffprobe")

    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(exist_ok=True)

    pexels_key = os.getenv("PEXELS_API_KEY", "").strip()
    pixabay_key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not pexels_key and not pixabay_key:
        raise RuntimeError(
            "Thiáº¿u API key. Äiá»n PEXELS_API_KEY hoáº·c PIXABAY_API_KEY trong .env."
        )

    voice = os.getenv("TTS_VOICE", "vi-VN-NamMinhNeural")
    rate = os.getenv("TTS_RATE", "+0%")
    tts_mode = os.getenv("TTS_MODE", "auto")
    tts_retries = int(os.getenv("TTS_RETRIES", "3"))
    tts_retry_delay = float(os.getenv("TTS_RETRY_DELAY", "3"))
    fallback_voices = [
        x.strip()
        for x in os.getenv("TTS_FALLBACK_VOICES", "vi-VN-HoaiMyNeural").split(",")
        if x.strip()
    ]
    darken = float(os.getenv("BROLL_BRIGHTNESS", "-0.08"))
    celebrity_brightness = float(os.getenv("CELEBRITY_BRIGHTNESS", "-0.03"))
    music_volume = float(os.getenv("MUSIC_VOLUME", "0.08"))
    font_name = os.getenv("SUBTITLE_FONT", "Arial")
    font_size = int(os.getenv("SUBTITLE_FONT_SIZE", "72"))
    margin_v = int(os.getenv("SUBTITLE_MARGIN_V", "0"))
    alignment = int(os.getenv("SUBTITLE_ALIGNMENT", "5"))
    subtitle_max_words = int(os.getenv("SUBTITLE_MAX_WORDS", "7"))
    subtitle_max_chars = int(os.getenv("SUBTITLE_MAX_CHARS", "38"))
    random_seed = os.getenv("RANDOM_SEED", "")
    if random_seed:
        random.seed(random_seed)

    data = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    subtitle_style = data.get("subtitle_style") or {}
    font_name = str(subtitle_style.get("font") or font_name)
    font_size = int(subtitle_style.get("font_size") or font_size)
    subtitle_max_words = int(subtitle_style.get("max_words") or subtitle_max_words)
    subtitle_max_chars = int(subtitle_style.get("max_chars") or subtitle_max_chars)
    title = data.get("title", "test-video")
    narration = (data.get("narration") or "").strip()
    narration_segments = data.get("narration_segments") or []
    voice_cfg = data.get("voice") or {}
    narration_language = str(voice_cfg.get("language") or data.get("narration_language") or "vi-VN").strip()
    subtitle_language = str(data.get("subtitle_language") or "vi-VN").strip()

    # IMPORTANT: when narration is Chinese, ignore the old Vietnamese TTS_VOICE in .env.
    if narration_language.lower().startswith("zh"):
        voice = str(voice_cfg.get("edge_voice") or os.getenv("ZH_TTS_VOICE", "zh-CN-YunxiNeural")).strip()
        rate = str(voice_cfg.get("rate") or os.getenv("ZH_TTS_RATE", os.getenv("TTS_RATE", "+0%"))).strip()
        fallback_voices = [
            str(x).strip()
            for x in (voice_cfg.get("fallback_voices") or os.getenv(
                "ZH_TTS_FALLBACK_VOICES",
                "zh-CN-YunyangNeural,zh-CN-XiaoxiaoNeural"
            ).split(","))
            if str(x).strip()
        ]

    scenes = data.get("scenes") or []
    style_cfg = data.get("style") or {}
    output_cfg = data.get("output") or {}
    output_width = int(output_cfg.get("width", 1080) or 1080)
    output_height = int(output_cfg.get("height", 1080) or 1080)
    reference_mode = str(style_cfg.get("mode") or "reference").strip().lower() == "reference"
    if not narration and not narration_segments:
        raise RuntimeError("script.json pháº£i cÃ³ narration hoáº·c narration_segments.")
    if not scenes and not reference_mode:
        raise RuntimeError("script.json pháº£i cÃ³ scenes khi style.mode != reference.")

    # 1) Celebrity hook: Æ°u tiÃªn config má»›i. Náº¿u khÃ´ng cÃ³ thÃ¬ váº«n há»— trá»£ hook.path cÅ©.
    celebrity_cfg = data.get("celebrity") or {}
    celebrity = pick_celebrity_segment(celebrity_cfg)

    legacy_hook_cfg = data.get("hook") or {}
    legacy_hook_path = resolve_root_path(str(legacy_hook_cfg.get("path") or "input/hook.mp4"))
    legacy_hook_duration = float(legacy_hook_cfg.get("duration", 0) or 0)
    if celebrity is None and legacy_hook_duration > 0 and legacy_hook_path.exists():
        src_d = ffprobe_duration(legacy_hook_path)
        d = min(legacy_hook_duration, src_d)
        celebrity = {
            "source": legacy_hook_path,
            "source_duration": src_d,
            "duration": d,
            "start_time": 0.0,
            "name": "legacy_hook",
            "audio_mode": "visual_only",
        }

    hook_duration = float(celebrity["duration"]) if celebrity else 0.0
    audio_mode = (celebrity.get("audio_mode") if celebrity else "visual_only") or "visual_only"
    # User-requested production default: NEVER keep celebrity source audio.
    if celebrity and bool(celebrity_cfg.get("mute_original_audio", True)):
        audio_mode = "visual_only"
    if audio_mode not in {"visual_only", "original_then_voice"}:
        raise RuntimeError(
            "celebrity.audio_mode chá»‰ nháº­n: visual_only hoáº·c original_then_voice"
        )

    # 2) Voice + subtitle.
    voice_mp3 = WORK_DIR / "voice.mp3"
    subtitles = WORK_DIR / "voice.srt"
    ass_subtitles = WORK_DIR / "voice.ass"
    if narration_language.lower().startswith("zh") and narration_segments:
        log(f"1/7 Voice TRUNG ({voice}) + subtitle VIá»†T...")
        tts_provider = make_chinese_voice_vietnamese_subs(
            narration_segments,
            voice,
            rate,
            voice_mp3,
            subtitles,
            mode=tts_mode,
            retries=tts_retries,
            retry_delay=tts_retry_delay,
            fallback_voices=fallback_voices,
            max_words=subtitle_max_words,
            max_chars=subtitle_max_chars,
        )
    else:
        log("1/7 Táº¡o voice + subtitle legacy...")
        tts_provider = make_tts(
            narration,
            voice,
            rate,
            voice_mp3,
            subtitles,
            mode=tts_mode,
            retries=tts_retries,
            retry_delay=tts_retry_delay,
            fallback_voices=fallback_voices,
        )
        compact_srt(subtitles, subtitle_max_words, subtitle_max_chars)
    voice_duration = ffprobe_duration(voice_mp3)

    # original_then_voice = clip celebrity nÃ³i trÆ°á»›c, sau Ä‘Ã³ má»›i báº¯t Ä‘áº§u voice narration + subtitle.
    subtitle_offset = hook_duration if celebrity and audio_mode == "original_then_voice" else 0.0
    if subtitle_offset > 0:
        shift_srt(subtitles, subtitle_offset)
    if output_height <= output_width and "SUBTITLE_FONT_SIZE" not in os.environ:
        font_size = 58
    srt_to_ass(
        subtitles, ass_subtitles, font_name, font_size, alignment, margin_v,
        width=output_width, height=output_height,
    )

    total_duration = voice_duration + subtitle_offset
    log(
        f"Voice={voice_duration:.2f}s | hook={hook_duration:.2f}s | "
        f"mode={audio_mode} | finalâ‰ˆ{total_duration:.2f}s"
    )

    # visual_only: hook thay tháº¿ pháº§n Ä‘áº§u cá»§a B-roll vÃ  voice VN cháº¡y tá»« giÃ¢y 0.
    # original_then_voice: hook Ä‘á»©ng riÃªng trÆ°á»›c, B-roll cháº¡y Ä‘á»§ theo toÃ n bá»™ voice VN.
    if celebrity and audio_mode == "visual_only":
        broll_total = max(0.0, voice_duration - hook_duration)
    else:
        broll_total = voice_duration

    scene_durations = distribute_durations(broll_total, scenes)
    processed: list[Path] = []
    sources: list[dict[str, Any]] = []

    # 3) Render celebrity visual.
    if celebrity:
        src = Path(celebrity["source"])
        dst = WORK_DIR / "clip_000_celebrity.mp4"
        log(
            f"2/7 Celebrity: {celebrity['name']} | mode={celebrity.get('source_mode','local')} | {src.name} | "
            f"start={celebrity['start_time']:.2f}s | duration={hook_duration:.2f}s"
        )
        process_clip(
            src,
            dst,
            hook_duration,
            celebrity_brightness,
            start_time=float(celebrity["start_time"]),
            loop=False,
            width=output_width,
            height=output_height,
            crop_bottom_fraction=float(celebrity_cfg.get("crop_bottom_fraction", 0.18) or 0.0),
        )
        processed.append(dst)
    else:
        log("2/7 KhÃ´ng cÃ³ celebrity hook -> báº¯t Ä‘áº§u báº±ng B-roll.")

    # 4) Stock B-roll.
    # REFERENCE mode intentionally mimics the source style:
    # one celebrity hook + only 1-2 continuous, visibly-moving B-roll shots.
    log("3/7 TÃ¬m + táº£i B-roll...")
    used_ids: set[str] = set()

    if reference_mode:
        pool_file = ROOT.parents[2] / "job_types" / "celebrity" / "broll_pool.json"
        extra_pool = []
        if pool_file.exists():
            try:
                extra_pool = json.loads(pool_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        broll_queries = (style_cfg.get("broll_queries") or []) + extra_pool
        if not broll_queries:
            broll_queries = [
                "city night aerial drone traffic cinematic",
                "modern skyscraper skyline timelapse 4k",
                "luxury penthouse interior design sunset",
                "stock market trading charts graph screen",
                "businessman working late office laptop",
                "counting cash money bills financial success",
            ]
        broll_queries = [str(x).strip() for x in broll_queries if str(x).strip()]
        random.shuffle(broll_queries)

        clip_count = max(1, min(12, int(style_cfg.get("broll_clip_count", 5) or 5)))
        motion_min = float(style_cfg.get("motion_min_score", 1.5) or 1.5)
        motion_attempts = max(1, int(style_cfg.get("motion_attempts", 5) or 5))
        durations = split_reference_durations(broll_total, clip_count)

        log(
            f"REFERENCE MODE: {clip_count} B-roll liên tục (từ kho {len(broll_queries)} từ khóa) | "
            f"motion_min={motion_min:.2f} | output={output_width}x{output_height}"
        )

        for idx, duration in enumerate(durations, start=1):
            if duration <= 0.05:
                continue
            query = broll_queries[(idx - 1) % len(broll_queries)]
            log(f"B-roll {idx}/{clip_count}: {query!r} | {duration:.2f}s")
            dst, item = pick_dynamic_broll(
                query=query,
                duration=duration,
                pexels_key=pexels_key,
                pixabay_key=pixabay_key,
                used_ids=used_ids,
                width=output_width,
                height=output_height,
                min_motion_score=motion_min,
                attempts=motion_attempts,
                darken=darken,
                idx=idx,
            )
            processed.append(dst)
            sources.append({"scene": idx, "query": query, **item})
    else:
        scene_durations = distribute_durations(broll_total, scenes)
        for idx, (scene, duration) in enumerate(zip(scenes, scene_durations), start=1):
            if duration <= 0.05:
                continue
            query = (scene.get("query") or "").strip()
            if not query:
                raise RuntimeError(f"Scene {idx} thiáº¿u query.")
            log(f"Scene {idx}: {query!r} | {duration:.2f}s")

            item = get_stock_video(
                query, pexels_key, pixabay_key, used_ids,
                orientation="landscape" if output_width >= output_height else "portrait",
                target_aspect=output_width / output_height,
            )
            used_ids.add(item["id"])
            sources.append({"scene": idx, "query": query, **item})

            src = WORK_DIR / f"source_{idx:03d}.mp4"
            dst = WORK_DIR / f"clip_{idx:03d}.mp4"
            try:
                download_file(item["download_url"], src)
            except Exception as exc:
                raise RuntimeError(f"Táº£i B-roll scene {idx} tháº¥t báº¡i: {exc}") from exc
            start, score = best_motion_window(src, duration, trials=4)
            process_clip(
                src, dst, duration, darken,
                start_time=start, loop=False,
                width=output_width, height=output_height,
            )
            sources[-1]["motion_score"] = round(score, 3)
            sources[-1]["start_time"] = round(start, 3)
            processed.append(dst)

    if not processed:
        raise RuntimeError("KhÃ´ng cÃ³ clip nÃ o Ä‘á»ƒ ghÃ©p.")

    # 5) Timeline video.
    log("4/7 Ná»‘i cÃ¡c clip...")
    silent_video = WORK_DIR / "timeline.mp4"
    concat_clips(processed, silent_video)

    # 6) Audio master.
    master_voice = voice_mp3
    if celebrity and audio_mode == "original_then_voice":
        log("5/7 Giá»¯ tiáº¿ng celebrity á»Ÿ Ä‘áº§u, sau Ä‘Ã³ ná»‘i voice Trung...")
        hook_audio = WORK_DIR / "celebrity_audio.m4a"
        master_voice = WORK_DIR / "master_voice.m4a"
        extract_or_make_hook_audio(
            Path(celebrity["source"]),
            float(celebrity["start_time"]),
            hook_duration,
            hook_audio,
        )
        concat_audio(hook_audio, voice_mp3, master_voice)
    else:
        log("5/7 Celebrity chá»‰ lÃ  hÃ¬nh; voice Trung báº¯t Ä‘áº§u tá»« giÃ¢y 0.")

    # 7) Burn subtitle + audio.
    log("6/7 Burn subtitle + ghÃ©p voice/music...")
    final_path = OUTPUT_DIR / "final.mp4"
    music_path = INPUT_DIR / "music.mp3"
    burn_and_mux(
        silent_video,
        master_voice,
        ass_subtitles,
        final_path,
        music_path if music_path.exists() else None,
        music_volume,
    )

    source_log = OUTPUT_DIR / "sources.json"
    celebrity_log = None
    if celebrity:
        celebrity_log = {
            "name": celebrity["name"],
            "file": str(Path(celebrity["source"]).resolve()),
            "start_time": round(float(celebrity["start_time"]), 3),
            "duration": round(hook_duration, 3),
            "audio_mode": audio_mode,
            "source_mode": celebrity.get("source_mode", "local"),
            "signature": celebrity.get("signature"),
            "source_meta": celebrity.get("source_meta") or {},
        }
    source_log.write_text(
        json.dumps(
            {
                "version": "2.6",
                "title": title,
                "voice_duration": voice_duration,
                "total_duration_expected": total_duration,
                "tts_provider": tts_provider,
                "narration_language": narration_language,
                "subtitle_language": subtitle_language,
                "tts_voice": voice,
                "celebrity": celebrity_log,
                "stock_sources": sources,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if celebrity:
        record_celebrity_use(celebrity)
    log("7/7 XONG")
    log(f"Video: {final_path}")
    log(f"Nguá»“n: {source_log}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nÄÃ£ dá»«ng.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\n[Lá»–I] {exc}", file=sys.stderr)
        raise SystemExit(1)

