import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from unittest.mock import MagicMock
from core.job_manager import JobManager
from core import db

db.init_db()
jm = JobManager(MagicMock(), MagicMock())
jm.load_plugins()
templates = jm.templates()
print(f"=== ĐÃ LOAD THÀNH CÔNG {len(templates)} JOB TEMPLATES ===")
for t in templates:
    print(f"- Job {t['id']}: {t['name']} (slug: {t['slug']}) | engine: {t['engine']}")
    if t['id'] == '6':
        print(f"  + Top Item: {t['defaults'].get('top_item')}")
        print(f"  + Video Model: {t['defaults'].get('video_model')}")
        print(f"  + Mô tả: {t['description'][:110]}...")

