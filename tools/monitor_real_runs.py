from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

BASE = "http://127.0.0.1:3000"

def run_monitor():
    r3_id = "run_3_11_021a6986bb2f"
    r2_id = "run_2_1_7ac075fdfc1d"
    
    print(f"Monitoring runs: Job 3={r3_id}, Job 2={r2_id}", flush=True)
    start_ts = time.time()
    
    while time.time() - start_ts < 1200:  # 20 min max
        time.sleep(10)
        try:
            q = requests.get(f"{BASE}/api/flow", timeout=5).json().get("queue", {})
            ext = q.get("extension") or {}
            rt = ext.get("runtime") or {}
            
            r3 = requests.get(f"{BASE}/api/runs/{r3_id}", timeout=5).json()
            r2 = requests.get(f"{BASE}/api/runs/{r2_id}", timeout=5).json()
            
            s3 = r3.get("status")
            s2 = r2.get("status")
            
            p_label = rt.get("progressLabel") or "IDLE"
            elapsed = int(time.time() - start_ts)
            print(f"[{elapsed}s] Flow: {p_label} | Job 3: {s3} | Job 2: {s2}", flush=True)
            
            if s3 in {"done", "done_no_pages", "published"} and s2 in {"done", "done_no_pages", "published"}:
                print("\n=== BOTH RUNS COMPLETED SUCCESSFULLY! ===", flush=True)
                break
        except Exception as e:
            print(f"Error checking: {e}", flush=True)

if __name__ == "__main__":
    run_monitor()
