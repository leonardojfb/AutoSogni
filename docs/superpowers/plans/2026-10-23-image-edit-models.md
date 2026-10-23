# Image Edit Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Nano Banana Pro Edit and Qwen Image 3.0 Edit to the Imágenes workspace.

**Architecture:** Validate each API schema independently, route it through `WaveSpeedClient`, and reuse the existing local-file upload, worker, history, and output paths from Flux Edit.

**Tech Stack:** Python 3.14, PySide6, httpx, pytest.

## Global Constraints

- Preserve `data/`, keys, history, campaigns, outputs, builds, distributions, and logs.
- Validate before upload and before a billable submit.
- Do not send one model's optional fields to the other model.

---

### Task 1: Model contracts and client routing

**Files:**
- Modify: `app/wavespeed/validation.py`
- Modify: `app/wavespeed/client.py`
- Test: `tests/test_wavespeed.py`

- [ ] Add failing tests for Nano Banana's fourteen-image maximum, its output settings, Qwen's three-image maximum, and Qwen's seed and expansion settings.
- [ ] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .\\.venv\\Scripts\\python.exe -m pytest tests/test_wavespeed.py -q`; expect an import failure for the new builders.
- [ ] Add `NANO_BANANA_PRO_MODEL_ID`, `QWEN_IMAGE_3_EDIT_MODEL_ID`, `build_nano_banana_pro_payload`, and `build_qwen_image_3_edit_payload`; dispatch both builders in `WaveSpeedClient._validate_model_payload`.
- [ ] Re-run the focused tests; expect pass.

### Task 2: Imágenes workspaces

**Files:**
- Modify: `app/ui/main_window.py`
- Test: `tests/test_ui_frame_preview.py`

- [ ] Add failing UI tests that select local files, build uploaded requests, and assert the two model IDs and model-specific fields.
- [ ] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .\\.venv\\Scripts\\python.exe -m pytest tests/test_ui_frame_preview.py -q`; expect missing widget/handler attributes.
- [ ] Add Nano Banana Pro and Qwen Image 3.0 Edit tabs; use their payload builders before upload, submit with their own IDs, download completed URL outputs, and record history.
- [ ] Re-run the focused UI tests; expect pass.

### Task 3: Regression verification

**Files:**
- Test: `tests/test_wavespeed.py`
- Test: `tests/test_ui_frame_preview.py`

- [ ] Run both focused files and then `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .\\.venv\\Scripts\\python.exe -m pytest -q`.
- [ ] Inspect `git diff --check` and `git status --short`; confirm only source, tests, and design/plan files changed.
