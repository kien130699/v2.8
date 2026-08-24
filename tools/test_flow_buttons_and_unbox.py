from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core import db, env_loader, server_features
from modules.parenting.parenting import (
    ParentingHandler,
    ProductPlanRequest,
    PlanRequest,
    StoryGenerateRequest
)

def run_tests() -> int:
    print("==================================================================")
    print("   KIEM TRA CAC NUT CLICK TREN FLOW & CHUC NANG UNBOX SAN PHAM   ")
    print("==================================================================")

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

    # -------------------------------------------------------------
    # PHAN 1: KIEM TRA CAC NUT CLICK & SELECTOR TREN GOOGLE FLOW
    # -------------------------------------------------------------
    print("\n--- PHAN 1: CAC NUT CLICK & SELECTOR DIEU KHIEN FLOW ---")

    page_js = (ROOT / "extensions" / "FLOW_WORKER" / "page.js").read_text(encoding="utf-8")
    bg_js = (ROOT / "extensions" / "FLOW_WORKER" / "background.js").read_text(encoding="utf-8")

    def test_flow_button_create_project():
        # Kiểm tra nút "Create Project" / "Tạo dự án mới" trên Flow
        assert "findCreateProjectButton" in page_js
        assert "getCreateProjectPoint" in page_js
        assert "new project" in page_js.lower() or "create project" in page_js.lower()
    test_case("1.1 Nút 'Create Project' (Tạo dự án mới Flow)", test_flow_button_create_project)

    def test_flow_button_all_media():
        # Kiểm tra nút chuyển view "All Media" / "Tất cả phương tiện"
        assert "findAllMediaButton" in page_js
        assert "getAllMediaPoint" in page_js
        assert "isAllMediaAvailable" in page_js
        assert "all media" in page_js.lower()
    test_case("1.2 Nút 'All Media' (Chuyển chế độ xem toàn bộ media)", test_flow_button_all_media)

    def test_flow_button_settings_menu():
        # Kiểm tra nút mở/đóng Settings (Model, Aspect Ratio, Concurrency)
        assert "getSettingsTriggerPoint" in page_js
        assert "openSettings" in page_js
        assert "closeSettings" in page_js
        assert "looksLikeMainSettingsMenu" in page_js
    test_case("1.3 Nút Settings Trigger & Menu (Mở/Đóng cài đặt Flow)", test_flow_button_settings_menu)

    def test_flow_button_model_select():
        # Kiểm tra nút chọn Model (Nano Banana 2, Veo 3.1 Fast/Quality, v.v.)
        assert "getModelTriggerPoint" in page_js
        assert "getModelOptionPoint" in page_js
        assert "selectModel" in page_js
        assert "Nano Banana" in page_js or "Nano Banana" in bg_js
    test_case("1.4 Nút chọn AI Model (Chuyển đổi Image & Video Models)", test_flow_button_model_select)

    def test_flow_button_asset_picker():
        # Kiểm tra nút bấm thêm ảnh tham chiếu / Asset Picker (Reference Card)
        assert "getAddMediaPoint" in page_js
        assert "openAssetPicker" in page_js
        assert "closePicker" in page_js or "closeAssetPicker" in page_js
        assert "getUploadImagePoint" in page_js
        assert "getImagesTabPoint" in page_js
    test_case("1.5 Nút 'Add Reference Media' & Tab chọn ảnh tham chiếu", test_flow_button_asset_picker)

    def test_flow_button_clear_reference_chip():
        # Kiểm tra nút hủy/xóa ảnh tham chiếu (Cancel/Remove chip)
        assert "getComposerMediaRemovePoint" in page_js
        assert "removeComposerMediaFirst" in page_js
        assert "cancel" in page_js.lower()
    test_case("1.6 Nút xóa/hủy Reference Image Chip (Composer Clear)", test_flow_button_clear_reference_chip)

    def test_flow_button_prompt_input_and_clear():
        # Kiểm tra Slate prompt editor (nhập prompt, xóa prompt)
        assert "replacePrompt" in page_js
        assert "clearPrompt" in page_js
        assert "findSlateEl" in page_js
    test_case("1.7 Nhập & Xóa Prompt (Slate Editor trên Flow)", test_flow_button_prompt_input_and_clear)

    def test_flow_button_generate_create():
        # Kiểm tra nút 'Create' / 'Generate' (Kích hoạt render)
        assert "getCreatePoint" in page_js
        assert "waitCreateReady" in page_js
    test_case("1.8 Nút 'Create' / Generate (Bấm tạo ảnh và video)", test_flow_button_generate_create)

    def test_flow_button_extend_video():
        # Kiểm tra nút 'Extend' / 'Add Clip' (Nối dài cảnh video Veo)
        assert "getAddClipPoint" in page_js
        assert "getExtendMenuPoint" in page_js
        assert "isExtendComposerOpen" in page_js
        assert "replaceExtendPrompt" in page_js
    test_case("1.9 Nút 'Extend Video' & 'Add Clip' (Kéo dài cảnh video Veo)", test_flow_button_extend_video)

    def test_flow_video_download_and_probe():
        # Kiểm tra cơ chế tự lấy link và tải video từ Flow
        assert "findSignedVideoResource" in page_js
        assert "probeVideoRedirect" in page_js
        assert "getVideoTileInfoByMediaId" in page_js
    test_case("1.10 Cơ chế định danh & Resolve Signed Video URL", test_flow_video_download_and_probe)

    # -------------------------------------------------------------
    # PHAN 2: KIEM TRA CHUC NANG UNBOX SAN PHAM (UNBOX PLAY)
    # -------------------------------------------------------------
    print("\n--- PHAN 2: CHUC NANG UNBOX SAN PHAM (UNBOX PLAY) ---")

    def test_unbox_template_registration():
        # Kiểm tra template unbox_play đã đăng ký trong ParentingHandler
        templates = ParentingHandler._product_story_templates()
        assert "unbox_play" in templates
        desc = templates["unbox_play"]
        assert "UNBOX PLAY" in desc
        assert "unboxing" in desc.lower() or "opening moment" in desc.lower()
    test_case("2.1 Đăng ký mẫu kịch bản 'unbox_play' trong hệ thống", test_unbox_template_registration)

    def test_unbox_product_category_detection():
        # Kiểm tra phân loại sản phẩm unbox tự động
        assert ParentingHandler._product_story_kind({"title": "Đồ chơi xe khủng long nuốt ô tô đường ray"}) in ("toy", "generic")
        assert ParentingHandler._product_story_kind({"title": "Bình nước giữ nhiệt trẻ em cao cấp"}) == "bottle"
        assert ParentingHandler._product_story_kind({"title": "Bàn học thông minh chống gù cho bé"}) == "desk"
        assert ParentingHandler._product_story_kind({"title": "Kệ để đồ chơi trẻ em đa năng"}) == "storage"
    test_case("2.2 Phân loại danh mục sản phẩm unbox (Toy, Bottle, Desk, Storage)", test_unbox_product_category_detection)

    def test_unbox_prompt_structure_generation():
        # Kiểm tra cấu trúc prompt 4 clips dành cho kịch bản UNBOX PLAY
        product_title = "Bộ Đồ Chơi Xe Khủng Long Nuốt Ô Tô Đường Ray Siêu To"
        facts = {
            "title": product_title,
            "category": "toy",
            "usp": ["Khủng long nuốt xe chạy qua bụng", "Đường ray dài kèm 6 xe con"],
            "safety": "Nhựa ABS an toàn không mùi",
            "age": "3-8 tuổi"
        }

        # Khởi tạo yêu cầu kịch bản unbox_play
        req = ProductPlanRequest(
            product_id="prod_dino_123",
            character_set_id="mother_girl_01",
            story_template_id="unbox_play",
            output_duration="32s"
        )

        dummy_handler = ParentingHandler.__new__(ParentingHandler)
        template_key, template_desc = dummy_handler._select_product_template(req, facts)
        assert template_key == "unbox_play"

        profile = ParentingHandler._product_output_profile(req.output_duration)
        assert profile["veo_clips"] == 4
        assert profile["target_seconds"] == 32

        # 4 phân cảnh của UNBOX PLAY
        unbox_scenes = [
            {"clip": 1, "phase": "unboxing", "action": "Unboxing the dinosaur toy box with excited expressions"},
            {"clip": 2, "phase": "assembling", "action": "Assembling the dinosaur track parts on the floor"},
            {"clip": 3, "phase": "playing", "action": "Child playing, dinosaur car track swallowing mini cars smoothly"},
            {"clip": 4, "phase": "review", "action": "Mother holding product details and smiling, final review"}
        ]
        assert len(unbox_scenes) == 4
        assert unbox_scenes[0]["phase"] == "unboxing"
        assert unbox_scenes[1]["phase"] == "assembling"
        assert unbox_scenes[2]["phase"] == "playing"
        assert unbox_scenes[3]["phase"] == "review"
    test_case("2.3 Cấu trúc kịch bản 4 phân cảnh UNBOX PLAY (Mở hộp -> Lắp ráp -> Trải nghiệm -> Review)", test_unbox_prompt_structure_generation)

    def test_unbox_affiliate_and_sub_generation():
        # Kiểm tra định dạng link affiliate và sub_id cho kịch bản unbox
        origin_url = "https://shopee.vn/product-unbox-demo-i.111.222"
        affiliate_url = "https://s.shopee.vn/unbox_affiliate_123"
        server_features.set_affiliate(origin_url, affiliate_url, source="test_unbox")
        cached = server_features.get_affiliate(origin_url)
        assert cached == affiliate_url
    test_case("2.4 Lưu cache & Gán Link Shopee Affiliate cho video Unbox", test_unbox_affiliate_and_sub_generation)

    print("\n==================================================================")
    print(f" KET QUA TEST: {passed}/{total} CASES PASSED ({passed*100//total}%)")
    print("==================================================================")
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(run_tests())
