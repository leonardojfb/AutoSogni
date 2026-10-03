# BytePlus Seedance 2.0 Direct Integration

## Goal

Provide a provider-specific PySide6 workflow for the official BytePlus ModelArk
`dreamina-seedance-2-0-260128` model, including task management, sequential
campaigns, reference media, persistence, recovery, and local downloads.

## Scope

- Use a BytePlus API key and ModelArk client independent of WaveSpeed.
- Support text, first-frame, first/last-frame, omni-reference, edit, and extend
  task modes with the documented Seedance 2.0 fields and limits.
- Persist campaigns and sequential queue items; recover remote tasks by ID,
  pause/resume, retry failures, and avoid duplicate submissions after restart.
- Download completed video and an optional returned last frame; retain local
  task history and expose task status and API errors.
- Estimate costs for queued work where pricing data is available.
- Do not include private portrait libraries, person verification, or other
  model variants such as Seedance Fast, Mini, or 2.5.

## Architecture

`app/byteplus/` owns request validation, the HTTP client, task and campaign
state, history, pricing, media preparation, and upload adapters. The UI uses
dedicated BytePlus references and queue widgets. Network work runs outside the
Qt event thread. BytePlus keys, campaigns, history, and outputs remain separate
from WaveSpeed and Sogni state.

The client targets `https://ark.ap-southeast.bytepluses.com/api/v3` and sends
`Authorization: Bearer <API key>`. The model ID is fixed to
`dreamina-seedance-2-0-260128`.

## Requests and Media

Every request contains the prompt and compatible documented controls:
`generate_audio`, optional `seed`, `resolution`, `ratio`, `duration`,
`watermark`, `return_last_frame`, `callback_url`, and
`execution_expires_after`. Validation enforces supported values and mode-specific
limits before submission.

Reference items are ordered and assigned their documented roles in `content`.
Public URLs are accepted directly; local reference files are prepared and sent
through the configured asset uploader. Local filesystem paths are never sent as
public URLs. The uploader can use the supported BytePlus asset flow or the
configured HTTPS upload service.

## Task Lifecycle

Queue items retain their request snapshot, task ID, price estimate, status,
output paths, and error. The runner polls until a terminal state, downloads
successful results, and saves state after transitions. A recovered task ID is
queried before any retry to avoid duplicate billable work.

## Local Data and Security

API keys, campaign files, history, and generated outputs are runtime data under
`data/` and must remain local/ignored. Keys are not included in request history,
logs, or error messages. Tests use mocked HTTP transports and fake uploaders;
they do not require credentials or paid API calls.

## Verification

Tests cover payload validation, authentication and task responses, uploads,
cost estimation, persistence and recovery, UI controls, and failure handling.
