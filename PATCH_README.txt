V2.8.5.9 WINDOWS UTF-8 ENGINE HOTFIX

- Fix Job 1 BROLL crash: `charmap codec can't encode character` on Windows ANSI console.
- Every V2.8 child Python process forces PYTHONUTF8=1 and PYTHONIOENCODING=utf-8.
- BROLL engine reconfigures stdout/stderr to UTF-8 with errors=replace.
- Parent still decodes engine output as UTF-8 with errors=replace.
- FLOW_WORKER unchanged at 14.7.0; no extension reload required.
- Port isolation unchanged: V2.8=3000 / Edge=9224; never touch 8786/8787 or 9222/9223.

V2.8.5.7 SINGLE INSTANCE + ENV SNAPSHOT HOTFIX

- Fix trường hợp server mới báo ENV OK nhưng Job vẫn chạy server cũ đang giữ port 3000.
- Chỉ dừng V2.8 cũ đã được xác minh trên port 3000; không đụng 8786/8787 hoặc Edge 9222/9223.
- Job 1 preflight + BROLL child dùng cùng resolve_env_snapshot.
- FLOW_WORKER vẫn 14.7.0; không cần reload extension.

V2.8.5.7 ENV RESOLVER + PERSISTENT CONSOLE HOTFIX

FIX KEY JOB 1
- Một resolver duy nhất cho PEXELS_API_KEY / PIXABAY_API_KEY / SERPER_API_KEY / 9ROUTER_API_KEY.
- Thứ tự: process không-rỗng > V28_ENV_FILE > V2.8 root .env > module .env > fallback đọc-only .env ở V2.4/V2.5/V2.6 sibling.
- Không sửa/xóa file .env cũ, không đụng port/extension cũ.
- Preflight Job 1 và subprocess BROLL dùng cùng exact env snapshot.
- ENV_CHECK.bat và /api/health chỉ báo configured/source/source_path, KHÔNG in secret.

FIX TERMINAL
- START.bat mở console riêng bằng CMD /K nên cửa sổ không tự biến mất sau khi Python thoát.
- py run_server.py vẫn qua supervisor.
- Supervisor bắt cả fatal exception ngoài child loop và HALT hiển thị thay vì tự out.
- Log: data/server_console.log và data/server_crash.log.

PORT ISOLATION GIỮ NGUYÊN
- V2.8 server 3000 / Edge debug owner 9224.
- Không bind/scan/connect 8786/8787; không đụng Edge 9222/9223.
- FLOW_WORKER vẫn 14.7.0, patch này không cần reload extension nếu đã đúng 14.7.0.
