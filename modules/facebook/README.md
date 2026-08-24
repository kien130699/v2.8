# Facebook Content Factory V3.1

Web server local để tạo và đăng Facebook Reels theo lịch: 10 Page × 2 video/ngày.

## V3.1 có gì mới

- Tích hợp **9Router** qua OpenAI-compatible `/v1/chat/completions`.
- `NINEROUTER_API_KEY` nằm trong `.env`, không trả secret ra browser.
- Dashboard đọc **`/v1/models`** từ 9Router để hiển thị model thực tế đang có.
- Preset sẵn Gemini 3.1, Gemini 3.5, Gemini 3.6, GPT 5.4 (Codex/GitHub) và GPT 5.5. Model ID đều chỉnh được bằng ENV.
- Nút **Test LLM** đo latency và xác nhận model gọi được.
- Nút **TEST VIDEO** tạo đúng 1 video render-only; job có trạng thái `TEST_READY` và tuyệt đối không được auto-publish lên Facebook.
- Vẫn giữ fallback script bank nếu LLM chưa cấu hình hoặc gọi lỗi.

## Cài đặt

1. Chạy `setup.bat` một lần.
2. Copy/sửa `.env`.
3. Chạy `start_web.bat`.
4. Mở `http://127.0.0.1:8797`.

### 9Router

```env
NINEROUTER_BASE_URL=http://127.0.0.1:20128/v1
NINEROUTER_API_KEY=sk-9router-...
NINEROUTER_DEFAULT_MODEL=cx/gpt-5.4

NINEROUTER_MODEL_GEMINI_31=vertex/gemini-3.1-pro-preview
NINEROUTER_MODEL_GEMINI_35=gemini-3.5
NINEROUTER_MODEL_GEMINI_36=gemini-3.6
NINEROUTER_MODEL_GPT_54=cx/gpt-5.4
NINEROUTER_MODEL_GPT_54_GH=gh/gpt-5.4
NINEROUTER_MODEL_GPT_55=cx/gpt-5.5
```

**Lưu ý:** Gemini 3.5/3.6 có thể là alias riêng trên 9Router của bạn. V3.1 không đoán cứng; bấm `Refresh models` để đọc `/v1/models`, sau đó chọn chính xác model ID live. Nếu alias của bạn khác, sửa biến ENV tương ứng.

## Test an toàn trước khi chạy 20 video/ngày

1. Vào **Settings → 9Router / LLM**.
2. Bấm **Refresh models**.
3. Chọn model → **Test LLM**.
4. Chọn Page profile → **Tạo 1 video test**.
5. Job test chạy độc lập kể cả Factory đang STOPPED.
6. Khi xong, mở Jobs → video preview. Job test là `TEST_READY`, không vào Facebook publish queue.
7. Chỉ khi video ổn mới bật `START` và `AUTO PUBLISH`.

## Video engine

Có thể dùng engine đi kèm hoặc trỏ đến V2.7 đang chạy tốt:

```env
VIDEO_ENGINE_DIR=D:\YT\Code\V2.4\broll_video_test
ENGINE_PYTHON=D:\YT\Code\V2.4\broll_video_test\.venv\Scripts\python.exe
```

Engine hiện được khóa 1 render worker để tránh ghi đè `script.json/work/output` dùng chung. Bản sau có thể tách workspace theo job để chạy multi-worker.

## Facebook

Token Page đọc từ ENV:

```env
FB_PAGE_1_TOKEN=
...
FB_PAGE_10_TOKEN=
```

Test Video không yêu cầu Page ID/token. Publish thật chỉ lấy job trạng thái `READY`; `TEST_READY` bị loại khỏi publisher theo thiết kế.
