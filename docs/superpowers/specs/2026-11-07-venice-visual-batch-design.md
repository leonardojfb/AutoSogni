# Venice visual reconstruction batch

## Goal

Process a folder of photographs in AutoSogni. For each source photo, send the user's editable visual reconstruction master prompt and that photo to a Venice vision model. Send the returned text to Qwen Image 2.1 Pro Edit with exactly two identity references, then save a new image and the text prompt.

## User flow

Add a Batch page inside the existing Venice tab. The user chooses a source folder, an output parent folder, the face/hair reference (first), the body reference (second), a vision model, and a master prompt. The master prompt is editable text; loading a TXT file is a convenience, not a requirement. If the project's `QWEN_2_1_Pro_Edit_Visual_Reconstruction_Master.txt` exists, load it into the text field by default without changing the file.

One **Process folder** action processes the supported photos in filename order. No confirmation is required between photos. The page shows progress and each result. Prompts and images are saved as they arrive, so a later failure does not discard earlier work. The user may stop between photos. A prompt review area and saved prompt files make inspection optional.

## Provider contract

Stage 1 calls `POST /api/v1/chat/completions` with the master prompt and one local photo as a `data:image/...;base64,...` image block. The prompt model defaults to `deepseek-v4-1-flash-uncensored` and can be edited. The model was present in the user's Venice catalog with `supportsVision: true` on 2026-11-07. Reject empty or length-truncated responses and prompts exceeding the selected Qwen edit model's current character limit.

Stage 2 calls the existing Venice multi-edit client with `modelId=qwen-image-2-1-pro-edit`, `resolution=2K`, `aspect_ratio=auto`, `output_format=jpeg`, `safe_mode=false`, and `images=[face_reference, body_reference]`. The source photo is never sent in stage 2. These are the settings recorded for output `venice_20261102_170606_408e9540.jpg`.

## Files and failure handling

Create a unique run directory inside the selected output parent. Save a prompt TXT and edited image for each photo using numbered source-based names. Validate that returned image bytes decode and match their declared format before recording completion. Save a manifest with the source filename, stage, status, model IDs, prompt filename, output filename, and sanitized error, updating it after each photo. Do not store API keys, headers, Base64, absolute source paths, or source images in the manifest. Do not overwrite originals or prior runs. Continue after a photo-specific validation or generation error; stop on authentication, payment, or rate-limit errors so the same failing request is not repeated across the folder. Do not automatically retry ambiguous timeouts or paid edit calls. The UI reports completed, failed, and stopped counts. If the main window is closed during a batch, request Stop and defer closing until the in-flight photo finishes.

## Boundaries and validation

Read supported JPG, PNG, and WebP files in the source folder, without descending into subfolders. Validate the selected references using the existing Venice validation. Use the existing `VeniceClient` and key store. Keep the current single-image editor's behavior. The current working tree contains unrelated WaveSpeed changes; implementation must touch only Venice and its tests/docs.

## Verification

Use mock HTTP responses to prove the vision request includes exactly the current source photo and master prompt, while the edit request contains only the two ordered identity references. Verify per-photo filenames, prompt persistence, manifest updates, continuation after a per-photo error, stopping on fatal provider errors, and no duplicate paid retry. Run the focused Venice tests and an offscreen Qt UI test. A local mock test cannot prove provider-side visual quality; report paid-call and visual-review status separately.
