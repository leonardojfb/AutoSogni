# Nano Banana Pro and Qwen Image 3.0 Edit

## Goal

Add two independent image-editing workspaces under **Imágenes**, backed by the existing WaveSpeed key, upload cache, price estimate, asynchronous submission, output download, and history store.

## Model contracts

- `google/nano-banana-pro/edit`: prompt and one to fourteen source images; optional aspect ratio (`1:1`, `3:2`, `2:3`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`), resolution (`1k`, `2k`, `4k`), PNG/JPEG output, sync mode, and Base64 output.
- `alibaba/qwen-image-3.0/edit`: prompt and one to three source images; optional aspect ratio, `1k` or `2k` resolution, prompt expansion, and a seed from `-1` through `2147483647`.

## Architecture

Each provider has a model-specific payload builder in `app/wavespeed/validation.py`; `WaveSpeedClient` dispatches validation by model ID. `MainWindow` exposes one tab per provider and uses a shared local-image list helper plus the established upload and generation lifecycle. No queue is added because neither requested model has an existing persistent queue contract.

## Error handling and verification

Validation occurs before uploads or billable submission. The UI reports asynchronous errors through the existing WaveSpeed event path and writes terminal results to `wavespeed_history.json`. Focused tests prove valid payloads, invalid image limits/options, client routing, tabs, uploaded payloads, and selected model IDs; the full suite verifies regressions.
