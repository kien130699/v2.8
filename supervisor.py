from __future__ import annotations
import os
import subprocess
import sys
import time
import traceback
import json
import socket
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
SUP_PID = DATA / "v28_supervisor.pid"
CHILD_PID = DATA / "v28_server.pid"
STOP_FLAG = DATA / "v28_stop.flag"
CRASH_LOG = DATA / "server_crash.log"
CONSOLE_LOG = DATA / "server_console.log"

# 0 is intentionally NOT a stop code. Uvicorn sometimes returns 0 after an internal
# lifespan/shutdown path. Unless STOP.bat or Ctrl+C requested it, a clean child exit
# must be supervised just like a crash so the server never silently disappears.
STOP_CODES = {130, -1073741510, 3221225786}
MAX_CRASHES = 5
WINDOW_SEC = 120


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"[{stamp()}] {msg}"
    print(line, flush=True)
    for path in (CRASH_LOG, CONSOLE_LOG):
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def stop_requested() -> bool:
    return STOP_FLAG.exists()



def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def _existing_v28_health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/api/health", timeout=0.8) as r:
            if int(getattr(r, "status", 200)) != 200:
                return None
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        if isinstance(data, dict) and data.get("ok") is True and int(data.get("port") or 0) == int(port) and str(data.get("version") or "").startswith("2.8"):
            return data
    except Exception:
        return None
    return None


def _win_cmdline(pid: int) -> str:
    if os.name != "nt" or pid <= 0:
        return ""
    ps = f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' -ErrorAction SilentlyContinue; if($p){{$p.CommandLine}}"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=4)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _win_port_pid(port: int) -> int | None:
    if os.name != "nt":
        return None
    ps = f"$p=Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if($p){{$p}}"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=4)
        raw = (r.stdout or "").strip().splitlines()
        return int(raw[-1].strip()) if raw and raw[-1].strip().isdigit() else None
    except Exception:
        return None


def _kill_tree(pid: int, reason: str) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=8)
        else:
            os.kill(pid, 15)
        log(f"SINGLE INSTANCE: stopped PID={pid} · {reason}")
        return True
    except Exception as exc:
        log(f"SINGLE INSTANCE: stop PID={pid} failed · {exc}")
        return False


def ensure_single_instance(port: int) -> bool:
    """Ensure only this V2.8 owns port 3000. Never touches 8786/8787.

    On Windows we only kill a listener after /api/health proves it is V2.8, or a
    stale supervisor PID whose command line clearly contains supervisor.py. An
    unrelated app on port 3000 is never killed automatically.
    """
    # First stop a previous supervisor from this family so it cannot resurrect its child.
    if SUP_PID.exists():
        try:
            old = int(SUP_PID.read_text(encoding="ascii").strip())
        except Exception:
            old = 0
        if old and old != os.getpid():
            cmdline = _win_cmdline(old).lower()
            if "supervisor.py" in cmdline:
                _kill_tree(old, "old V2.8 supervisor pid file")
                time.sleep(0.6)

    if not _port_open(port):
        return True

    health = _existing_v28_health(port)
    if not health:
        log(f"PORT CONFLICT: 127.0.0.1:{port} đang bị app KHÁC chiếm. Không kill tự động.")
        return False

    pid = _win_port_pid(port)
    version = str(health.get("version") or "unknown")
    if os.name == "nt" and pid:
        cmdline = _win_cmdline(pid).lower()
        if "run_server.py" in cmdline or "uvicorn" in cmdline or "python" in cmdline:
            _kill_tree(pid, f"old V2.8 listener {version} on port {port}")
        else:
            log(f"PORT CONFLICT: health giống V2.8 {version} nhưng PID={pid} command line không an toàn để kill")
            return False
    else:
        log(f"PORT CONFLICT: phát hiện V2.8 {version} trên {port} nhưng không xác định được PID")
        return False

    deadline = time.time() + 8
    while time.time() < deadline:
        if not _port_open(port):
            return True
        time.sleep(0.25)
    log(f"PORT CONFLICT: port {port} chưa được giải phóng sau khi stop V2.8 cũ")
    return False

