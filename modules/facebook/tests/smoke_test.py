from pathlib import Path
import os
import sys
import tempfile

_tmp = tempfile.TemporaryDirectory(prefix="facebook-smoke-")
os.environ["DB_PATH"] = str(Path(_tmp.name) / "factory.db")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from facebook_app.db import init_db
from facebook_app.repository import create_test_job, dashboard, get_job, list_pages
from facebook_app.services.llm_router import llm_router

init_db()
pages = list_pages()
assert len(pages) == 10, len(pages)
d = dashboard()
assert d["daily_target"] == 20
ids = [x["id"] for x in llm_router.presets()]
assert "cx/gpt-5.4" in ids
assert any("gemini-3.1" in x for x in ids)
job_id = create_test_job(pages[0]["id"])
assert get_job(job_id)["status"] == "TEST_QUEUED"
print("SMOKE OK", len(pages), "pages", d["factory_state"], "presets", len(ids), "test", job_id)
_tmp.cleanup()
