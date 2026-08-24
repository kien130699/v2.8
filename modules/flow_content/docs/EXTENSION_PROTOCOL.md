# Extension v14.5.4 protocol used by Server V1

Extracted from the user-provided `flow_wardrobe_studio_extension_v14_5_4_FULL` package.

## Connection

- WebSocket default: `ws://127.0.0.1:8786/ws`
- Extension → server after connect:
  - `AGENT_HELLO`
- Server → extension:
  - `PING`
  - `RUN_FLOW_JOB`
  - `DOWNLOAD_MEDIA_FILES` (video recovery; V1 server does not need this for image tests)
- Extension → server:
  - `PONG`
  - `FLOW_JOB_ACCEPTED`
  - `FLOW_JOB_REJECTED`
  - `FLOW_JOB_RESULT`
  - `FLOW_JOB_INTERRUPTED`
  - `VIDEO_FILE_READY`
  - `VIDEO_FILE_ERROR`
  - `VIDEO_DOWNLOAD_SUMMARY`

## Current image result behavior

The v14.5.4 extension compacts image generation results into `FLOW_JOB_RESULT`:

```json
{
  "sceneId": 1,
  "imageState": "SUCCESS",
  "image": {
    "mediaId": "...",
    "url": "generatedImage.fifeUrl",
    "title": "..."
  }
}
```

Server V1 saves these as Assets and attempts to cache the signed URL locally.

Server V1 also understands a future `IMAGE_READY` event so the extension can later be patched to stream each completed image before the whole batch finishes.

## Supported UI model values in v14.5.4

Image:
- `Nano Banana Pro`
- `Nano Banana 2 Lite`
- `Nano Banana 2`
- `NONE`

Video:
- `Veo 3.1 - Lite`
- `Veo 3.1 - Fast`
- `Veo 3.1 - Quality`
- `Gemini Omni Flash`
- `NONE`

Image concurrency UI supports 1–10. Server V1 caps Factory Batch to 10 accordingly.
