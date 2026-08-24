# Test Run Report 2026-08-14

## Commands

- `py tools\self_test.py` -> PASS
- `py modules\flow_content\tests\smoke_test.py` -> PASS
- `py modules\flow_content\tests\v2_trial_test.py` -> PASS, produced Flow final videos
- `PYTHONPATH=modules\flow_content py modules\flow_content\tests\model_health_test.py` -> PASS
- `py modules\facebook\tests\smoke_test.py` -> PASS
- `py tools\video_output_smoke.py` -> PASS, produced one valid mp4 per V2.8 job type

## Video Outputs

- Job 1.1 Celebrity: `data\video_output_smoke\celebrity.mp4`
- Job 2.1 Beauty: `data\video_output_smoke\beauty.mp4`
- Job 3.1 Parenting: `data\video_output_smoke\parenting.mp4`
- Flow factory final videos: `modules\flow_content\outputs\factory_v2\**\final.mp4`

## Fixes Applied

- Safe Windows Unicode logging in `core\job_manager.py`.
- Closed SQLite handles in `tools\self_test.py`.
- Made Flow `conn()` close/commit/rollback reliably in `modules\flow_content\app.py`.
- Updated Flow worker smoke test to current 14.7.0 ready protocol.
- Updated Flow V2 trial fake clips so generated mp4 files pass size/video validation.
- Isolated Facebook smoke test from live DB.
- Added master-level video output smoke test covering Celebrity, Beauty, Parenting.
