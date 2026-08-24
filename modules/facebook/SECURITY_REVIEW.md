# Security review — V3.1

- Server bind mặc định `127.0.0.1`, không public Internet.
- Secret (`NINEROUTER_API_KEY`, Facebook token, Pexels, Serper) chỉ đọc từ ENV; API settings chỉ trả trạng thái configured, base URL và model ID, không trả secret.
- 9Router request chỉ gọi endpoint OpenAI-compatible đã cấu hình: `GET /v1/models`, `POST /v1/chat/completions`.
- Test video là render-only: trạng thái kết thúc `TEST_READY`; publisher chỉ claim `READY`, nên test không thể tự đăng Facebook.
- Video endpoint kiểm tra file nằm dưới `output/` trước khi serve.
- Subprocess video engine dùng argv với `create_subprocess_exec`, không `shell=True`.
- Không có `eval`, `exec`, `os.system`, PowerShell download-and-execute trong server source.
- Không clone/chạy code GitHub bên thứ ba ở runtime.
- 9Router model ID 3.5/3.6 được coi là cấu hình/alias của người dùng; dashboard có `/v1/models` để xác thực trước khi dùng.
