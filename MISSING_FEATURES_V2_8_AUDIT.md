# V2.8 Missing Functions Audit

## Fixed immediately
- Video endpoint allowed roots now includes `modules/*/outputs`, so `/api/runs/{run_id}/video?index=0` works for Job 2/3 module outputs.
- Job 3 config topic/tone restored to valid Vietnamese.
- Job 3 product scene-1 override no longer contains mojibake prompt text.
- Job 3 exposes 5 product story templates: `direct_demo`, `problem_solution`, `unbox_play`, `before_after`, `mini_challenge`.

## V2.4 functions still need full port/check
- Factory controls: start / pause / stop / plan-today.
- Job-level retry button and job detail logs.
- Facebook page test endpoint.
- BROLL/celebrity engine config: verified celebrity folder, search/download count, broll query pack, voice/subtitle config.

## V2.5 functions still need full port/check
- Full Page Profile advanced editor parity.
- Persona front/left/right/back upload + regenerate per angle UI parity.
- Scheduler buffer controls, discard failed, publish queue controls.
- Music library/autogen controls.
- Factory mode controls: IMAGE_BEAT / IMAGE_MIX / IMAGE_TO_VIDEO, beat settings, scene mix, outfit refs.

## V2.6 functions still need full port/check
- Extension v14.9 checkpoint protocol parity.
- Scene image checkpoint reuse before retrying video.
- Per-scene video retry with same image mediaId.
- Download state reconciliation: DOWNLOADING / DONE / ERROR.
- Policy-stop handling: do not regenerate provider-policy blocked media.
- FIFO image/video submit queue parity.

## Current risk
- V2.8 now has run-level auto retry, but not yet full V2.6 scene-level checkpoint/resume parity for every engine.
- Need next patch: backport V2.6 checkpoint + resume into `extensions/FLOW_WORKER/background.js` and parenting/beauty server handlers.
