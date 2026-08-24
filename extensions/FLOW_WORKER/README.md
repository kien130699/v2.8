# Flow Extension v14.5.21 — IMAGE_MIX PER-SCENE FIX

FULL unpacked Chrome extension source based on v14.5.20 FULL.

Critical change:
- Each scene has makeVideo / metadata.makeVideo.
- Blank videoPrompt is valid for IMAGE_ONLY scenes inside an IMAGE_MIX job.
- videoPrompt is required only when makeVideo=true.
- After an image-only scene succeeds: videoState=SKIP and scene done=true.
- Only makeVideo scenes enter VIDEO queue.
- Legacy jobs fall back to videoPrompt presence.

Install:
1. chrome://extensions
2. Developer mode ON
3. Remove/disable old Flow extension copies
4. Load unpacked this v14.5.21 folder
5. Reload the Flow tab

Matching server: Flow Content Factory V2.14.14.
