PATCH V2.14.29 — AGENT GUARD + BALANCED AUTO

Server 2.14.29
Extension 14.5.33 REQUIRED

AGENT / SETTINGS
- Agent aria-pressed=false: NEVER CLICK.
- Settings selector explicitly excludes Agent.
- If Settings cannot be found/opened, inspect Agent state.
- If Agent aria-pressed=true: click Agent ONCE only to turn it OFF.
- Verify Agent is OFF, then retry Settings.
- Only when Agent is OFF/not found may reload recovery run.

AUTO RANDOM
- If previous job is IMAGE_BEAT, next AUTO job MUST be IMAGE_MIX or IMAGE_TO_VIDEO.
- If the same video-capable mode repeats twice, next chooses another mode.
- Normal weights: IMAGE 25% / IMAGE+VIDEO 40% / I2V 35%.
- This prevents a 2-item reserve buffer from naturally becoming two consecutive image-only jobs.

This patch preserves Server 2.14.28's critical /ws -> extension_ws route fix.

Install:
1. Stop server.
2. Copy SERVER/*.
3. Copy EXTENSION/*.
4. Reload extension.
5. F5 Flow tab once.
6. Start run.bat.
7. Ctrl+F5 web UI.
