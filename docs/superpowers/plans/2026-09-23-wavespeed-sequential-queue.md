# WaveSpeed Sequential Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, strictly sequential WaveSpeed queue that reuses one shared frame across multiple video/prompt/configuration rows, continues after failures, and exposes retry controls.

**Architecture:** Keep the existing single-generation WaveSpeed workflow unchanged. Add a focused queue model/store in `app/wavespeed/queue.py`, a client-independent sequential runner in `app/wavespeed/queue_runner.py`, and a dedicated queue panel/widget wired into the existing WaveSpeed tab. The runner receives a small client protocol and callbacks so ordering, failure continuation, and retry behavior can be tested without network calls or Qt.

**Tech Stack:** Python 3, dataclasses, JSON persistence, PySide6, existing `WaveSpeedClient`, pytest, httpx mock transport.

## Global Constraints

- The user selects one shared reference frame and creates multiple generation items, each with its own reference video, prompt, and model configuration.
- Items execute strictly in order: the next item is submitted only after the previous item reaches a terminal state and its output has been handled.
- An item failure is persisted with its error and task ID, marked `Fallido`, and does not stop the queue.
- Retrying a failed row resets only that row to `Pendiente` and executes it after the currently active item, or immediately when the queue is idle.
- The queue forces the reliable asynchronous path: webhook, sync mode, and Base64 output are disabled in the queue section.
- Temporary upload URLs and API keys are never persisted.
- Existing AutoSogni tests must remain green.

---

### Task 1: Define queue rows, statuses, and local persistence

**Files:**
- Create: `app/wavespeed/queue.py`
- Create: `tests/test_wavespeed_queue.py`

**Interfaces:**
- `QueueStatus`: string constants `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `PAUSED`.
- `WaveSpeedQueueItem`: dataclass with `item_id`, `video_path`, `prompt`, `resolution`, `aspect_ratio`, `duration`, `enable_prompt_expansion`, `enable_audio`, `seed`, `status`, `task_id`, `price`, `output_file`, `error`, and `created_at`.
- `WaveSpeedQueue`: dataclass with `frame_path`, `output_dir`, `items`, `paused`, and `updated_at`.
- `WaveSpeedQueueStore(path: Path | None = None)` with `load() -> WaveSpeedQueue`, `save(queue: WaveSpeedQueue) -> None`, and `clear() -> None`.
- `WaveSpeedQueueItem.retry() -> None` resets only status, task ID, price, output file, and error.

- [ ] **Step 1: Write failing tests for row serialization, retry reset, and secret-safe persistence.**

```python
def test_queue_store_round_trips_items_and_redacts_remote_values(tmp_path):
    store = WaveSpeedQueueStore(tmp_path / "queue.json")
    queue = WaveSpeedQueue(
        frame_path="C:/inputs/frame.png",
        output_dir="C:/outputs",
        items=[WaveSpeedQueueItem(item_id="1", video_path="C:/inputs/ref.mp4", prompt="Walk")],
    )
    queue.items[0].task_id = "pred-1"
    queue.items[0].output_file = "C:/outputs/pred-1.mp4"

    store.save(queue)
    restored = store.load()

    assert restored.items[0].video_path == "C:/inputs/ref.mp4"
    assert restored.items[0].task_id == "pred-1"
    assert "https://" not in (tmp_path / "queue.json").read_text(encoding="utf-8")


def test_retry_resets_only_remote_result_fields():
    item = WaveSpeedQueueItem(item_id="1", video_path="ref.mp4", prompt="Walk")
    item.status = "failed"
    item.task_id = "pred-1"
    item.price = 0.5
    item.output_file = "out.mp4"
    item.error = "remote failure"

    item.retry()

    assert item.status == QueueStatus.PENDING
    assert item.task_id == ""
    assert item.price is None
    assert item.output_file == ""
    assert item.error == ""
    assert item.video_path == "ref.mp4"
    assert item.prompt == "Walk"
```

- [ ] **Step 2: Run the focused tests and verify they fail because the queue types do not exist.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_wavespeed_queue.py`

Expected: FAIL with an import error for `app.wavespeed.queue`.

- [ ] **Step 3: Implement the dataclasses and store.**

Use `dataclasses.asdict` for JSON serialization, generate a UUID `item_id` when omitted, write through a `.tmp` file with `os.replace`, and return an empty queue when the file is absent or invalid. Serialize only local paths, model settings, statuses, task IDs, prices, output paths, errors, and timestamps; never add upload URLs or API keys to the queue schema.

