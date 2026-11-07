# Venice Visual Reconstruction Batch Implementation Plan

> **Executor: Luna 5.6 High.** Work through the numbered tasks in order. Read this document and the linked design before editing code. Do not infer requirements from the chat. Work in the existing `feature/venice-visual-batch` branch of `C:\Users\Wodahs\Downloads\TODOOOO\TODO\Proyectos codigo\AutoSogni`.

**Goal:** A single action in AutoSogni turns a folder of source photos into one new Qwen image per photo: `master prompt + source photo -> individual reconstruction prompt`; then `individual prompt + face reference + body reference -> output image`.

**Architecture:** Extend the existing `VeniceClient` with a vision chat request. Put file enumeration, two-stage orchestration, result persistence, and progress reporting in `app/venice/batch.py`. Add a focused Qt batch page under the existing Venice tab; leave the current single-image editor intact.

**Tech stack:** Python, `httpx`, Pillow, PySide6, pytest. Use the project's existing dependencies and key store.

**Approved design:** `docs/superpowers/specs/2026-11-07-venice-visual-batch-design.md`.

## Confirmed exemplar and exact contract

- The user selected **`data/venice_outputs/venice_20261102_170606_408e9540.jpg`** as the successful target behavior. Its record is in `data/venice_history.json`.
- Stage 2 must use `modelId: qwen-image-2-1-pro-edit`, `resolution: 2K`, `aspect_ratio: auto`, `output_format: jpeg`, `safe_mode: false`.
- Stage 2 has exactly **two** `images`, in this order: face/hair identity, then body anatomy/proportions. The selected source photo is never sent to `/image/multi-edit`.
- The exemplar's stored final prompt is 6,594 characters. It is an *output example*, not the master prompt to repeat for every photo.
- The user-provided master instruction currently exists as untracked `QWEN_2_1_Pro_Edit_Visual_Reconstruction_Master.txt` in the AutoSogni root (SHA-256 `0904D62295C2CA14A6088C9B2AC14FD44E58683007BE8C5117D49EC74DC2DFAC`). It is 19,903 bytes. **Read its text as the default editable field; do not require the file to exist; do not alter or commit it.** The master tells the vision model to return one complete English Qwen prompt and no analysis or JSON.
- One **Process folder** action must do both stages for every source photo automatically. Prompt review is optional and never pauses the batch.

## Working-tree guardrails

The branch was created from commit `fef1faf`, which contains the Venice tab but is 14 commits ahead of `origin/feature/wavespeed-seedance-flux`. The branch has a documentation commit `2f7a17f`. Before changing anything, run `git status --short --branch`. The following pre-existing changes belong to other work: `app/ui/main_window.py`, `app/wavespeed/client.py`, `app/wavespeed/validation.py`, `tests/test_ui_frame_preview.py`, `tests/test_wavespeed.py`, `app/wavespeed/flux3_multi.py`, `tests/test_flux3_multi.py`, `data/`, `errorlogs.txt`, and the untracked master prompt. Do not reset, clean, move, edit, stage, or commit them. No new worktree or branch is needed.

Files this feature may change:

| File | Responsibility |
| --- | --- |
| `app/venice/client.py` | Vision API call and response validation; retain existing multi-edit behavior. |
| `app/venice/batch.py` (new) | Input validation, deterministic enumeration, two-stage calls, persistence, progress, stop behavior. |
| `app/ui/venice_batch.py` (new) | Batch form, background worker, progress table, optional prompt review. |
| `app/ui/venice.py` | Embed the batch widget as a second page inside the Venice tab; preserve the single-image page. |
| `tests/test_venice.py` | Mock HTTP tests for the new client call. |
| `tests/test_venice_batch.py` (new) | Real temporary files and fake client to verify complete orchestration. |
| `tests/test_venice_ui.py` | Offscreen batch UI smoke tests. |
| `README.md` | Brief usage instructions after implementation. |

## External API contract

Venice's [vision guide](https://docs.venice.ai/guides/features/vision) supports a local image as a `data:image/<type>;base64,...` block in chat completions. The [multi-edit guide](https://docs.venice.ai/guides/media/image-editing) says the first input image is the base and subsequent images are references; here the first *identity* image is the base because the source photo is represented only by its written reconstruction.

