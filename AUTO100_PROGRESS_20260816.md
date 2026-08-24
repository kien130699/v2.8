# V2.8 Auto-100 Progress 20260816_084018

## Ported in this pass
- Scene checkpoint public API: /api/flow/jobs/{job_id}/checkpoints.
- Manual resume API: /api/flow/jobs/{job_id}/resume.
- Per-scene retry API: /api/flow/jobs/{job_id}/scenes/{scene_id}/retry.
- Master run enrichment: /api/runs?checkpoints=true shows Job 3 checkpoint state.
- Runs UI now shows scene chips, missing/download counts, Resume button, Retry scene button.
- Extension minimum bumped to 14.7.28 to block stale 14.7.27 code.
- Extension metadata now exposes capabilities, including shopeeSearch after reload.

## Still not truthfully 100%
- Edge must reload unpacked extension from extensions/FLOW_WORKER so server sees 14.7.28.
- Real Flow output still unverified after reload.
- Auto reload Flow tab cannot be forced from server if extension itself is stale/offline; user reload is required once.
