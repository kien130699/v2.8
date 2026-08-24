# V2.8 Rebuild Plan

Mục tiêu: rebuild `V2.8_Facebook_Job_Factory` thành nền tảng ổn định, typed, test được, không vá chồng từ V2.4/V2.5/V2.6.

## 0. Chẩn đoán nhanh

- `modules/flow_content/app.py`: 6726 dòng, monolith từ V2.5, khó test, khó reuse.
- `modules/parenting/app.py`: 5490 dòng, `modules/parenting/parenting.py`: 4445 dòng, duplicate logic từ V2.6.
- `modules/facebook/engine_v27/app.py`: 2311 dòng, legacy engine từ V2.4.
- `core/job_manager.py`: 831 dòng, giữ quá nhiều trách nhiệm: plugin, assets, schedule, run queue, publish, recovery.
- `core/broker.py`: 711 dòng, vừa websocket broker vừa queue vừa settings vừa download/outbox.
- Test hiện có ít hơn nhu cầu thực tế: 4 Python tests cho toàn server 2.8.
- Kiến trúc hiện tại là bản ghép 2.4/2.5/2.6 + nhiều patch hotfix, không phải core product clean.

## 1. Nguyên tắc rebuild

- Không sửa tiếp monolith nếu không bắt buộc; bọc legacy bằng adapter, rút dần domain service ra ngoài.
- Core server chỉ quản lý job lifecycle, flow queue, assets, publish, settings, auth/env, logs.
- Mỗi job type là plugin độc lập: manifest + schema + adapter + tests + fixtures.
- SQLite dùng repository + migration rõ, không SQL rải khắp code.
- Flow worker giao tiếp bằng protocol versioned, idempotent, có correlation id, timeout, retry, recovery.
- Mọi trạng thái quan trọng phải durable: run, flow request, engine task, asset, publish task, error.
- Không hard-code port/model/duration/path trong module/job; dùng config typed.
- Không dùng secret fallback mơ hồ khi chạy production; env source phải explicit, audit được.
- Test trước khi refactor sâu: characterization tests khóa hành vi cũ.

## 2. Kiến trúc mới đề xuất

```text
server/
  app.py                  # FastAPI composition root
  api/                    # HTTP + WebSocket routes only
  core/                   # domain services, no FastAPI dependency
  infra/                  # sqlite, files, subprocess, facebook graph, flow ws
  jobs/                   # plugin SDK + built-in job types
  workers/                # run worker, scheduler, publish worker, recovery worker
  schemas/                # pydantic DTOs
  tests/
extensions/
  flow_worker/            # protocol client only
legacy/
  celebrity_v27/
  flow_content_v25/
  parenting_v26/
data/
```

## 3. Core modules cần có

- `RunService`: create/start/cancel/retry/recover runs, state machine duy nhất.
- `JobRegistry`: load plugin manifests, validate config, expose capabilities.
- `AssetService`: upload, clone, hash, metadata, provenance, cleanup.
- `FlowService`: enqueue Flow requests, dispatch to extension, receive result, retry/recover.
- `EngineGateway`: bridge tới legacy engines hoặc engine mới, trả status chuẩn.
- `PublishService`: Facebook page/token, video validation, publish queue, retry policy.
- `SchedulerService`: daily/interval/manual trigger, no duplicate run.
- `SettingsService`: typed global settings + per-job override.
- `EventLogService`: structured event, error code, user-visible message.
- `EnvService`: explicit env load, preflight, redacted diagnostics.

## 4. Database mới

- Dùng migration folder `migrations/*.sql`, có `schema_version`.
- Bảng chính:
  - `job_types`, `job_instances`, `job_assets`
  - `runs`, `run_steps`, `run_events`
  - `flow_requests`, `flow_results`, `flow_worker_sessions`
  - `engine_tasks`
  - `facebook_pages`, `publish_jobs`
  - `settings`, `secrets_meta`, `locks`
- Mọi worker claim task bằng transaction + `locked_by` + `locked_until`.
- Mọi operation external có `idempotency_key`.
- Không dùng JSON blob cho state cốt lõi nếu cần query/recovery.

## 5. Job plugin contract mới

Mỗi job type phải có:

- `manifest.json`: id, version, display name, config schema, asset schema, output schema, required capabilities.
- `adapter.py`: `prepare()`, `start()`, `poll()`, `recover()`, `cancel()`, `collect_outputs()`.
- `tests/`: config validation, dry-run, recovery, failed dependency, output mapping.
- `fixtures/`: sample config/assets nhỏ, không chứa secret.

Chuẩn return:

```json
{
  "status": "queued|running|waiting_flow|done|failed|cancelled",
  "external_tasks": [],
  "outputs": [],
  "error": {"code": "", "message": "", "retryable": false}
}
```

## 6. Flow worker protocol mới

- Version bắt buộc: `protocol_version`, `worker_version`, `capabilities`.
- Server gửi `request_id`, `run_id`, `step_id`, `idempotency_key`, `deadline_at`.
- Worker trả `ack`, `progress`, `asset_index`, `result`, `error`.
- Server không phụ thuộc tab hiện tại; worker tự report project/session.
- Reconnect phải replay pending request, không mất result.
- Extension không chứa business logic job; chỉ automation Flow UI/API.

## 7. Rebuild phases

