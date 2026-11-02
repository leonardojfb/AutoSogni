# Venice Multi-Edit Integration Design

**Date:** 2026-11-02  
**Status:** Approved design  
**Scope:** Add a Venice image-editing tab inside the existing AutoSogni PySide6 application.

## Goal

Add a `Venice` tab to AutoSogni that edits one to three ordered local reference images through Venice's synchronous `/api/v1/image/multi-edit` endpoint, displays and saves the returned image, and records a sanitized local history without changing the existing Sogni, WaveSpeed, or BytePlus flows.

## Verified Venice contract

The integration is based on the official Venice documentation and the live public model catalog checked on 2026-11-02:

- Base URL: `https://api.venice.ai/api/v1`.
- Authentication: `Authorization: Bearer <VENICE_API_KEY>`.
- Endpoint: `POST /image/multi-edit`.
- The operation is synchronous and returns raw image bytes, not a task ID or polling response.
- JSON image inputs may be raw Base64 strings, data URLs, or HTTP(S) URLs. This app will use raw Base64 so local images do not need to be uploaded to a third party.
- The first image is the base/composition image; later images are additional references. The visible UI order is the request order.
- Common input limits: files under 25 MB, minimum 256×256 pixels, total input pixels no greater than 33,177,600.
- Supported endpoint fields relevant to this feature: `modelId`, `prompt`, `images`, `aspect_ratio`, `resolution`, `output_format`, and `safe_mode`.
- No polling, automatic generation retry, remote cancellation, `seed`, `steps`, `guidance`, `negative_prompt`, or WaveSpeed-specific fields will be used.

Official references:

- https://docs.venice.ai/guides/media/image-editing
- https://docs.venice.ai/api-reference/endpoint/image/multi-edit
- https://docs.venice.ai/models/image
- https://api.venice.ai/api/v1/models?type=inpaint

## Models

The UI will expose only the requested IDs after catalog verification. The current catalog confirms all four:

| UI name | ID | Current max inputs | Resolutions | Aspect ratios | Prompt limit |
| --- | --- | ---: | --- | --- | ---: |
| Qwen Image 2.1 Pro Edit | `qwen-image-2-1-pro-edit` | 10 | `1K`, `2K` | `auto`, `1:1`, `3:2`, `16:9`, `21:9`, `9:16`, `2:3`, `3:4`, `4:5` | 10,000 |
| Nano Banana Pro Edit | `nano-banana-pro-edit` | 6 | `1K`, `2K`, `4K` | `auto`, `1:1`, `3:2`, `16:9`, `21:9`, `9:16`, `2:3`, `3:4`, `4:5` | 32,768 |
| Seedream V5 Pro Edit | `seedream-v5-pro-edit` | 6 | `1K`, `2K` | `auto`, `1:1`, `3:2`, `16:9`, `9:16`, `2:3`, `3:4`, `4:3` | 10,000 |
| FLUX 2 Max Edit | `flux-2-max-edit` | 6 | catalog-defined; omit when unavailable | `auto`, `1:1`, `3:2`, `16:9`, `21:9`, `9:16`, `2:3`, `3:4`, `4:5` | 3,000 |

The UI will default to Qwen Image 2.1 Pro Edit only when that ID is present in the verified catalog. If catalog verification fails, generation is disabled and the tab explains that availability must be refreshed; it will not silently assume a model is available.

The user-facing layout intentionally presents three semantic slots even though the provider permits more references. The feature scope is one to three images, so the fourth and later catalog capacity is not exposed as undocumented extra slots.

## Architecture

### `app/venice/client.py`

Owns Venice HTTP behavior only:

- `VeniceClient` stores the API key in memory and uses an injectable `httpx.Client` for tests.
- `list_edit_models()` fetches the public `GET /models?type=inpaint` catalog and returns model metadata.
- `multi_edit(model_id, prompt, images, options)` sends the exact JSON request and returns an immutable result containing response bytes, content type, request ID, and response headers.
- The client never writes files, logs API keys, or stores Base64 inputs.
- `VeniceApiError` retains sanitized status/error information and maps invalid credentials, insufficient balance, request validation, rate limits, provider capacity, and timeouts to user-readable messages.

### `app/venice/validation.py`

Contains model metadata structures, allowed model IDs, field capability extraction, local image checks, and `build_multi_edit_payload(...)`.

Payload construction is model-profile driven. Optional controls are included only when the selected model's verified profile supports them. The builder receives the already-entered prompt and never rewrites it.