- [ ] **Step 4: Run the focused tests and verify they pass.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_wavespeed_queue.py`

Expected: PASS.

- [ ] **Step 5: Commit the queue model and persistence.**

```powershell
git add app/wavespeed/queue.py tests/test_wavespeed_queue.py
git commit -m "feat: add WaveSpeed queue persistence"
```

### Task 2: Implement the strict sequential runner

**Files:**
- Create: `app/wavespeed/queue_runner.py`
- Modify: `tests/test_wavespeed_queue.py`

**Interfaces:**
- `WaveSpeedQueueRunner(client, upload_file, save_output, on_update=None, sleep=time.sleep)`.
- `run(queue: WaveSpeedQueue, cancel_event=None) -> WaveSpeedQueue`.
- The client dependency must provide `estimate_price(payload)`, `submit(payload)`, and `poll_result(task_id, on_update=..., cancel_event=...)`.
- `upload_file(path)` returns a mapping containing `download_url`.
- `save_output(output, output_dir, task_id) -> str` is injected so the runner does not depend on Qt widgets.
- `on_update(item: WaveSpeedQueueItem) -> None` is called after every persisted state change.

- [ ] **Step 1: Write failing tests for ordering and failure continuation.**

```python
def test_runner_submits_one_item_at_a_time_and_continues_after_failure(tmp_path):
    events = []
    client = FakeWaveSpeedClient(
        events,
        results={"pred-1": WaveSpeedPrediction("pred-1", "failed", error="bad video"),
                 "pred-2": WaveSpeedPrediction("pred-2", "completed", outputs=["url-2"])},
    )
    queue = WaveSpeedQueue(
        frame_path="frame.png",
        output_dir=str(tmp_path),
        items=[
            WaveSpeedQueueItem(item_id="1", video_path="one.mp4", prompt="One"),
            WaveSpeedQueueItem(item_id="2", video_path="two.mp4", prompt="Two"),
        ],
    )

    result = WaveSpeedQueueRunner(
        client,
        upload_file=lambda path: {"download_url": f"https://cdn/{path}"},
        save_output=lambda output, output_dir, task_id: str(tmp_path / f"{task_id}.mp4"),
        on_update=lambda item: events.append((item.item_id, item.status)),
        sleep=lambda _seconds: None,
    ).run(queue)

    assert [event for event in client.events if event[0] == "submit"] == [
        ("submit", "One"),
        ("submit", "Two"),
    ]
    assert result.items[0].status == QueueStatus.FAILED
    assert result.items[1].status == QueueStatus.COMPLETED
```

- [ ] **Step 2: Run the focused test and verify it fails because the runner does not exist.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_wavespeed_queue.py::test_runner_submits_one_item_at_a_time_and_continues_after_failure`

Expected: FAIL with an import or attribute error for `WaveSpeedQueueRunner`.

- [ ] **Step 3: Implement the runner with explicit state transitions.**

For each pending item, upload the shared frame once and that row's video, build a payload with `build_reference_video_payload(reference_images=[frame_url], reference_videos=[video_url], enable_sync_mode=False, enable_base64_output=False)`, call `client.estimate_price` when available, submit, store the task ID, poll until a terminal result, save completed output, and persist the item. Catch exceptions per item, set `FAILED` plus a readable error, call `on_update`, and continue. Check `queue.paused` and `cancel_event` only between items; never submit a new item after pause or local cancellation.

- [ ] **Step 4: Add tests for pause, cancellation, retry ordering, and shared-frame upload reuse.**

The tests must assert that a paused queue leaves later items `PENDING`, cancellation does not start another item, retry changes only the selected failed item to `PENDING`, and the frame upload occurs once for three rows while each video uploads once.

- [ ] **Step 5: Run all queue tests and verify they pass.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_wavespeed_queue.py`

Expected: PASS with ordering, continuation, pause, cancellation, retry, and upload-cache coverage.

- [ ] **Step 6: Commit the runner.**

```powershell
git add app/wavespeed/queue_runner.py tests/test_wavespeed_queue.py
git commit -m "feat: run WaveSpeed queue sequentially"
```

### Task 3: Add the queue panel and row editing UI

**Files:**
- Create: `app/ui/wavespeed_queue.py`
- Modify: `app/ui/main_window.py`
- Modify: `tests/test_ui_frame_preview.py`

**Interfaces:**
- `WaveSpeedQueueWidget(parent=None)` exposes `queue_changed`, `start_requested`, `pause_requested`, `retry_requested`, and `clear_completed_requested` Qt signals.
- `load_queue(queue) -> None`, `queue() -> WaveSpeedQueue`, `set_running(running: bool) -> None`, `set_item_update(item) -> None`, and `set_error(message: str) -> None` are the UI/controller boundary.
- `MainWindow` owns `WaveSpeedQueueStore`, the shared frame upload cache, and the runner thread; the widget never calls WaveSpeed directly.

- [ ] **Step 1: Write failing UI tests for queue presence, row creation, per-row values, and retry wiring.**

```python
def test_wavespeed_queue_panel_adds_rows_and_exposes_retry(tmp_path):
    window = make_main_window(tmp_path)
    queue = WaveSpeedQueue(frame_path="frame.png", items=[])
    window.wavespeed_queue_widget.load_queue(queue)
    window.wavespeed_queue_widget.add_item_for_test("video.mp4")

    item = window.wavespeed_queue_widget.queue().items[0]
    assert item.video_path == "video.mp4"
    assert item.status == QueueStatus.PENDING
    assert window.wavespeed_queue_widget.has_retry_control(item.item_id)
```

- [ ] **Step 2: Run the focused UI tests and verify they fail because the widget is not present.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_ui_frame_preview.py::test_wavespeed_queue_panel_adds_rows_and_exposes_retry`