### Phase 1 — Baseline & safety

- Freeze bản hiện tại: copy `V2.8_Facebook_Job_Factory` thành `V2.8_legacy_backup`.
- Tạo branch/thư mục rebuild riêng: `V2.8_rebuild` hoặc `V2.9_core`.
- Viết smoke test hiện trạng: start server, list jobs, create run dry-run, Flow ws handshake, publish queue dry-run.
- Ghi ma trận feature từ V2.4/V2.5/V2.6: cái nào giữ, bỏ, nâng cấp.
- Chuẩn hóa `.env.example`, preflight, port map.

### Phase 2 — New skeleton

- Tạo FastAPI app mỏng: routes gọi service, không chứa business logic.
- Tạo `infra/sqlite` + migrations + repositories.
- Tạo typed schemas bằng Pydantic.
- Tạo unified error model: `code`, `message`, `retryable`, `details`.
- Tạo event bus/log nội bộ.

### Phase 3 — Durable lifecycle

- Implement run state machine duy nhất.
- Implement worker claim/heartbeat/timeout/retry.
- Implement scheduler không duplicate.
- Implement recovery startup: reset stale lock, resume pending Flow, resume publish.
- Test crash/restart bằng fake worker.

### Phase 4 — Flow protocol

- Viết `FlowService` + fake in-memory worker trước.
- Refactor extension về protocol rõ.
- Test: disconnect/reconnect, duplicate result, stale result, timeout, asset index delay.
- Chỉ sau khi fake pass mới nối browser extension thật.

### Phase 5 — Legacy engine adapters

- Di chuyển V2.4/V2.5/V2.6 vào `legacy/` read-only.
- Viết gateway cho celebrity/beauty/parenting không sửa legacy nhiều.
- Chuẩn hóa output: video path, title, description, metadata, publish readiness.
- Bắt lỗi subprocess/stderr/stdout thành error code chuẩn.

### Phase 6 — Job types rebuilt

- Rebuild `celebrity` adapter: source search/cache, b-roll, tts, render, output validation.
- Rebuild `beauty` adapter: Flow image/video generation, asset correlation, output gather.
- Rebuild `parenting` adapter: character/reference assets, strict recovery, render/output validation.
- Mỗi adapter có unit + integration test bằng fake gateways.

### Phase 7 — Facebook publish rebuilt

- Tách Facebook Graph client khỏi queue service.
- Validate video trước publish bằng `ffprobe`.
- Token/page storage có redaction và test page.
- Retry policy phân loại permanent/transient.
- Publish result map vào run output, không mất history.

### Phase 8 — UI rebuilt

- UI đọc API schema/capabilities, không hard-code quá nhiều state.
- Run detail có timeline/event/error/output.
- Flow worker status rõ: connected, version, active request, last heartbeat.
- Form config dùng manifest schema.
- Toast/status chống stale response bằng request sequence.

### Phase 9 — Test & release gate

- Unit test service/repository/plugin contract.
- Integration test server + fake Flow + fake Facebook + fake legacy engine.
- E2E dry-run cho 3 job types.
- Migration test từ DB 2.8 hiện tại sang schema mới.
- Build checklist: `ruff`, `mypy` nếu áp dụng, `pytest`, self-test, manual Flow worker.

## 8. Thứ tự ưu tiên rebuild

1. `core/db.py` + migrations: nền state đúng.
2. `RunService` + worker lifecycle: hết lỗi queue/recovery.
3. `FlowService` + fake worker: hết lỗi mất/misroute result.
4. Plugin contract: hết adapter lỏng kiểu `Any`.
5. Legacy gateways: giữ feature cũ nhưng cô lập nợ.
6. Facebook publish: tránh publish lỗi/mất mapping.
7. UI: cuối cùng, vì UI phụ thuộc API ổn.

## 9. Không nên làm

- Không nhét thêm patch vào `modules/flow_content/app.py` hoặc `modules/parenting/app.py`.
- Không để `core/job_manager.py` tiếp tục phình.
- Không cho extension quyết định logic business.
- Không dùng `Any` làm contract lâu dài.
- Không để settings global âm thầm override per-job mà không trace.
- Không test bằng browser thật trước khi fake worker pass.

## 10. Definition of Done

- Server restart không mất run đang chờ Flow/publish.
- 3 job types chạy dry-run repeatable bằng fake dependencies.
- Flow reconnect không mất result, duplicate result không tạo duplicate output.
- Mọi failure có error code + retryable flag.
- DB migration giữ được jobs/pages/history từ V2.8.
- UI hiển thị run timeline đủ để debug không mở console.
- Không còn file core > 500 dòng trừ route tổng hợp đặc biệt.
- Test suite chạy xanh trên máy local không cần secret thật.

## 11. Milestone đề xuất

- M1: 1 ngày — baseline tests + feature matrix + skeleton.
- M2: 2 ngày — DB migration + run lifecycle + fake worker.
- M3: 2 ngày — Flow protocol + extension refactor.
- M4: 2 ngày — 3 legacy gateways + plugin contract.
- M5: 1 ngày — Facebook publish + recovery tests.
- M6: 1 ngày — UI run timeline + release checklist.

Tổng: 7-10 ngày nếu giữ legacy engines. 14-21 ngày nếu rewrite toàn bộ engines.
