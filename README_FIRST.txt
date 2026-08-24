V2.8.5.7 ENV RESOLVER + PERSISTENT CONSOLE
======================
- Job 1 keys resolve from process/root/module/legacy .env read-only fallback; subprocess receives the same snapshot.
- ENV_CHECK.bat shows safe key source_path without exposing values.
- START.bat uses a dedicated CMD /K console; supervisor fatal errors HALT visibly and logs persist.
- Port isolation unchanged: V2.8=3000, Edge=9224; never touch 8786/8787 or 9222/9223.

V2.8.5.4 PORT ISOLATION
======================
V2.8 SERVER : 127.0.0.1:3000
V2.8 EDGE   : remote-debugging-port 9224 (external Edge launch only)
DO NOT TOUCH: server 8786 / 8787; Edge debug 9222 / 9223
FLOW_WORKER : 14.7.0, fixed ws://127.0.0.1:3000/ws/flow
AI          : GPT/Gemini only via 9ROUTER_API_KEY

FACEBOOK JOB FACTORY V2.8.5.3
===========================

MỞ: http://127.0.0.1:3000
FLOW WS: ws://127.0.0.1:3000/ws/flow
FLOW_WORKER yêu cầu: 14.6.9+

V2.8.5.3 FLOW ROUTE + ASSET INDEX + CONSOLE STABILITY
-----------------
- Audit + fix hơn 30 failure mode ở RUN queue, SQLite, Flow WS, scheduler, Job adapters, persona/media, Facebook publish, API validation và UI state.
- RUN dùng durable SQLite workers; waiting_flow không busy-loop, không nested DB lock.
- Flow result/media có durable outbox và run identity guard; reconnect không làm mất/misroute result.
- Model ảnh/video, concurrency, duration/output/extend là GLOBAL và được truyền thật xuống extension; bỏ hard-code 9/4.
- Job 1 có preflight ffmpeg/ffprobe, edge-tts, B-roll API key, celebrity source; subprocess lỗi trả nguyên nhân thật thay vì chỉ exit code 1.
- Persona upload validate ảnh thật; clone tách asset. Parenting clone tách character/reference.
- Facebook kiểm tra video trước publish, phân loại lỗi permanent, giữ mapping/history đúng.
- UI chống stale response/toast race; launcher chờ server ready.

CÀI / NÂNG
-----------
1. Tắt server cũ.
2. Nếu dùng FULL: giải nén vào thư mục mới.
3. Nếu dùng PATCH: ghi đè lên V2.8.5.1, KHÔNG xóa thư mục data.
4. chrome://extensions -> Reload FLOW_WORKER từ thư mục extensions/FLOW_WORKER.
5. Kiểm tra version extension = 14.6.9.
6. Chạy START.bat.

CHECK
-----
- Chạy CHECK_BUILD.bat nếu muốn kiểm tra Python/JS/self-test.
- Job 1 không cần Facebook Page để render, nhưng cần B-roll API key và các binary/package preflight.
- Job 2 cần Persona Front và FLOW_WORKER online.
- Job 3 cần FLOW_WORKER online.


V2.8.5 SERVER LIFECYCLE
- START.bat chạy supervisor.py; Uvicorn là child process.
- Nếu child crash bất thường, supervisor tự restart tối đa 5 lần/120 giây.
- Log crash: data\server_crash.log.
- STOP.bat dừng cả child + supervisor để không bị tự restart lại.
- FLOW_WORKER bắt buộc 14.6.9; reload extension sau khi update.
- Healthy duplicate websocket không được thay socket đang hoạt động; forced reconnect chờ old socket đóng trước.

V2.8.5.3 FIX MỚI
- Nhận đúng URL Flow có locale: /fx/vi/tools/flow/project/<id>, /fx/en/...; không tự kéo project đang mở về landing page.
- Recovery/project-root giữ nguyên locale hiện tại.
- Upload persona/reference: không re-upload chỉ vì Asset Picker index chậm; chờ exact mediaId, correlate ID mới theo filename, reload đúng project một lần, rồi mới báo FLOW_ASSET_INDEX_DELAY.
- Chạy thẳng `py run_server.py` cũng đi qua supervisor. Child tự exit mà không có STOP sẽ tự restart.
- Log terminal luôn được lưu ở data\server_console.log; traceback/crash ở data\server_crash.log.
