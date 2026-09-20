# H3 Prompt Processing Checkbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let each new campaign choose whether Sogni may automatically process MiniMax H3 prompts.

**Architecture:** The campaign form stores a boolean in `settings_json`. `JobRunner` passes that setting into the Sogni payload builder, which conditionally adds `skipPromptProcessing` only for H3 `animate_photo` jobs. The default remains enabled to preserve the current behavior.

**Tech Stack:** Python, PySide6, SQLite JSON settings, pytest.

## Global Constraints

- Preserve prompt text exactly; the checkbox must only control the Sogni API flag.
- Default the checkbox to checked for existing behavior and safer H3 dialogue preservation.
- Do not send `expandPrompt` for MiniMax H3.
- Do not affect non-H3 video models.

### Task 1: Persist the campaign option

**Files:**
- Modify: `app/core/model_settings.py`
- Modify: `app/ui/main_window.py`
- Test: `tests/test_model_settings.py`

- [ ] Add a `skip_prompt_processing` argument to `build_campaign_settings`, defaulting to `True`, and serialize it as `skipPromptProcessing`.
- [ ] Add a checked `QCheckBox` to the campaign form and pass its value when creating the campaign.
- [ ] Test that the setting persists for both checked and unchecked values.

### Task 2: Thread the option into job payloads

**Files:**
- Modify: `app/core/job_runner.py`
- Modify: `app/sogni/client.py`
- Test: `tests/test_core_behaviors.py`

- [ ] Read `skipPromptProcessing` from campaign settings and pass it to the payload builder.
- [ ] Add an optional `skip_prompt_processing` argument to `build_image_to_video_payload`.
- [ ] Add `skipPromptProcessing: true` only for H3 when enabled; omit it when disabled.
- [ ] Keep non-H3 payloads unchanged.
- [ ] Test both H3 checkbox states and non-H3 behavior.

### Task 3: Verify the complete suite

**Files:**
- Test: `tests/test_model_settings.py`
- Test: `tests/test_core_behaviors.py`
- Test: `tests/test_runner_behaviors.py`

- [ ] Run focused tests for settings and payload construction.
- [ ] Run `py -m pytest -q` and report the exact result.
- [ ] Confirm the packaged executable is not rebuilt automatically; source changes require a later PyInstaller build.