Stage 1 request, using the same `VENICE_API_KEY` / `ApiKeyStore` already used by the Venice tab:

```json
{
  "model": "deepseek-v4-1-flash-uncensored",
  "max_completion_tokens": 4096,
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "<exact editable master prompt>"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<bytes of current source photo>"}}
    ]
  }]
}
```

`deepseek-v4-1-flash-uncensored` is the default **editable** prompt model. The user's Venice catalog showed `supportsVision: true` on 2026-11-07; verify availability again when running. Send exactly one source photo per vision call. Preserve the master prompt text, except for normal transport serialization. Support JPEG, PNG, and WebP MIME types. Reject empty text, missing `choices[0].message.content`, and `finish_reason == "length"`. Do not pass a truncated prompt to Qwen. Do not log the prompt, image Base64, key, or full HTTP body.

Stage 2 request must be built through existing `build_multi_edit_payload` and `VeniceClient.multi_edit`; it should be equivalent to:

```json
{
  "modelId": "qwen-image-2-1-pro-edit",
  "prompt": "<exact stage-1 text for this source photo>",
  "images": ["<raw base64 of face/hair image>", "<raw base64 of body image>"],
  "aspect_ratio": "auto",
  "resolution": "2K",
  "output_format": "jpeg",
  "safe_mode": false
}
```

Validate the prompt against the live Qwen profile's `prompt_character_limit` before this call. Validate both references with existing `validate_prompt_and_images` or `validate_local_image`. If Qwen 2.1 Pro Edit is absent from `client.list_edit_models()`, stop before stage 1 and report the missing model. Do not substitute another image model silently.

## Output contract

On each run, create one unique directory under the user-selected output parent, e.g. `venice_batch_20261107_143000_ab12cd34`. Do not put outputs in the source folder or overwrite an earlier run. Scan **only the immediate source directory**, sort supported extensions case-insensitively, and accept `.jpg`, `.jpeg`, `.png`, `.webp`. Ignore subfolders and unrelated files. Fail preflight if no supported photos are found or source and output parent resolve to the same directory.

For each input, use a stable numeric prefix and sanitized source stem. Example: `0001_photo-name.prompt.txt` and `0001_photo-name.jpg`. Decode returned image bytes and verify their format matches the MIME type before recording success. If the provider returns another allowed image MIME type despite requesting JPEG, save with the matching extension; never label PNG bytes `.jpg`. Save prompt text immediately after successful stage 1, *before* stage 2. Write image bytes via a temporary file and atomic replace; remove temp files on failure. Do not copy source photos into the output directory.

Write `manifest.json` atomically after preflight and after every state change. Use a concrete schema like:

```json
{
  "schema_version": 1,
  "created_at": "2026-11-07T17:30:00Z",
  "vision_model": "deepseek-v4-1-flash-uncensored",
  "edit_model": "qwen-image-2-1-pro-edit",
  "edit_options": {"resolution": "2K", "aspect_ratio": "auto", "output_format": "jpeg", "safe_mode": false},
  "master_prompt_sha256": "<hash>",
  "references": [{"slot": 1, "filename": "face.jpg"}, {"slot": 2, "filename": "body.png"}],
  "items": [{"source": "photo-name.jpg", "stage": "done", "status": "completed", "prompt_file": "0001_photo-name.prompt.txt", "output_file": "0001_photo-name.jpg", "error": null}]
}
```

Keep filenames only in the manifest: no absolute paths, API key, headers, image Base64, or complete prompts. Prompt text belongs in each `.prompt.txt`. Use sanitized, short error messages. A photo-specific invalid image, vision error, or edit error marks that item failed and lets later photos continue. A 401, 402, or 429 from either Venice call stops the run so the same account/rate problem is not repeated for every photo. A timeout must **never trigger automatic retry**; the provider might have charged. A Stop click finishes the in-flight request, saves anything received, and prevents the next source photo. The final UI status reports completed, failed, and stopped counts. Closing the top-level window while active requests Stop and keeps the window open until the worker finishes.

