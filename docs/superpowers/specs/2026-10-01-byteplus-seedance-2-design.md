# BytePlus Seedance 2.0 direct integration

## Goal

Add an independent PySide6 tab that invokes the official BytePlus ModelArk
`dreamina-seedance-2-0-260128` video-generation API, including all documented
Seedance 2.0 controls, persistent sequential campaigns, recovery of submitted
tasks, download of completed media, and local history.

## Scope and boundaries

- The provider is BytePlus ModelArk only, not the existing WaveSpeed reseller
  endpoint.
- The fixed model ID is `dreamina-seedance-2-0-260128`; Fast, Mini, and 2.5
  are deliberately not selectable.
- The API base URL is `https://ark.ap-southeast.bytepluses.com/api/v3` and
  requests use `Authorization: Bearer <BytePlus API key>`.
- BytePlus receives public asset URLs. This first slice does not implement TOS
  asset uploads or the private portrait asset library; the UI accepts direct
  public URLs and files whose URLs are entered by the operator. A local file
  cannot be silently submitted as a URL.
- Existing WaveSpeed key, client, campaigns, queue, history, output directory,
  and data files remain unchanged.
- Runtime state lives in new ignored files:
  `data/byteplus_api_key.txt`, `data/byteplus_campaigns.json`,
  `data/byteplus_history.json`, and `data/byteplus_outputs/`.

## Architecture

`app/byteplus` is a provider-owned package. `validation.py` converts the
explicit UI mode and ordered content items into the ModelArk request body;
`client.py` owns HTTP, error decoding, task polling, cancellation/deletion,
and downloading; `queue.py` owns serializable campaign state; and
`queue_runner.py` processes one persisted item at a time.

The new `BytePlusQueueWidget` is a narrow, provider-specific counterpart to
the existing WaveSpeed table. It displays and edits the same persisted fields
that the runner needs; it does not share a mutable WaveSpeed queue class.
`MainWindow` composes the tab and dispatches worker results through a dedicated
Qt event, so network activity never occurs on the UI thread.

## Request modes and payloads

The UI offers these mutually exclusive task modes. The mode selects content
roles and validation; it does not invent undocumented fields.

| Mode | Required ordered `content` |
| --- | --- |
| Text to video | One `text` item |
| First-frame image to video | `text`, one `image_url` with role `first_frame` |
| First/last-frame image to video | `text`, an image with role `first_frame`, then an image with role `last_frame` |
| Omni reference | `text`, 1–9 `reference_image`, 0–3 `reference_video`, 0–3 `reference_audio`; audio requires an image or video |
| Edit video | `text`, one `reference_video`, plus zero or more ordered references; extra body `omni_reference_task_type: edit` |
| Extend video | `text`, one `reference_video`; extra body `omni_reference_task_type: extend` |

Every submitted request includes the fixed model and selected content. Exposed
official controls are `generate_audio`, optional deterministic `seed`,
`resolution` (`480p`, `720p`, `1080p`, `4k`), `ratio` (`adaptive`, `21:9`,
`16:9`, `4:3`, `1:1`, `3:4`, `9:16`), duration 4–15 seconds, `watermark`,
`return_last_frame`, `callback_url`, and `execution_expires_after` (3,600 to
259,200 seconds). The UI prevents configurations the documentation excludes:
1080p is unavailable for image-reference tasks, 4K requires the fixed 2.0
model, and extension/edit controls require their reference video.

## Task lifecycle and persistence

Creating a task returns an ID. The app polls
`GET /contents/generations/tasks/{id}` until `succeeded`, `failed`,
`cancelled`, or `expired`; cancellation/deletion uses the documented task API.
On success it downloads `content.video_url`, and, if provided, writes the
returned last-frame URL as a separate image. The complete raw response is kept
only in the local history entry after redacting remote URLs and never contains
the API key.

Each queue item stores its request snapshot, task ID, status, local output
paths, error, timestamps, and usage. A task ID already saved in a recovered
queue is queried before any new submission. Running rows become pending after
restart but retain their task ID, preventing duplicate billable jobs. Retry
clears only remote result fields and preserves the request.

## UI and error handling

The tab has connection/key, request mode, prompt, ordered reference URL/file
lists, documented controls, immediate task controls, campaign/queue controls,
status/progress/raw response, and history. “Test connection” validates only
key presence locally because ModelArk has no documented zero-cost key-health
endpoint; a remote HTTP response is surfaced when an operator creates or
retrieves a task.

Validation happens before starting a worker. HTTP, malformed JSON, API error
objects, missing task IDs, unsupported task states, failed downloads, local
validation errors, and polling cancellation/timeouts produce visible Spanish
errors and are persisted on their queue item. A failed or expired output is
never marked downloaded.

## Verification

Tests use `httpx.MockTransport` and fake clients: no API key or paid request
is sent. Coverage proves exact API paths/auth/body, mode-specific payload
validation, file/URL safety, terminal-state polling, queue persistence and
recovery, runner non-duplication, and the visible tab controls. The full
pytest suite is run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.
