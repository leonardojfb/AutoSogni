# WaveSpeed sequential reference-video queue

## Goal

Add a standalone queue workflow to the WaveSpeed tab. The user selects one shared reference frame and creates multiple generation items, each with its own reference video, prompt, and model configuration. Items execute strictly in order: the next item is submitted only after the previous item reaches a terminal state and its output has been handled.

## User workflow

1. Select one shared image frame.
2. Add one or more reference videos as queue rows.
3. Edit each row's prompt and generation settings.
4. Estimate each row's price before starting, when possible.
5. Start the queue.
6. Observe per-row progress, task ID, output file, and error.
7. Continue automatically after a failure; failed rows expose a retry action.

## UI design

The existing single-generation WaveSpeed controls remain available. A separate queue section is added to the same WaveSpeed tab:

- Shared frame selector with file path and remove/replace action.
- Editable queue table with one row per video. Columns: order, video, prompt, resolution, aspect ratio, duration, audio, prompt expansion, seed, status, task ID, output, error, retry.
- Actions: add video, remove selected rows, start queue, pause after current item, retry failed rows, and clear completed rows.
- Queue status shows the active row and aggregate progress without hiding the single-generation workflow.

The first implementation supports the model's generation settings per row. The queue forces the reliable asynchronous path: webhook, sync mode, and Base64 output are disabled in the queue section. Each row uses a task ID and polling so the runner can recover, wait for a terminal state, and enforce strict ordering.

## Execution model

The queue runner owns one active item at a time. For each row it:

1. Validates the shared frame, video, prompt, and row settings.
2. Uploads or reuses cached media URLs.
3. Estimates and records the row price before submission when requested.
4. Submits the model request.
5. Polls the task until completed or a terminal failure.
6. Downloads a completed output to the configured output directory.
7. Persists the row status and moves to the next row.

The runner must never submit two queue items concurrently. Pause means no new row starts after the active item finishes; it does not cancel an in-flight WaveSpeed task. A local cancellation action may stop polling, but the remote task remains recoverable by task ID.

## Failure and retry behavior

An item failure is persisted with its error and task ID, marked `Fallido`, and does not stop the queue. Retrying a failed row resets only that row to `Pendiente` and executes it after the currently active item, or immediately when the queue is idle. A retry must not create a duplicate submission while an existing task ID is still recoverable; the runner queries that task first when possible.

## Persistence and recovery

The queue is stored locally with sanitized metadata. Local paths, prompts, settings, statuses, task IDs, output paths, timestamps, and errors are persisted; temporary upload URLs and API keys are not. On application restart, non-terminal rows remain recoverable as pending, while completed and failed rows retain their history. The queue can be resumed explicitly by the user.

## Verification

- Unit tests cover row validation, serialization, status transitions, retry reset, and strict sequential ordering.
- Client tests verify the shared frame/video upload reuse and that no API key reaches storage uploads.
- UI tests verify adding/removing rows, per-row editing, status rendering, and retry action wiring.
- An integration-style test uses a fake WaveSpeed client to prove that item 2 is not submitted before item 1 reaches a terminal state, and that a failed item does not prevent item 3 from running.
- Existing AutoSogni tests must remain green.
