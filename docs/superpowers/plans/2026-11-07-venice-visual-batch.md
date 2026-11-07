# Venice Visual Reconstruction Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a source photo folder and editable master prompt into individual reconstruction prompts and new Qwen edited images using two fixed identity references.

**Architecture:** Extend the existing Venice HTTP client with a vision call. A small batch runner owns file enumeration, the two-call sequence, output persistence, and status. A new Qt widget embedded in the Venice tab collects inputs and runs the batch off the UI thread.

**Tech Stack:** Python, httpx, PySide6, Pillow, pytest.

## Global Constraints

- Edit model: `qwen-image-2-1-pro-edit`; resolution `2K`; aspect ratio `auto`; JPEG output; `safe_mode=false`.
- Edit images: first face/hair, second body. Never send the analyzed photo to multi-edit.
- The master prompt is editable text and may be loaded from TXT; no required file path.
- No paid calls during automated tests. Preserve unrelated WaveSpeed modifications.

---

### Task 1: Vision transport

**Files:** Modify `app/venice/client.py`; test `tests/test_venice.py`.

**Interfaces:** `VeniceClient.analyze_photo(*, model: str, master_prompt: str, image_bytes: bytes, mime_type: str) -> str`.

- [ ] Add a mock transport test that decodes the JSON body and asserts `POST /api/v1/chat/completions`, one text block containing the exact master prompt, and one `data:image/jpeg;base64,...` image block containing the exact source bytes. Assert the returned assistant text is used.
- [ ] Run `python -m pytest -q tests/test_venice.py -k analyze_photo` and see failure because the method is absent.
- [ ] Implement the method using `_request`, `max_completion_tokens=4096`, and response validation. Reject missing text and `finish_reason=length`.
- [ ] Re-run the focused test and verify it passes. Add one focused test for a truncated completion.

### Task 2: Batch runner and persistence

**Files:** Create `app/venice/batch.py`; test `tests/test_venice_batch.py`.

**Interfaces:** `BatchConfig(input_dir, output_parent, master_prompt, face_path, body_path, vision_model)` and `run_batch(client, profile, config, progress=None, stop_event=None) -> Path` returning the unique run directory.

- [ ] Write a failing test using two temporary source images and a fake client. Assert two vision calls each receive their own source bytes and every edit payload has exactly `[face_bytes, body_bytes]`, Qwen 2.1 Pro, 2K, auto, JPEG, and safe mode false.
- [ ] Run `python -m pytest -q tests/test_venice_batch.py`; verify the missing module fails.
- [ ] Implement sorted top-level enumeration, existing edit-reference validation, per-photo prompt and JPEG saving, and an atomic manifest updated after each photo. Validate source images with Pillow, reject an empty folder and equal input/output directories.
- [ ] Add focused tests for a failed photo followed by a successful one, a fatal 401/402/429 stop, and no retry after timeout. Re-run the batch tests.

### Task 3: Venice batch UI

**Files:** Create `app/ui/venice_batch.py`; modify `app/ui/venice.py`; test `tests/test_venice_ui.py`.

**Interfaces:** `VeniceBatchWidget(client, key_store, parent=None)` embedded as the second page of `VeniceEditWidget`.

- [ ] Write an offscreen Qt test that constructs the existing Venice tab, finds the Batch page and its editable master prompt, and proves the folder run dispatches a snapshot to a background worker without sending the source photo as an edit reference.
- [ ] Run `python -m pytest -q tests/test_venice_ui.py -k batch` with `QT_QPA_PLATFORM=offscreen`; verify the test fails for the missing page.
- [ ] Build folder/reference pickers, a TXT loader, vision model field, run/stop buttons, progress table, and selected prompt review. Preload the project-root master TXT if present. Keep UI changes inside Venice files, leaving `main_window.py` untouched.
- [ ] Re-run the UI tests and focused Venice tests.

### Task 4: Verification and handoff

**Files:** Update `README.md` with a short usage note only if the UI needs explanation.

- [ ] Run `python -m pytest -q tests/test_venice.py tests/test_venice_batch.py tests/test_venice_ui.py` with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `QT_QPA_PLATFORM=offscreen`.
- [ ] Run the project test suite once after focused tests pass; inspect the diff to ensure WaveSpeed files remain untouched by this change.
- [ ] Report local tests, UI check, live Venice call status, and visual quality separately. Do not call the batch complete from tests alone.