Expected: FAIL because `MainWindow` has no `wavespeed_queue_widget`.

- [ ] **Step 3: Implement the queue widget.**

Use a `QTableWidget` with columns `#`, `Video`, `Prompt`, `Resolution`, `Aspect`, `Duration`, `Audio`, `Expand`, `Seed`, `Price`, `Status`, `Task`, `Output`, `Error`, and `Retry`. Keep the selected shared frame in a `QLineEdit` plus `Seleccionar frame` and `Quitar frame` buttons. Use per-row cell widgets for prompt, combo boxes, duration, checkboxes, and a retry button; keep status/task/output/error read-only. Add `Agregar video`, `Quitar seleccionado`, `Iniciar cola`, `Pausar después del actual`, `Reintentar fallidos`, and `Limpiar completados`.

- [ ] **Step 4: Wire the widget into `_wavespeed_tab`.**

Instantiate the widget below the existing single-generation controls, connect its signals to controller methods, load `WaveSpeedQueueStore`, and add the WaveSpeed tab assertion to the existing UI test. Queue controls must disable webhook, sync, and Base64 options for queue execution while leaving the existing single-generation controls unchanged.

- [ ] **Step 5: Run the UI tests and verify they pass.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_ui_frame_preview.py`

Expected: PASS, including the new queue UI tests and the existing frame preview test.

- [ ] **Step 6: Commit the queue panel.**

```powershell
git add app/ui/wavespeed_queue.py app/ui/main_window.py tests/test_ui_frame_preview.py
git commit -m "feat: add WaveSpeed queue editor"
```

### Task 4: Connect queue execution, persistence, and retry actions

**Files:**
- Modify: `app/ui/main_window.py`
- Modify: `app/ui/wavespeed_queue.py`
- Modify: `app/wavespeed/queue.py`
- Modify: `tests/test_ui_frame_preview.py`
- Modify: `tests/test_wavespeed_queue.py`

**Interfaces:**
- `MainWindow._start_wavespeed_queue()`, `_pause_wavespeed_queue()`, `_retry_wavespeed_item(item_id)`, `_retry_wavespeed_failed()`, and `_clear_wavespeed_completed()` are the controller slots.
- Queue worker events use the existing `_WaveSpeedEvent` path with kinds `queue_status`, `queue_item`, and `queue_finished`.

- [ ] **Step 1: Write failing tests for controller state updates and restart recovery.**

Tests must start the queue with a fake runner, assert that the button state changes and row updates arrive through the event handler, then create a new `MainWindow` with the same queue store and assert pending/failed rows are restored with their task IDs and errors.

- [ ] **Step 2: Run the focused tests and verify they fail because the controller slots do not exist.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_wavespeed_queue.py tests/test_ui_frame_preview.py`

Expected: FAIL with missing controller methods or queue widget wiring.

- [ ] **Step 3: Implement the controller worker.**

Run `WaveSpeedQueueRunner` on a daemon thread, pass the current queue, shared frame, output directory, cancel event, upload cache, and output-saving callback, then post row/state updates through `_post_wavespeed_event`. Persist after every transition. Disable only queue-mutating controls while active. Pause sets a flag consumed between rows; retry while active marks the row pending for the next available slot and never launches a second runner.

- [ ] **Step 4: Implement restart and retry behavior.**

Load the queue JSON at tab construction. On retry, call `item.retry()`, persist immediately, update the row, and start the runner only if it is idle. For a failed item with a task ID, query the existing task before submitting a duplicate; submit a new task only when the existing task is terminal and retry was explicitly requested.

- [ ] **Step 5: Run focused tests and verify they pass.**

Run: `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q tests/test_wavespeed_queue.py tests/test_ui_frame_preview.py`

Expected: PASS with queue execution, persistence, row updates, restart recovery, and retry coverage.

- [ ] **Step 6: Commit the integrated queue flow.**

```powershell
git add app/wavespeed/queue.py app/ui/wavespeed_queue.py app/ui/main_window.py tests/test_wavespeed_queue.py tests/test_ui_frame_preview.py
git commit -m "feat: connect WaveSpeed queue execution"
```

### Task 5: Full verification and handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-23-wavespeed-sequential-queue-design.md`

- [ ] **Step 1: Document the queue workflow and recovery behavior.**

Add a short README section explaining the shared frame, one video per row, per-row prompt/configuration, sequential execution, failure continuation, retry, local queue file, and the fact that webhook/sync/Base64 are disabled for queue runs.

- [ ] **Step 2: Run the complete verification suite.**

Run:

```powershell
& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q
& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m compileall app tests -q
git diff --check
```

Expected: all tests pass, compileall exits successfully, and `git diff --check` emits no whitespace errors.

- [ ] **Step 3: Verify no runtime data or secrets are staged.**

Run: `git status --short` and confirm only source, test, documentation, and plan/spec files are tracked; `data/` remains ignored or untracked.

- [ ] **Step 4: Commit documentation and final verification.**

```powershell
git add README.md docs/superpowers/specs/2026-09-23-wavespeed-sequential-queue-design.md
git commit -m "docs: document WaveSpeed sequential queue"
```