### Runner sequence to preserve

```python
profile = client.list_edit_models().get("qwen-image-2-1-pro-edit")
if profile is None:
    raise ValueError("Qwen Image 2.1 Pro Edit is unavailable")
# Validate the shared inputs once; create the run directory and manifest.
face_b64 = base64.b64encode(face_path.read_bytes()).decode("ascii")
body_b64 = base64.b64encode(body_path.read_bytes()).decode("ascii")
for index, source in enumerate(sorted_sources, start=1):
    if stop_event.is_set():
        break
    # Validate this source. On a photo-specific error, record it and continue.
    prompt = client.analyze_photo(
        model=vision_model, master_prompt=master_prompt,
        image_bytes=source.read_bytes(), mime_type=source_mime,
    )
    # Save prompt immediately. If too long for Qwen, record failure; do not edit.
    payload = build_multi_edit_payload(
        profile=profile, prompt=prompt, images=[face_b64, body_b64],
        aspect_ratio="auto", resolution="2K", output_format="jpeg", safe_mode=False,
    )
    result = client.multi_edit(payload)  # Exactly one attempt per photo.
    # Atomically save result, then record completion and notify the UI.
```

The sketch fixes the call order and payload ownership; implement per-photo exception handling and atomic writes around it. Do not call `multi_edit` in a `finally` block, and do not put the source image in `images`.

## Task 1 — Add the vision transport (TDD)

1. In `tests/test_venice.py`, write `test_analyze_photo_sends_exact_master_and_one_source_image`. Use `httpx.MockTransport`; assert URL `/api/v1/chat/completions`, Bearer header, model, `max_completion_tokens`, exact master text, and exactly one `image_url` block. Decode the data URL to assert it contains the source bytes. Return `{"choices":[{"finish_reason":"stop","message":{"content":"final prompt"}}]}` and assert `"final prompt"` is returned.
2. Run `python -m pytest -q tests/test_venice.py -k analyze_photo` with the environment described below. It must fail because `analyze_photo` does not exist.
3. Add `VeniceClient.analyze_photo(*, model: str, master_prompt: str, image_bytes: bytes, mime_type: str) -> str` to `app/venice/client.py`. Validate nonempty input and allowed MIME, Base64 encode the bytes, call existing `_request`, parse the response defensively, return only stripped assistant text. Raise `VeniceApiError` on malformed/empty/truncated output. Reuse `_request`'s input-redacted provider error behavior.
4. Add tests for empty content and `finish_reason="length"`; verify neither returns a prompt. Run all `tests/test_venice.py`.

## Task 2 — Implement the batch runner (TDD)

1. In new `tests/test_venice_batch.py`, create real tiny JPEG/PNG/WebP files with Pillow in `tmp_path` and a fake client whose `analyze_photo` returns a distinct prompt per input. The fake `multi_edit` returns `VeniceImageResult` with valid image bytes. Assert two source photos yield two prompt files, two images, and manifest entries. Decode both `images` in each captured edit payload and assert they are always `[face_bytes, body_bytes]` and **never either source photo**. Assert all exemplar settings exactly.
2. Run the new test and see failure because `app.venice.batch` is absent.
3. Create `app/venice/batch.py` with `BatchConfig` (input directory, output parent, master text, face path, body path, vision model), `BatchEvent` (index, filename, stage, status, output/prompt path or sanitized error), and `run_batch(client, profile, config, progress=None, stop_event=None) -> Path`. Keep API calls sequential to respect ordering and avoid uncontrolled charges. Snapshot input paths and master text before starting. Preflight the folder, enumerated filenames, options, and both shared identity references before any paid request. Validate each source photo immediately before its own vision call so one corrupt source does not block later photos. Fetch the Qwen profile in the UI worker before stage 1.
4. Add focused tests for: filenames sorted and unique even when two sources share a stem; empty folder rejected before calls; a photo-specific error followed by a successful photo; 401/402/429 halting later calls; timeout never retried; prompt longer than `profile.prompt_character_limit` saved and flagged without edit call; stop after current photo; and manifest containing no key, Base64, or absolute paths. Write manifest after every photo and leave earlier outputs intact.
5. Run `python -m pytest -q tests/test_venice_batch.py` until green. Do not add a generic queue framework, database table, or dependency.

