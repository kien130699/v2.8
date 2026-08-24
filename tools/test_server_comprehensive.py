from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from core import db, facebook, env_loader, server_features
from master.app import app, broker, manager, engine

def run_tests() -> int:
    print("==================================================")
    print(" BAT DAU KIEM TRA TOAN DIEN SERVER V2.8 FACEBOOK  ")
    print("==================================================")

    with tempfile.TemporaryDirectory(prefix="v28_server_test_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_file = tmp_path / "test_v28.sqlite3"
        original_db = db.DB_PATH
        db.DB_PATH = db_file

        try:
            db.init_db()
            manager.load_plugins()

            passed = 0
            total = 0

            def test_case(name: str, fn):
                nonlocal passed, total
                total += 1
                try:
                    fn()
                    passed += 1
                    print(f" [PASS] {name}")
                except Exception as e:
                    print(f" [FAIL] {name}: {type(e).__name__} - {e}")
                    import traceback
                    traceback.print_exc()

            with TestClient(app) as client:

                # 1. Base / Static / Health
                def test_health():
                    r = client.get("/api/health")
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    d = r.json()
                    assert d.get("ok") is True, d
                    assert "version" in d
                    assert "isolation" in d
                    assert d["isolation"].get("server_port") == 3000
                test_case("1. Health Endpoint (/api/health)", test_health)

                def test_status():
                    r = client.get("/api/status")
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    d = r.json()
                    assert d.get("ok") is True, d
                    assert "flow" in d
                    assert "facebook" in d
                    assert "jobs" in d
                test_case("2. Status Endpoint (/api/status)", test_status)

                def test_diagnostics():
                    r = client.get("/api/diagnostics")
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    d = r.json()
                    assert d.get("ok") is True, d
                    assert "db" in d
                    assert "flow" in d
                    assert "queuedRuns" in d
                test_case("3. Diagnostics Endpoint (/api/diagnostics)", test_diagnostics)

                def test_logs():
                    db.log_event("Test log entry for server test", level="INFO", kind="test")
                    r = client.get("/api/logs?limit=10")
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    logs = r.json()
                    assert isinstance(logs, list)
                    assert any("Test log entry for server test" in l.get("message", "") for l in logs)
                test_case("4. Logs Endpoint (/api/logs)", test_logs)

                def test_static_files():
                    r_root = client.get("/")
                    assert r_root.status_code == 200
                    assert "text/html" in r_root.headers.get("content-type", "")

                    r_appjs = client.get("/static/app.js")
                    assert r_appjs.status_code == 200

                    r_css = client.get("/static/style.css")
                    assert r_css.status_code == 200

                    r_fav = client.get("/favicon.ico")
                    assert r_fav.status_code == 200
                test_case("5. Static Files & Root HTML (/, /static/app.js, /static/style.css, /favicon.ico)", test_static_files)

                # 2. Templates
                def test_templates():
                    r = client.get("/api/job-templates")
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    templates = r.json()
                    t_ids = {str(t.get("id")) for t in templates}
                    assert {"1", "2", "3", "4"}.issubset(t_ids), f"Missing templates: {t_ids}"
                test_case("6. Job Templates Listing (/api/job-templates)", test_templates)

                # 3. Flow Settings
                def test_flow_settings():
                    r = client.get("/api/flow")
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    flow = r.json()
                    assert "settings" in flow
                    assert "models" in flow

                    patch_res = client.patch("/api/flow/settings", json={
                        "imageConcurrency": 8,
                        "videoConcurrency": 3,
                        "videoDuration": "8s",
                        "scriptAiModel": "ag/gemini-3.1-pro-high"
                    })
                    assert patch_res.status_code == 200, f"HTTP {patch_res.status_code}: {patch_res.text}"
                    updated = patch_res.json()
                    assert updated.get("imageConcurrency") == 8
                    assert updated.get("videoConcurrency") == 3
                test_case("7. Flow Settings Read & Patch (/api/flow, /api/flow/settings)", test_flow_settings)

                # 4. Job CRUD (All 4 Templates)
                created_job_ids = []

                def test_job_crud_template_1():
                    r = client.post("/api/jobs", json={
                        "template_id": "1",
                        "name": "Celebrity Test Job",
                        "config": {"theme": "life"},
                        "page_ids": []
                    })
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    job = r.json().get("job")
                    assert job and job.get("template_id") == "1"
                    jid = job["id"]
                    created_job_ids.append(jid)

                    # Get
                    g = client.get(f"/api/jobs/{jid}")
                    assert g.status_code == 200
                    assert g.json().get("name") == "Celebrity Test Job"

                    # Patch
                    p = client.patch(f"/api/jobs/{jid}", json={
                        "name": "Celebrity Test Job Updated",
                        "config": {"theme": "life"},
                        "schedule": {"enabled": True, "mode": "interval", "interval_minutes": 60}
                    })
                    assert p.status_code == 200, f"HTTP {p.status_code}: {p.text}"
                    assert p.json().get("job", {}).get("name") == "Celebrity Test Job Updated"
                test_case("8. Job CRUD: Template 1 - Celebrity", test_job_crud_template_1)

                def test_job_crud_template_2():
                    r = client.post("/api/jobs", json={
                        "template_id": "2",
                        "name": "Beauty Test Job",
                        "config": {"body_preset": "glam_curvy", "sexiness_level": 80},
                        "page_ids": []
                    })
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    job = r.json().get("job")
                    jid = job["id"]
                    created_job_ids.append(jid)
                    
                    # Asset Upload (Persona)
                    img_buf = io.BytesIO()
                    Image.new("RGB", (64, 64), (200, 100, 100)).save(img_buf, format="JPEG")
                    img_bytes = img_buf.getvalue()

                    up_res = client.post(
                        f"/api/jobs/{jid}/assets/persona_path",
                        files={"file": ("persona.jpg", img_bytes, "image/jpeg")}
                    )
                    assert up_res.status_code == 200, f"HTTP {up_res.status_code}: {up_res.text}"
                    assert up_res.json().get("ok") is True

                    # Get Asset
                    get_asset = client.get(f"/api/jobs/{jid}/assets/persona_path")
                    assert get_asset.status_code == 200
                    assert len(get_asset.content) > 0

                    # Clone Job
                    clone_res = client.post(f"/api/jobs/{jid}/clone", json={"name": "Beauty Cloned"})
                    assert clone_res.status_code == 200, f"HTTP {clone_res.status_code}: {clone_res.text}"
                    cloned_job = clone_res.json().get("job")
                    assert cloned_job and cloned_job["id"] != jid
                    created_job_ids.append(cloned_job["id"])
                test_case("9. Job CRUD: Template 2 - Beauty (with Persona Asset & Clone)", test_job_crud_template_2)

                def test_job_crud_template_3():
                    r = client.post("/api/jobs", json={
                        "template_id": "3",
                        "name": "Parenting Test Job",
                        "config": {"topic": "Tập cho bé ăn dặm", "tone": "ấm áp"},
                        "page_ids": []
                    })
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    job = r.json().get("job")
                    created_job_ids.append(job["id"])
                test_case("10. Job CRUD: Template 3 - Parenting", test_job_crud_template_3)

                def test_job_crud_template_4():
                    r = client.post("/api/jobs", json={
                        "template_id": "4",
                        "name": "Shopee Keyword Job",
                        "config": {
                            "keyword": "do choi lego",
                            "product_count": 2,
                            "sub_id": "legotest",
                            "product_video_mode": "one_product_per_video"
                        },
                        "page_ids": []
                    })
                    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                    job = r.json().get("job")
                    created_job_ids.append(job["id"])
                test_case("11. Job CRUD: Template 4 - Shopee Keyword", test_job_crud_template_4)

                # 5. Facebook Pages Management
                def test_facebook_pages():
                    # List
                    r_list = client.get("/api/facebook/pages")
                    assert r_list.status_code == 200

                    # Save Page via direct core call or POST
                    facebook.save_page("100123456789012", "Fanpage Test Server", "EAA_TEST_TOKEN_1234567890", [])
                    
                    # Verify in list
                    r_check = client.get("/api/facebook/pages")
                    pages = r_check.json()
                    assert any(p["id"] == "100123456789012" for p in pages)

                    # List Publish Jobs
                    r_pub = client.get("/api/facebook/publish-jobs")
                    assert r_pub.status_code == 200

                    # Delete Page
                    r_del = client.delete("/api/facebook/pages/100123456789012")
                    assert r_del.status_code == 200
                test_case("12. Facebook Pages & Publish Queue (/api/facebook/pages)", test_facebook_pages)

                # 6. Runs & Orchestrator Features
                def test_runs_and_steps():
                    # List runs
                    r_runs = client.get("/api/runs")
                    assert r_runs.status_code == 200

                    # Create a test run row directly for step/checkpoint testing
                    ts = db.now_iso()
                    test_run_id = "run_test_step_001"
                    with db.connect() as c:
                        c.execute(
                            "INSERT INTO runs(id, instance_id, template_id, engine, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                            (test_run_id, "1.1", "1", "celebrity", "queued", ts, ts)
                        )

                    # Step updates in core
                    server_features.init_steps(test_run_id)
                    server_features.step(test_run_id, "script", "completed", "Generated 3 scenes")
                    server_features.checkpoint(test_run_id, "scene_1", "video", "ready", output_path="/tmp/scene1.mp4")

                    # GET run detail
                    r_detail = client.get(f"/api/runs/{test_run_id}?checkpoints=false")
                    assert r_detail.status_code == 200, f"HTTP {r_detail.status_code}: {r_detail.text}"
                    assert r_detail.json().get("id") == test_run_id

                    # Cancel run
                    r_cancel = client.post(f"/api/runs/{test_run_id}/cancel")
                    assert r_cancel.status_code == 200, f"HTTP {r_cancel.status_code}: {r_cancel.text}"
                test_case("13. Runs & Orchestrator API (/api/runs, /api/runs/{id}, cancel)", test_runs_and_steps)

                # 7. Shopee Endpoints Validation
                def test_shopee_validation():
                    # Empty keyword should return 400
                    r_empty = client.post("/api/shopee/research", json={"keyword": "", "count": 5})
                    assert r_empty.status_code == 400

                    # Invalid affiliate link should return 400
                    r_inv_aff = client.post("/api/shopee/affiliate/convert", json={"links": ["https://google.com"], "sub_ids": []})
                    assert r_inv_aff.status_code == 400
                test_case("14. Shopee Request Validations & Guardrails", test_shopee_validation)

                # 8. WebSocket /ws/flow Handshake
                def test_websocket_flow():
                    with client.websocket_connect("/ws/flow") as ws:
                        # Send AGENT_HELLO
                        ws.send_json({
                            "type": "AGENT_HELLO",
                            "role": "flow-extension",
                            "version": "14.7.41",
                            "extensionId": "test_ext_123",
                            "workerId": "test_ext_123",
                            "capabilities": {"serverQueue": True, "signedUrlDownload": True}
                        })
                        # Check server status for extension connection
                        flow_info = client.get("/api/flow").json()
                        queue_info = flow_info.get("queue", {})
                        assert queue_info.get("extensionConnected") is True
                        assert queue_info.get("extensionCompatible") is True

                        # Send HEARTBEAT
                        ws.send_json({"type": "HEARTBEAT", "ts": 1234567890})
                test_case("15. WebSocket Bridge (/ws/flow) Handshake & Heartbeat", test_websocket_flow)

                # 9. Clean up created test jobs
                def test_cleanup_jobs():
                    for jid in created_job_ids:
                        del_res = client.delete(f"/api/jobs/{jid}")
                        assert del_res.status_code in (200, 404), f"Delete {jid} returned {del_res.status_code}"
                test_case("16. Cleanup Created Jobs", test_cleanup_jobs)

            print("==================================================")
            print(f" KET QUA TEST: {passed}/{total} CASES PASSED ({passed*100//total}%)")
            print("==================================================")
            if passed == total:
                print(">>> SERVER V2.8 HOAT DONG HOAN HAO, TAT CA CAC CHUC NANG DA TEST THANH CONG!")
                return 0
            else:
                print(">>> MOT SO TEST CASE BI LOI, VUI LONG KIEM TRA LOG.")
                return 1

        finally:
            db.DB_PATH = original_db

if __name__ == "__main__":
    code = run_tests()
    sys.exit(code or 0)
