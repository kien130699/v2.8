import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
persona_file = (ROOT / "data" / "job_assets" / "2_1" / "persona_pink_dress.jpg").resolve()
assert persona_file.exists(), f"File {persona_file} not found"

# 1. Update v28.sqlite3
c1 = sqlite3.connect(ROOT / "data" / "v28.sqlite3")
c1.row_factory = sqlite3.Row
r = c1.execute("SELECT config_json FROM job_instances WHERE id='2.1'").fetchone()
cfg = json.loads(r["config_json"]) if r and r["config_json"] else {}
cfg["persona_path"] = str(persona_file)
c1.execute("UPDATE job_instances SET config_json=?, updated_at=datetime('now') WHERE id='2.1'", (json.dumps(cfg, ensure_ascii=False),))
c1.commit()

# 2. Update flow_content factory.sqlite3
c2 = sqlite3.connect(ROOT / "modules" / "flow_content" / "data" / "factory.sqlite3")
c2.execute("UPDATE page_profiles SET persona_path=?, updated_at=datetime('now') WHERE id='v28_2_1' OR id='2.1'", (str(persona_file),))
c2.commit()

print("CONFIRMED PERSONA FOR PAGE MINH ANH (JOB 2.1) SET TO:", persona_file)