## Task 3 — Add one-click UI (TDD)

1. Add an offscreen test in `tests/test_venice_ui.py` that constructs `VeniceEditWidget` with the existing fake client. Assert a Batch page is present, its master text field is editable, and `Process folder` creates one immutable snapshot of all chosen paths/text before starting a background thread. Do not require clicking between source photos. Use fake calls only.
2. Run the focused UI test and see a missing-page failure.
3. Create `app/ui/venice_batch.py` with `VeniceBatchWidget(client, key_store, parent=None)`. Include: source folder picker; output parent picker; face/hair image picker; body image picker; editable vision-model field; large editable master-prompt field; `Load TXT` button; `Process folder` and `Stop` buttons; progress table with source/status/output; read-only prompt preview for the selected row; final output-folder label. Default the master text from `Path(__file__).resolve().parents[2] / "QWEN_2_1_Pro_Edit_Visual_Reconstruction_Master.txt"` if that file exists; otherwise leave the field empty. The file is a convenience, not a dependency. Never save or display the API key in the batch manifest.
4. Use `QObject` signals for worker-to-UI updates, following `app/ui/venice.py`. Run the synchronous runner in a daemon thread, never on the Qt UI thread. Disable `Process folder` while active. `Stop` sets a `threading.Event`; it does not interrupt an HTTP request or discard a received response. Keep control values unchanged on failure so the user can correct them.
5. In `app/ui/venice.py`, wrap the existing editor controls in a `QTabWidget` with **Single image** and **Batch** pages. Instantiate the batch page with the same `VeniceClient` and Venice key store. Do not edit `app/ui/main_window.py`; it has unrelated dirty WaveSpeed changes. Existing single-image tests must continue to pass.
6. Add a UI test for missing master text and references: show a readable error before starting any worker/API call. Add a UI test that status events appear in the table and saved prompt is available for optional review. Run `tests/test_venice_ui.py` offscreen.

## Task 4 — Documentation and verification

1. Add a short README section: select the Venice Batch page, source folder, identity references in order, output parent, editable master text, and Process folder. Explain that the generated prompts are saved next to images and that a run uses two Venice calls per photo.
2. Inspect `git diff` for the allowlisted files only. Verify the untracked master TXT, `data/`, and WaveSpeed changes were not staged or changed by this feature.
3. Run focused tests on Windows PowerShell:

   ```powershell
   $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
   $env:QT_QPA_PLATFORM='offscreen'
   python -m pytest -q tests/test_venice.py tests/test_venice_batch.py tests/test_venice_ui.py
   ```

4. Run the full suite once with the same environment: `python -m pytest -q`. If unrelated pre-existing dirty WaveSpeed work fails, identify those failures without changing that work.
5. Do a local offscreen UI smoke check. A paid Venice call is **not** part of automated verification and must not be inferred from mocks. Report separately: test results, Qt UI smoke result, live Venice request status, and visual quality review status.
6. Commit only allowlisted feature files with a focused message. Do not stage with `git add .`. Leave the branch ready for the user's later review in this chat; do not merge, deploy, or process the user's photo folder as part of implementation.

## Acceptance checklist

- [ ] One click processes all supported photos in a chosen folder without per-photo confirmation.
- [ ] Vision sees the master prompt and exactly one current source photo; Qwen sees the resulting text and exactly two identity references in the approved order.
- [ ] Qwen edit settings exactly match job `408e9540`; no silent fallback to a different edit model.
- [ ] Each successful photo has a new image, its own prompt TXT, and a manifest entry. Earlier successes survive later failures.
- [ ] A partial/truncated vision response, too-long final prompt, missing model, bad reference, and fatal provider status never trigger an image edit for that photo.
- [ ] User can optionally inspect prompts after generation; the batch never pauses for review.
- [ ] Existing single-image Venice behavior and unrelated WaveSpeed work remain intact.
