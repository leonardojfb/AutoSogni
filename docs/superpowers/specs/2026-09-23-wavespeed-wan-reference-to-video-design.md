# WaveSpeed Wan 3.0 Reference-to-Video

## Goal

Add a standalone PySide6 `WaveSpeed` tab to AutoSogni for direct use of
`alibaba/wan-3.0/reference-to-video`, without changing the existing Sogni
campaign matrix or queue.

## Architecture

- `app/wavespeed/client.py` owns authenticated REST calls, presigned uploads,
  submission, polling, pricing, balance, deletion, and output download.
- `app/wavespeed/validation.py` owns the model request contract and local media
  limits.
- `app/wavespeed/schemas.py` normalizes prediction responses.
- `app/wavespeed/history.py` stores up to 100 local records while redacting
  remote URLs; it never stores an upload ticket or API key.
- `app/ui/main_window.py` composes the independent tab and runs network/media
  work off the Qt UI thread.

## Confirmed model request

The model endpoint is:

`POST https://api.wavespeed.ai/api/v3/alibaba/wan-3.0/reference-to-video`

The tab exposes `prompt`, up to 10 `reference_images`, up to 5
`reference_videos`, up to 5 `reference_audios`, `resolution` (`480p`, `720p`,
`1080p`), `aspect_ratio` (`16:9`, `9:16`, `1:1`, `4:3`, `3:4`), `duration`
(`2..30`), `enable_prompt_expansion`, `enable_audio`, and `seed`.

Reference video constraints enforced before upload are MP4/MOV, at most 100 MB
per file, and at most 5 files. The API remains authoritative for duration,
dimensions, aspect-ratio, and total reference-video duration checks.

## API-wide controls

The advanced panel exposes `enable_sync_mode`, `enable_base64_output`, and the
`webhook` query parameter. WaveSpeed documents sync and Base64 as supported only
by some models, so the controls remain explicitly advanced and the server
response is authoritative. Sync/Base64 are disabled in the UI when a webhook
URL is entered because WaveSpeed rejects those combinations.

The Playground's `Enable Safety Checker` control is not in the current model
REST schema. AutoSogni labels it as Playground-only and never sends an
undocumented safety parameter.

## Lifecycle

1. The user selects local references and writes a prompt.
2. Each file receives a WaveSpeed upload ticket, then its bytes are sent to the
   returned storage URL without an `Authorization` header.
3. AutoSogni sends the returned `download_url` values to the model endpoint.
4. Async tasks are polled via `GET /api/v3/predictions/{task-id}/result` every
   two seconds initially, increasing up to ten seconds.
5. Completed output URLs are downloaded to the selected local directory. Base64
   output is decoded locally.
6. The task and sanitized request metadata are recorded locally.

## Official sources

- Model page: https://wavespeed.ai/models/alibaba/wan-3.0/reference-to-video
- Model API reference: https://wavespeed.ai/docs/docs-api/alibaba/alibaba-wan-3.0-reference-to-video
- REST API: https://wavespeed.ai/docs/rest-api
- Authentication: https://wavespeed.ai/docs/api-authentication
- Upload files: https://wavespeed.ai/docs/upload-files-api
- Get result: https://wavespeed.ai/docs/get-result
- Sync mode: https://wavespeed.ai/docs/sync-mode
- Base64 output: https://wavespeed.ai/docs/base64-output
- Webhooks: https://wavespeed.ai/docs/how-to-use-webhooks
- Predictions: https://wavespeed.ai/docs/predictions-api
- Balance: https://wavespeed.ai/docs/check-balance
- Pricing: https://wavespeed.ai/docs/pricing-api
- Delete task: https://wavespeed.ai/docs/delete-task
- Error codes: https://wavespeed.ai/docs/error-codes
