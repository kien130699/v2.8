import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import facebook

# 1. Load tokens from d:\YT\Code\Shopee\fb_page.json
shopee_pages = json.load(open(r"d:\YT\Code\Shopee\fb_page.json", encoding="utf-8")).get("data", [])
pages_by_id = {str(p["id"]): p for p in shopee_pages}

p_minhanh = pages_by_id.get("111789830996371")
p_treem = pages_by_id.get("372902152584058")

assert p_minhanh, "Không tìm thấy Page Minh Anh (111789830996371) trong fb_page.json"
assert p_treem, "Không tìm thấy Page Trẻ Em Thông Minh (372902152584058) trong fb_page.json"

# 2. Save fresh tokens into V2.8 DB
facebook.save_page("111789830996371", "Minh Anh", p_minhanh["access_token"], p_minhanh.get("tasks", []))
facebook.save_page("372902152584058", "Trẻ Em Thông Minh", p_treem["access_token"], p_treem.get("tasks", []))

print("Tokens verified:")
t1 = facebook.test_page("111789830996371")
print(f"  - Minh Anh Test: {t1.get('ok')}, {t1.get('name')}")
t2 = facebook.test_page("372902152584058")
print(f"  - Tre Em Thong Minh Test: {t2.get('ok')}, {t2.get('name')}")

v_minhanh = ROOT / "modules" / "flow_content" / "outputs" / "factory_v2" / "new_minhanh_run" / "final_minhanh_new.mp4"
v_treem = ROOT / "modules" / "parenting" / "outputs" / "parenting" / "new_treem_run" / "final_treem_new.mp4"

# 3. Create run records in SQLite DB
with sqlite3.connect(ROOT / "data" / "v28.sqlite3") as conn:
    conn.execute(
        "INSERT OR REPLACE INTO runs(id, instance_id, template_id, engine, status, output_json, created_at, updated_at, finished_at) VALUES('run_2_1_fresh_publish_01', '2.1', '2', 'beauty', 'done', ?, datetime('now'), datetime('now'), datetime('now'))",
        (json.dumps({"video_paths": [str(v_minhanh)], "final_path": str(v_minhanh)}),)
    )
    conn.execute(
        "INSERT OR REPLACE INTO runs(id, instance_id, template_id, engine, status, output_json, created_at, updated_at, finished_at) VALUES('run_3_11_fresh_publish_01', '3.11', '3', 'parenting', 'done', ?, datetime('now'), datetime('now'), datetime('now'))",
        (json.dumps({"video_paths": [str(v_treem)], "final_path": str(v_treem)}),)
    )
    conn.commit()

# 4. Publish Video for Page Minh Anh
pub_1 = facebook.enqueue_publish(
    run_id="run_2_1_fresh_publish_01",
    page_id="111789830996371",
    video_path=str(v_minhanh),
    title="Phong cách quyến rũ cùng Minh Anh ✨",
    description="Tự tin khoe trọn từng đường cong quyến rũ với đầm bodycon cực sang chảnh ✨ Bạn thích outfit nào nhất trong video này? 💕 #beauty #lifestyle #glamour #fashion #reels",
    dry_run=False
)
print(f"\nUploading Reel cho Page Minh Anh (Pub ID: {pub_1})...")
res_minhanh = facebook.publish_one(pub_1)
print(f"Ket qua Minh Anh: {res_minhanh}")


# 5. Publish Video for Page Trẻ Em Thông Minh
pub_2 = facebook.enqueue_publish(
    run_id="run_3_11_fresh_publish_01",
    page_id="372902152584058",
    video_path=str(v_treem),
    title="Đồ chơi xe khủng long đường ray cỡ lớn cho bé 🦖",
    description="Món đồ chơi siêu cuốn giúp bé thỏa sức khám phá, rèn luyện tư duy không gian và tương tác cực tốt cùng ba mẹ! 🦖🚗 Sắm ngay cho bé chơi mê say nhé ba mẹ ơi! #dochoithongminh #mevabe #unboxdochoi #dochoitreem #reels",
    dry_run=False
)
print(f"\nUploading Reel cho Page Tre Em Thong Minh (Pub ID: {pub_2})...")
res_treem = facebook.publish_one(pub_2)
print(f"Ket qua Tre Em Thong Minh: {res_treem}")
