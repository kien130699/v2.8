from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    host: str = os.getenv("SERVER_HOST", "127.0.0.1")
    port: int = _int("SERVER_PORT", 8797)
    timezone_name: str = os.getenv("APP_TIMEZONE", "Asia/Ho_Chi_Minh")
    db_path: Path = Path(os.getenv("DB_PATH", str(ROOT / "data" / "factory.db"))).resolve()
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", str(ROOT / "output"))).resolve()
    video_engine_dir: Path = Path(os.getenv("VIDEO_ENGINE_DIR", str(ROOT / "engine_v27"))).resolve()
    engine_python: str = os.getenv("ENGINE_PYTHON", "").strip()
    render_timeout_sec: int = _int("RENDER_TIMEOUT_SEC", 1800)
    scheduler_interval_sec: int = _int("SCHEDULER_INTERVAL_SEC", 10)
    auto_start: bool = _bool("AUTO_START_FACTORY", False)
    default_auto_publish: bool = _bool("AUTO_PUBLISH", False)
    allow_render_without_fb_page: bool = _bool("ALLOW_RENDER_WITHOUT_FB_PAGE", False)
    # 9Router is an OpenAI-compatible local/cloud gateway. Keep generic LLM_*
    # for backward compatibility with V3.0.
    ninerouter_base_url: str = (os.getenv("9ROUTER_BASE_URL") or os.getenv("NINEROUTER_BASE_URL") or "http://127.0.0.1:20128/v1").rstrip("/")
    ninerouter_api_key: str = os.getenv("9ROUTER_API_KEY", os.getenv("NINEROUTER_API_KEY", os.getenv("NINE_ROUTER_API_KEY", "")))
    ninerouter_default_model: str = os.getenv("9ROUTER_DEFAULT_MODEL", os.getenv("NINEROUTER_DEFAULT_MODEL", "cx/gpt-5.4"))
    ninerouter_model_gemini_31: str = os.getenv("NINEROUTER_MODEL_GEMINI_31", "vertex/gemini-3.1-pro-preview")
    ninerouter_model_gemini_35: str = os.getenv("NINEROUTER_MODEL_GEMINI_35", "gemini-3.5")
    ninerouter_model_gemini_36: str = os.getenv("NINEROUTER_MODEL_GEMINI_36", "gemini-3.6")
    ninerouter_model_gpt_54: str = os.getenv("NINEROUTER_MODEL_GPT_54", "cx/gpt-5.4")
    ninerouter_model_gpt_54_gh: str = os.getenv("NINEROUTER_MODEL_GPT_54_GH", "gh/gpt-5.4")
    ninerouter_model_gpt_55: str = os.getenv("NINEROUTER_MODEL_GPT_55", "cx/gpt-5.5")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    fb_graph_version: str = os.getenv("FB_GRAPH_VERSION", "").strip().strip("/")
    request_timeout_sec: int = _int("HTTP_TIMEOUT_SEC", 60)

    @property
    def tz(self) -> tzinfo:
        """Return the configured timezone without letting Windows kill startup.

        On Windows, CPython normally has no system IANA tz database. The
        official ``tzdata`` package is therefore installed by this project.
        We still keep a deterministic fallback for Vietnam so an old venv or
        a temporarily broken dependency install cannot prevent the server
        from starting.
        """
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            normalized = self.timezone_name.strip().lower()
            if normalized in {"asia/ho_chi_minh", "asia/saigon"}:
                return timezone(timedelta(hours=7), name="ICT")
            if normalized in {"utc", "etc/utc", "gmt", "etc/gmt"}:
                return timezone.utc
            # Safe last-resort fallback. This keeps the web server alive;
            # install tzdata or correct APP_TIMEZONE for DST-aware zones.
            return timezone.utc


settings = Settings()
settings.db_path.parent.mkdir(parents=True, exist_ok=True)
settings.output_dir.mkdir(parents=True, exist_ok=True)
