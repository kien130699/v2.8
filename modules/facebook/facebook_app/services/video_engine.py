from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Awaitable, Callable

from facebook_app.config import settings
from core.env_loader import build_subprocess_env, env_status

LogFn = Callable[[str, str], Awaitable[None]]


class VideoEngine:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    def _python(self) -> str:
        if settings.engine_python:
            return settings.engine_python
        return sys.executable

    async def render(self, job_id: str, script: dict, log: LogFn) -> tuple[Path, Path | None]:
        async with self._lock:
            engine = settings.video_engine_dir
            app_py = engine / "app.py"
            if not app_py.exists():
                raise RuntimeError(f"Không thấy video engine: {app_py}")
            (engine / "output").mkdir(exist_ok=True)
            (engine / "work").mkdir(exist_ok=True)
            (engine / "data").mkdir(exist_ok=True)
            script_path = engine / "script.json"
            backup = engine / "script.factory.backup.json"
            if script_path.exists():
                try:
                    shutil.copy2(script_path, backup)
                except Exception:
                    pass
            script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")

            # Reuse the server .env if the engine does not have one. Never print secrets.
            server_env = settings.root / ".env"
            engine_env = engine / ".env"
            if server_env.exists() and not engine_env.exists():
                shutil.copy2(server_env, engine_env)

            key_state = env_status("PEXELS_API_KEY", "PIXABAY_API_KEY", "SERPER_API_KEY", "9ROUTER_API_KEY")
            safe_bits = [f"{k}={'OK' if v.get('configured') else 'MISS'}:{v.get('source')}" for k, v in key_state.items()]
            await log("INFO", f"Engine start: {engine} · env " + " · ".join(safe_bits))
            child_env = build_subprocess_env({"V28_CHILD_ENGINE": "1"})
            proc = await asyncio.create_subprocess_exec(
                self._python(), str(app_py),
                cwd=str(engine),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=child_env,
            )
            assert proc.stdout is not None
            tail: list[str] = []
            try:
                async with asyncio.timeout(settings.render_timeout_sec):
                    while True:
                        line = await proc.stdout.readline()
                        if not line:
                            break
                        text = line.decode("utf-8", errors="replace").rstrip()
                        if text:
                            tail.append(text)
                            if len(tail) > 80:
                                del tail[:-80]
                            await log("ENGINE", text)
                    code = await proc.wait()
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"Render timeout > {settings.render_timeout_sec}s")
            if code != 0:
                # Do not collapse the real engine failure into a useless `exit code 1`.
                # app.py prints the Python traceback/message to stdout (stderr is merged),
                # so surface the last meaningful line directly to V2.8.
                meaningful = [
                    x.strip() for x in tail
                    if x.strip() and not x.lstrip().startswith(("Traceback (most recent call last):", "File \""))
                ]
                cause = meaningful[-1] if meaningful else "không có stderr/stdout chi tiết"
                # Trim Python's common exception prefix but preserve the actual message.
                if ": " in cause and cause.split(": ", 1)[0] in {
                    "RuntimeError", "ValueError", "KeyError", "OSError", "IOError",
                    "FileNotFoundError", "CalledProcessError", "HTTPError", "RequestException"
                }:
                    cause = cause.split(": ", 1)[1]
                raise RuntimeError(f"Video engine lỗi: {cause} [exit={code}]")

            final_src = engine / "output" / "final.mp4"
            sources_src = engine / "output" / "sources.json"
            if not final_src.exists() or final_src.stat().st_size < 1024:
                raise RuntimeError("Engine chạy xong nhưng không có output/final.mp4 hợp lệ")

            day = script.get("factory_meta", {}).get("business_date") or "undated"
            out_dir = settings.output_dir / day / job_id
            out_dir.mkdir(parents=True, exist_ok=True)
            final_dst = out_dir / "final.mp4"
            shutil.copy2(final_src, final_dst)
            sources_dst: Path | None = None
            if sources_src.exists():
                sources_dst = out_dir / "sources.json"
                shutil.copy2(sources_src, sources_dst)
            (out_dir / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
            await log("INFO", f"Render OK: {final_dst}")
            return final_dst, sources_dst


video_engine = VideoEngine()
