# Generic Image-Edit Queue Core for Qwen Image 3.0 Edit

## Goal

Add a persistent Qwen Image 3.0 Edit queue under **Imágenes**. The queue uses a generic image-edit queue core so another model can be registered later, but Qwen is the only configuration and UI wired in this change.

## Scope

- Qwen items capture the prompt, local image paths, aspect ratio, resolution, prompt-expansion option, seed, and output directory at enqueue time.
- Qwen campaigns persist independently in `data/qwen_image_campaigns.json`.
- The Qwen tab offers enqueue, campaign selection, sequential start, pause, cost estimation, retry for failed items, and removal of completed items.
- A restarted application recovers a saved remote task by its `task_id` before considering another submission.

Flux Edit keeps its current classes, persistence file, UI, and behavior. Nano Banana Pro receives no queue, state file, or UI change. Existing WaveSpeed history remains the terminal-result record.

## Architecture

`app/wavespeed/image_edit_queue.py` provides model-neutral campaign, queue, item, JSON store, and runner types. `ImageEditQueueConfig` is immutable and carries the model key, fixed model ID, payload builder, campaign filename, and image limit. The sole initial configuration is Qwen and uses `alibaba/qwen-image-3.0/edit` with `build_qwen_image_3_edit_payload`.

`app/ui/image_edit_queue.py` renders the generic campaign and item controls. `MainWindow` owns the Qwen configuration, Qwen campaign store, running state, queue callbacks, and the bridge to the existing upload cache, output saver, and history store.

## Execution and Recovery

Before uploads or submission, the runner validates the complete saved Qwen snapshot and every referenced local file. It uploads via the existing WaveSpeed upload cache, estimates a price when available, submits with Qwen's fixed model ID, and persists the returned `task_id` before polling.

If an item already has a `task_id`, the runner retrieves that task instead of uploading or submitting again. Completed tasks save the first output through the existing image saver; failed terminal tasks retain a readable error. Per-item failures do not prevent later queued items from running. Pause is checked between sequential items, so no subsequent item is submitted after a pause request.

## Persistence and Isolation

The Qwen store holds its own campaigns and active campaign ID. Writes use a temporary JSON file followed by atomic replacement while holding a lock. A saved running or paused item is restored as pending so its known remote task can be queried on the next run. No Qwen action reads or changes Flux data, Nano data, keys, outputs, or history entries outside its own terminal results.

## Verification

Tests use fake WaveSpeed clients and no paid requests. They prove JSON round-trips, Qwen-only schema routing, a saved task's recovery without a second submit, sequential pause behavior, per-item failure handling, price estimation, UI queue controls, and Qwen UI wiring. Focused queue/UI tests and the full suite must pass before completion.