def main() -> int:
    STOP_FLAG.unlink(missing_ok=True)
    port = int(os.environ.get("V28_PORT", "3000"))
    if port != 3000:
        log(f"WARNING: V2.8 isolation expects port 3000, current V28_PORT={port}")
    if not ensure_single_instance(port):
        log("Supervisor HALTED due to port conflict; giữ terminal để xem lỗi. STOP.bat/Ctrl+C để thoát.")
        return _halt_after_fatal(2)
    SUP_PID.write_text(str(os.getpid()), encoding="ascii")
    log(f"SINGLE INSTANCE READY · root={ROOT} · port={port} · pid={os.getpid()}")
    crashes: list[float] = []
    try:
        while True:
            if stop_requested():
                log("STOP flag present -> supervisor exits")
                return 0
            cmd = [sys.executable, str(ROOT / "run_server.py")]
            env = os.environ.copy()
            env["V28_SUPERVISED"] = "1"
            log("START child server")
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
            started_at = time.time()
            unresponsive_count = 0
            code = None
            try:
                while proc.poll() is None:
                    try:
                        code = proc.wait(timeout=10)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if stop_requested():
                        log("STOP flag requested -> terminate child")
                        try:
                            proc.terminate()
                            code = proc.wait(timeout=5)
                        except Exception:
                            proc.kill()
                            code = proc.wait()
                        break
                    # After startup grace period of 25s, verify active HTTP health
                    if time.time() - started_at > 25:
                        h = _existing_v28_health(port)
                        if h is None:
                            unresponsive_count += 1
                            log(f"HEALTH WARNING: server unresponsive {unresponsive_count}/4 on port {port}")
                            if unresponsive_count >= 4:
                                log(f"HEALTH CHECK FAILED: server event loop frozen (PID={proc.pid}); killing to restart...")
                                _kill_tree(proc.pid, "Health check timeout / frozen event loop")
                                code = proc.poll() or 1
                                break
                        else:
                            unresponsive_count = 0
                if code is None:
                    code = proc.poll()
            except KeyboardInterrupt:
                log("CTRL+C -> stop child")
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return 0
            finally:
                CHILD_PID.unlink(missing_ok=True)

            if stop_requested():
                log(f"Server stopped by STOP request · code={code}")
                return 0
            if code in STOP_CODES:
                log(f"Server stopped by console signal · code={code}")
                return 0

            now = time.time()
            crashes = [x for x in crashes if now - x < WINDOW_SEC]
            crashes.append(now)
            kind = "CLEAN child exit without STOP request" if code == 0 else "UNEXPECTED server exit"
            log(f"{kind} · code={code} · restart {len(crashes)}/{MAX_CRASHES}")
            if len(crashes) >= MAX_CRASHES:
                log("Crash-loop guard reached; server HALTED but supervisor stays alive so the terminal/log never disappears.")
                log("Check data/server_console.log and data/server_crash.log. Use STOP.bat or Ctrl+C to exit.")
                try:
                    while not stop_requested():
                        time.sleep(2)
                except KeyboardInterrupt:
                    log("CTRL+C while HALTED -> supervisor exits")
                    return code or 1
                log("STOP flag present while HALTED -> supervisor exits")
                return 0
            log("Restart server in 2 seconds...")
            time.sleep(2)
    finally:
        CHILD_PID.unlink(missing_ok=True)
        SUP_PID.unlink(missing_ok=True)
        # Keep STOP_FLAG only long enough for both processes to see it.
        STOP_FLAG.unlink(missing_ok=True)


def _halt_after_fatal(code: int = 1) -> int:
    log("Supervisor HALTED after fatal error; terminal/process stays alive until STOP.bat or Ctrl+C.")
    try:
        while not stop_requested():
            time.sleep(2)
    except KeyboardInterrupt:
        log("CTRL+C while supervisor HALTED -> exit")
        return 130
    log("STOP flag present while supervisor HALTED -> exit")
    return code


def guarded_main() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        log("CTRL+C outside child wait -> supervisor exits")
        return 130
    except BaseException:
        tb = traceback.format_exc()
        log("SUPERVISOR FATAL EXCEPTION\n" + tb)
        return _halt_after_fatal(1)


if __name__ == "__main__":
    raise SystemExit(guarded_main())
