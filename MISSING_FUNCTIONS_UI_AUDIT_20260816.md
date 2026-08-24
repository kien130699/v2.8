# V2.8 Missing Functions Audit - 2026-08-16

## Fixed in this pass
- UI fetched from `http://127.0.0.1:3000/` and `/static/app.js?v=2853`.
- Main UI labels translated to Vietnamese.
- Broken mojibake UI strings fixed with HTML entities / JS unicode escapes.
- `master/static/app.js` passes `node --check`.

## Still missing versus V2.6/V2.5

### Automation reliability
- Scene-level checkpoint cache is not fully ported.
- Successful scene reuse after retry/restart is not complete.
- Flow tab auto-reload on stuck/error is not exposed as robust server action.
- Failed job resume is run-level only, not scene-level resume.
- Download reconciliation is weak: V2.6 had more states around `downloading`, `checkpointing`, stale leases, and requeue.
- Policy/blocked scene handling still not granular enough.

### Job 3 templates
- Template Job selector exists now, but UI still does not show per-template explanation/examples.
- Template 4/5 generation exists at plan level, but full V2.6 topic library/campaign UI is not fully ported.
- Job 3 product/hybrid has product story templates, but no UI preview for 5 product templates.

### Job 2
- Location strategy fields exist, but no strong UI preview/validation for selected location per clip.
- Multi-clip output exists, but UI does not expose per-clip scene status/checkpoint.
- Sexiness control exists, but no visual preflight scoring/preview before Flow run.

### UI/Operations
- No one-click “test all 3 jobs and show outputs” dashboard.
- No button for “retry failed scene only”.
- No button for “resume from last successful scene”.
- No button for “reload Flow tab and rebind extension”.
- No browser watcher panel showing current Flow tab state/errors.
- No output completeness validator: expected N clips vs downloaded N clips vs final mp4.

## Next implementation order
1. Add output completeness validator to runs UI.
2. Add Flow reload/rebind action and visible browser status panel.
3. Port scene checkpoint schema from V2.6 to V2.8 run records.
4. Add retry failed scene only.
5. Add resume from last successful scene.
6. Add one-click test matrix: Job 1, Job 2, Job 3, Template 3/4/5.