### `app/venice/history.py`

Stores at most the recent 100 sanitized records in `data/venice_history.json`. Records include provider, exact model ID, literal prompt, ordered reference filenames, parameters actually sent, status, timestamps, request ID when supplied, output path, and sanitized error text. Base64 data, API keys, authorization headers, and absolute source paths are excluded.

### `app/ui/venice.py`

Owns a self-contained `VeniceEditWidget` so the large existing `MainWindow` remains a thin integration point. The widget uses the existing PySide6 conventions and `data_dir()` output paths, but never imports WaveSpeed builders or polling code.

The widget runs catalog loading and edit requests on daemon worker threads. Completion is delivered back to the Qt UI through signals. A request button is disabled for the active request, and a failed request leaves the prompt and references untouched. Waiting locally can be abandoned by closing/continuing the UI, but no remote cancellation control is shown because the endpoint is synchronous and has no documented cancellation API.

### `app/ui/main_window.py`

Only the following wiring is added:

- construct a separate `ApiKeyStore(data_dir() / "venice_api_key.txt")` and `VeniceClient`;
- add `_venice_tab()` or the `VeniceEditWidget` to the root tabs;
- preserve the existing dirty WaveSpeed-related changes and all current tab behavior.

## UI and data flow

The tab contains:

1. A password API-key field, save button, and connection/catalog refresh status.
2. A model selector with the four approved display names and exact IDs available through tooltips/status text.
3. A multiline prompt field that is sent literally.
4. Three ordered reference rows:
   - `Imagen 1 — Escena y composición`
   - `Imagen 2 — Rostro y cabello`
   - `Imagen 3 — Cuerpo y proporciones`
5. Per-row select/replace/remove controls and up/down reordering. Empty rows are allowed; non-empty rows compact into the exact visible order sent to Venice.
6. Dynamic controls for verified model fields: aspect ratio, resolution, output format, and safe mode. Unsupported values are removed when the model changes.
7. Generate button, status/progress indicator, returned image preview, output folder chooser, and download/save status.
8. A sanitized Venice history table.

Before submission:

1. Snapshot prompt, ordered paths, selected model profile, options, and output directory on the UI thread.
2. Validate count, prompt length, file extension/size/dimensions, and model-specific controls.
3. Encode each local file to Base64 in the same snapshot order.
4. Build and validate the exact JSON payload.
5. Send one synchronous request with no automatic retry.

After submission:

1. On success, save returned bytes to `data/venice_outputs/venice_<request-or-timestamp>.<format>` or the user-selected output directory.
2. Display the saved image in the tab and retain a download/open-file action through the normal local file path.
3. Append a sanitized history record.
4. On error, show a concise provider error while retaining all entered data and append a failed history record without secrets.

## Error handling and safety

- `401`: show that the Venice API key is invalid or rejected.
- `402`: show insufficient Venice balance.
- `400`/`415`: show that the request schema or content type is invalid.
- `413`: show the 25 MB image limit.
- `422`: show image-size or content-policy rejection without bypass attempts.
- `429`/`503`: show rate limiting or model capacity; do not automatically resubmit a generation request.
- timeout/network failure: show that the local wait failed and do not claim that the remote operation was cancelled or definitely not processed.
- API keys are stored only through the existing local key-store convention and are excluded from logs/history/errors.
- Base64 image inputs are never written to history or debug output.

## Testing strategy

Tests will use `httpx.MockTransport`, temporary files/directories, and Qt offscreen mode where UI coverage is needed. No paid Venice generation will be called.

Coverage:

- catalog parsing exposes only requested available models and selects Qwen by default when present;
- model-specific payloads include exact fields and omit incompatible controls;
- one, two, and three references preserve order and reject invalid counts/limits;
- prompt text is unchanged;
- local file limits and image dimensions are enforced;
- binary response bytes are saved and previewed;
- request IDs/content types are handled;
- 401/402/413/422/429/503/error-body/timeout cases are sanitized;
- history contains filenames and order but no key, authorization header, Base64, or absolute source paths;
- generate-button locking prevents duplicate clicks;
- existing test suite remains passing.

## Explicit non-goals

- No separate Venice application or executable.
- No Venice video, text, background-removal, or prompt-enhancement workflow.
- No automatic prompt rewriting or hidden instructions.
- No provider-side upload cache, polling queue, automatic generation retry, or remote cancellation claim.
- No modification of current Sogni, WaveSpeed, or BytePlus contracts.
