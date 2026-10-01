# BytePlus Seedance 2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a direct BytePlus ModelArk Seedance 2.0 tab with documented request modes, task operations, history, and persistent campaigns.

**Architecture:** A new `app.byteplus` package owns request validation, HTTP lifecycle, and persistence. `MainWindow` owns the PySide6 composition and worker events; a provider-specific queue widget and runner preserve state independently from WaveSpeed.

**Tech Stack:** Python 3, PySide6, httpx, dataclasses, JSON persistence, pytest.

## Global Constraints

- Use only `dreamina-seedance-2-0-260128` and `https://ark.ap-southeast.bytepluses.com/api/v3`.
- Keep BytePlus key, campaigns, history, and output files separate from WaveSpeed runtime state.
- Never submit a local file path as an API URL and never persist an API key or signed remote URL.
- Run tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; never send paid requests in tests.

---

### Task 1: ModelArk validator and client

**Files:** Create `app/byteplus/__init__.py`, `app/byteplus/validation.py`, `app/byteplus/schemas.py`, `app/byteplus/client.py`; test `tests/test_byteplus.py`.

**Interfaces:** `build_task_payload(snapshot: dict) -> dict`; `BytePlusTask`; `BytePlusClient.submit`, `get_task`, `poll_task`, `cancel_task`, `delete_task`, `download_output`.

- [ ] Write a failing MockTransport test proving the client posts the fixed model, bearer authorization, ordered `content`, and documented controls to `/contents/generations/tasks`.
- [ ] Run `python -m pytest tests/test_byteplus.py -q`; confirm failure because `app.byteplus` does not exist.
- [ ] Implement the minimal validator and client, including six mutually exclusive modes, local URL validation, ModelArk API-error decoding, terminal-state polling, and downloads.
- [ ] Run `python -m pytest tests/test_byteplus.py -q`; confirm the client and payload tests pass.
- [ ] Commit with `git add app/byteplus tests/test_byteplus.py && git commit -m "feat: add BytePlus ModelArk client"`.

### Task 2: Isolated queue, campaigns, history, and runner

**Files:** Create `app/byteplus/history.py`, `app/byteplus/queue.py`, `app/byteplus/queue_runner.py`; modify `tests/test_byteplus.py`.

**Interfaces:** `BytePlusQueueItem`, `BytePlusQueue`, `BytePlusCampaign`, `BytePlusCampaignStore`, `BytePlusHistoryStore`, `BytePlusQueueRunner.run`.

- [ ] Write failing tests for JSON round trips, redacted URLs, restart recovery, querying an existing task before submitting, retry reset, and sequential continuation after failure.
- [ ] Run `python -m pytest tests/test_byteplus.py -q`; confirm failure caused by missing queue types.
- [ ] Implement isolated `data/byteplus_*.json` stores and a runner that saves only output after a terminal `succeeded` response.
- [ ] Run `python -m pytest tests/test_byteplus.py -q`; confirm all persistence and runner tests pass.
- [ ] Commit with `git add app/byteplus tests/test_byteplus.py && git commit -m "feat: persist BytePlus campaigns"`.

### Task 3: PySide6 tab and workers

**Files:** Create `app/ui/byteplus_queue.py`; modify `app/ui/main_window.py`, `tests/test_ui_frame_preview.py`.

**Interfaces:** `BytePlusQueueWidget` emits campaign/queue/retry/start/pause signals; `MainWindow._byteplus_tab()` composes the fixed provider and dispatches its worker results through Qt events.

- [ ] Write failing UI tests proving the BytePlus tab, key field, fixed model, all mode controls, direct task actions, and independent queue widget exist.
- [ ] Run `python -m pytest tests/test_ui_frame_preview.py -q`; confirm failure because there is no BytePlus tab.
- [ ] Implement the tab, event handlers, local key persistence, reference URL editor, queue lifecycle, history refresh, and visible Spanish errors.
- [ ] Run `python -m pytest tests/test_ui_frame_preview.py -q`; confirm the new UI tests and existing frame-preview tests pass.
- [ ] Commit with `git add app/ui/byteplus_queue.py app/ui/main_window.py tests/test_ui_frame_preview.py && git commit -m "feat: add BytePlus Seedance tab"`.

### Task 4: Regression verification

**Files:** Modify this plan to record final commands and results if needed.

- [ ] Run `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q`; confirm zero failures.
- [ ] Run `python -m compileall app`; confirm exit code 0.
- [ ] Run `git diff --check origin/master...HEAD; git status --short --branch`; confirm no whitespace errors and only intended files.
