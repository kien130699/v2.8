from __future__ import annotations
import faulthandler
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
import uvicorn

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
CRASH_LOG = DATA / "server_crash.log"
CONSOLE_LOG = DATA / "server_console.log"
from core.env_loader import load_project_env
load_project_env()


class TeeStream:
    """Mirror console output to a persistent UTF-8 log without hiding the terminal."""
    def __init__(self, primary, path: Path):
        self.primary = primary
        self._file = path.open("a", encoding="utf-8", buffering=1)
        self.encoding = getattr(primary, "encoding", "utf-8") or "utf-8"

    def write(self, text):
        text = str(text)
        try:
            self.primary.write(text)
        except Exception:
            pass
        try:
            self._file.write(text)
        except Exception:
            pass
        return len(text)

    def flush(self):
        try:
            self.primary.flush()
        except Exception:
            pass
        try:
            self._file.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return bool(self.primary.isatty())
        except Exception:
            return False

    def fileno(self):
        return self.primary.fileno()


def _append_crash(text: str) -> None:
    try:
        with CRASH_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] {text}\n")
    except Exception:
        pass


def _run_uvicorn() -> int:
    # Persist everything the user would normally lose when a terminal disappears.
    original_out, original_err = sys.stdout, sys.stderr
    tee_out = tee_err = None
    try:
        tee_out = TeeStream(original_out, CONSOLE_LOG)
        tee_err = TeeStream(original_err, CONSOLE_LOG)
        sys.stdout, sys.stderr = tee_out, tee_err
        print(f"\n===== V2.8.6.0 SERVER START {datetime.now().astimezone().isoformat(timespec='seconds')} pid={os.getpid()} · FLOW_WORKER>=14.8.0 =====", flush=True)
    except Exception:
        sys.stdout, sys.stderr = original_out, original_err

    crash_fp = None
    try:
        crash_fp = CRASH_LOG.open("a", encoding="utf-8")
        faulthandler.enable(file=crash_fp, all_threads=True)
    except Exception:
        crash_fp = None
    try:
        uvicorn.run(
            "master.app:app",
            host=os.getenv("V28_HOST", "127.0.0.1"),
            port=int(os.getenv("V28_PORT", "3000")),
            reload=False,
        )
        return 0
    except Exception:
        text = traceback.format_exc()
        print(text, flush=True)
        _append_crash("PYTHON EXCEPTION\n" + text)
        return 1
    finally:
        try:
            if crash_fp:
                crash_fp.flush(); crash_fp.close()
        except Exception:
            pass
        try:
            if tee_out: tee_out.flush()
            if tee_err: tee_err.flush()
        except Exception:
            pass


if __name__ == "__main__":
    # Users often run `py run_server.py` directly. Make that path supervised too.
    # supervisor.py marks only its child with V28_SUPERVISED=1 to avoid recursion.
    if os.getenv("V28_SUPERVISED", "") != "1":
        from supervisor import guarded_main as supervisor_main
        raise SystemExit(supervisor_main())
    raise SystemExit(_run_uvicorn())
