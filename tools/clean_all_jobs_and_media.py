from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "v28.sqlite3"


def clean_database():
    if not DB_PATH.exists():
        print("📁 Database file không tồn tại.")
        return
    print("🧹 Đang dọn dẹp toàn bộ dữ liệu Jobs & Media tracking trong SQLite...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    tables = [
        "runs",
        "run_steps",
        "scene_checkpoints",
        "media_tracking",
        "publish_jobs",
        "logs",
    ]
    for table in tables:
        try:
            cursor.execute(f"DELETE FROM {table}")
            print(f"  ✓ Đã xóa dữ liệu bảng {table}")
        except Exception as e:
            print(f"  ⚠️ Bảng {table}: {e}")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()


def clean_folders():
    print("🧹 Đang dọn dẹp các thư mục làm việc, output & media cache...")
    dirs_to_clean = [
        ROOT / "modules" / "facebook" / "engine_v27" / "work",
        ROOT / "modules" / "facebook" / "engine_v27" / "output",
        ROOT / "modules" / "facebook" / "engine_v27" / "input" / "celebrity",
        ROOT / "data" / "FlowAutomationServer",
        ROOT / "data" / "FlowPairAuto",
    ]
    for d in dirs_to_clean:
        if d.exists():
            for item in d.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                except Exception as e:
                    print(f"  ⚠️ Không thể xóa {item}: {e}")
            print(f"  ✓ Đã làm sạch thư mục: {d.relative_to(ROOT)}")

    # Clean TEST_* evidence folders inside data
    data_dir = ROOT / "data"
    if data_dir.exists():
        for item in data_dir.glob("TEST_*"):
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                print(f"  ✓ Đã xóa evidence: {item.name}")
            except Exception:
                pass


def main():
    clean_database()
    clean_folders()
    print("\n✅ HOÀN TẤT DỌN DẸP TOÀN BỘ JOBS CŨ VÀ MEDIA CACHE!")


if __name__ == "__main__":
    main()
