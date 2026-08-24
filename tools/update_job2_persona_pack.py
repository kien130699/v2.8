import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 1. Update v28.sqlite3
c = sqlite3.connect(ROOT / "data" / "v28.sqlite3")
c.row_factory = sqlite3.Row
r = c.execute("SELECT config_json FROM job_instances WHERE id='2.1'").fetchone()
cfg = json.loads(r["config_json"]) if r and r["config_json"] else {}

cfg["body_preset"] = "glam_curvy"
cfg["sexiness_level"] = 95
cfg["persona_path"] = str((ROOT / "data" / "job_assets" / "2_1" / "persona_pink_dress.jpg").resolve())

cfg["outfit_prompts"] = [
    "váy bodycon hồng pastel cổ yếm (ruched halter mini dress), khoét ngực mềm mại, chất liệu thun nhún ôm sát đường cong 3 vòng đồng hồ cát, giày cao gót quai mảnh ánh bạc",
    "đầm lụa satin hai dây xẻ đùi màu đỏ rượu vang (wine red cowl-neck silk slip dress), tôn vòng eo con kiến và bờ vai thon quyến rũ, giày cao gót màu nude",
    "váy len tăm dệt kim trễ vai màu trắng kem (off-shoulder bodycon knit mini dress), ôm sát cơ thể tôn vòng một đầy đặn và hông quả táo, khuyên tai ngọc trai sang trọng",
    "set áo corset satin đen cúp ngực tôn vòng một (black structured corset top with sweetheart neckline) phối chân váy mini đen ôm sát, choker nhung đen cá tính, giày cao gót sành điệu"
]

cfg["outfit_paths"] = [
    str((ROOT / "data" / "job_assets" / "2_1" / "persona_pink_dress.jpg").resolve()),
    str((ROOT / "data" / "job_assets" / "2_1" / "persona_red_silk.jpg").resolve()),
    str((ROOT / "data" / "job_assets" / "2_1" / "persona_white_knit.jpg").resolve()),
    str((ROOT / "data" / "job_assets" / "2_1" / "persona_black_corset.jpg").resolve())
]

cfg["backgrounds"] = [
    "Penthouse studio sang trọng với vòm đèn led hắt sáng ấm áp, nội thất tối giản hiện đại (modern luxury architectural arch interior)",
    "Rooftop lounge cao cấp tại TP.HCM/Hà Nội lúc hoàng hôn dusk, nhìn ra skyline Bitexco / Landmark 81 lung linh đèn",
    "Quán cafe phong cách Pháp sang chảnh tại Thảo Điền với cửa kính lớn và ánh sáng tự nhiên dịu nhẹ",
    "Luxury Night Lounge / Bar cao cấp về đêm với ánh đèn spotlight ấm áp và không gian nội thất gỗ tối màu sang trọng"
]

cfg["poses"] = [
    "đứng tự nhiên nhìn về máy quay, khoe trọn đường cong cơ thể và mỉm cười nhẹ",
    "bước đi catwalk chậm rãi, xoay nhẹ người 45 độ và nhìn cuốn hút về phía ống kính",
    "tựa nhẹ vào lan can hoặc bàn lounge, tay chỉnh nhẹ tóc bồng bềnh",
    "đi ngang qua máy quay rồi ngoái lại nhìn với ánh mắt quyến rũ"
]

c.execute("UPDATE job_instances SET config_json=?, updated_at=datetime('now') WHERE id='2.1'", (json.dumps(cfg, ensure_ascii=False),))
c.commit()
print("Updated Job 2.1 in v28.sqlite3!")

# 2. Update flow_content factory.sqlite3
c2 = sqlite3.connect(ROOT / "modules" / "flow_content" / "data" / "factory.sqlite3")
c2.execute("""UPDATE page_profiles SET 
    persona_path=?, body_preset='glam_curvy', sexiness_level=95,
    outfit_prompts_json=?, outfit_paths_json=?, backgrounds_json=?, poses_json=?, updated_at=datetime('now')
    WHERE id='v28_2_1' OR id='profile_v28_2_1' OR id='2.1'
""", (
    cfg["persona_path"],
    json.dumps(cfg["outfit_prompts"], ensure_ascii=False),
    json.dumps(cfg["outfit_paths"], ensure_ascii=False),
    json.dumps(cfg["backgrounds"], ensure_ascii=False),
    json.dumps(cfg["poses"], ensure_ascii=False)
))
c2.commit()
print("Updated page_profiles in flow_content factory.sqlite3!")
