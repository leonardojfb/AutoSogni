# Sogni Video Automator

Local Windows desktop app for persistent Frame x Prompt image-to-video campaigns through the Sogni API, plus a standalone WaveSpeedAI Wan 3.0 reference-to-video tab.

## Run

```powershell
py -m pip install -r requirements.txt
py -m app.main
```

Set `SOGNI_API_KEY` or save it in the Settings tab. The app stores local state in `data/app.db`.

## Current Flow

1. Fetch video models from Sogni.
2. Select a frames folder (`.png`, `.jpg`, `.jpeg`, `.webp`).
3. Import prompts from `.json`, `.csv`, or `.txt`.
4. Create a campaign. All Frame x Prompt jobs are persisted before execution.
5. Choose duration mode: manual seconds or automatic detection from each prompt.
6. Choose the video format/aspect ratio, such as `9:16`, `16:9`, `1:1`, or `4:5`.
7. Start/resume the queue. Default concurrency is 1.
8. Soft pause sets the campaign to `PAUSE_REQUESTED`; the active job is allowed to finish.

## WaveSpeedAI tab

The `WaveSpeed` tab directly integrates `alibaba/wan-3.0/reference-to-video`.
It supports local image/video/audio uploads, all documented model parameters,
advanced sync/Base64/webhook controls, price and balance checks, async polling,
output download, task inspection/deletion, and a sanitized local history. It is
intentionally independent from the Sogni campaign queue.

The WaveSpeed section also has a sequential queue: select one shared frame,
add one reference video per row, and edit each row's prompt, resolution, aspect
ratio, duration, audio, prompt expansion, and seed. The queue submits only one
task at a time, waits for it to finish and saves its output before moving to the
next row. Failed rows are marked `Fallido`, do not stop later rows, and expose a
`Reintentar` action. The queue is stored locally in
`data/wavespeed_queue.json`; API keys and temporary upload URLs are never
stored there. Queue runs use asynchronous task polling, so webhook, sync mode,
and Base64 output are disabled for that workflow. Use `Estimar costos` to see
the price of every row and the total before submitting. When an image is
loaded in the references panel, `Agregar a la cola` in the
`Estimación, salida y ejecución` panel adds the current videos with that image
as the shared frame. Repeated additions with the same frame name append jobs to
the same queue; a different frame name is rejected instead of mixing frames.
Queues are saved as named WaveSpeed campaigns in
`data/wavespeed_campaigns.json`. The campaign selector restores the last active
campaign on startup, and the previous single-queue file is migrated
automatically the first time this feature is opened.

## Sogni API Notes

The integration uses the current Sogni docs as of 2026-09-14:

- Model catalog: `GET /v1/model-catalog?mediaType=video&include=parameters`
- Workflow start: `POST /v1/creative-agent/workflows`
- Idempotency header: `Idempotency-Key`
- Workflow read/polling: `GET /v1/creative-agent/workflows/:id`
- Image pre-upload: `GET /v2/image/uploadUrl`

API-specific behavior is isolated in `app/sogni/`.
