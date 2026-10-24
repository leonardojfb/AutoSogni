# Independent Queues for Nano Banana Pro and Qwen Image 3.0 Edit

## Goal

Give each new image-edit model its own persistent queue, with the recovery and control behavior already available for Flux Edit. A user can collect several edits, estimate their cost, execute them sequentially or in parallel, pause between items, and resume safely after restarting the application.

## Scope

The feature applies only to Nano Banana Pro and Qwen Image 3.0 Edit. Flux Edit, Face Swap, WAN, Sogni, BytePlus, existing history, and their persisted queue formats remain unchanged.

## Queue boundaries and persistence

Each model owns a separate campaign store:

- `data/nano_banana_campaigns.json` stores Nano Banana Pro campaigns.
- `data/qwen_image_campaigns.json` stores Qwen Image 3.0 Edit campaigns.

Each store uses atomic replacement and a lock, matching Flux persistence. The stores are local runtime state, ignored by Git, and are never read by the other model. Creating, updating, pausing, retrying, or deleting an image-edit campaign only changes that model's JSON file.

## Queue item snapshot

Adding an item to a queue captures the selected local image paths, prompt, output directory, aspect ratio, resolution, seed, and the model-specific settings visible at that moment. The queue does not read subsequent edits from the form when it runs.

Nano Banana Pro snapshots output format, synchronous mode, and base64 mode. Qwen Image 3.0 Edit snapshots prompt expansion and its optional seed. The model ID is fixed by the owning queue, so a Qwen item can never be submitted as Nano Banana Pro or vice versa.

## User interface

Each model tab keeps its current single-image generation controls and gains an `Agregar a la cola` action. Below those controls it renders the same campaign workflow as Flux with model-neutral labels:

- campaign selector, name, execution mode, create, save, and delete controls;
- item table showing images, prompt, model settings, estimated price, state, remote task ID, output file, error, and a per-item retry action;
- start or resume, pause after the current item, retry failed, remove selected, clear completed, and estimate costs actions.

The two queue widgets are independent. Starting or pausing one model queue does not disable or mutate the other model queue.

## Execution and recovery

The runner validates the complete local snapshot before uploading anything. It uploads local image files through the existing WaveSpeed upload cache, builds the model-specific payload, estimates cost when possible, submits with the queue's fixed model ID, and persists each status transition through the UI callback.

After a successful submission, the runner persists the returned task ID before polling. On restart or retry it asks WaveSpeed for that task ID first; it submits again only when no remote task exists or when the user explicitly retries a terminal failed item. Completed output is downloaded with the existing image-edit saver and recorded in WaveSpeed history.

Sequential mode processes one pending item at a time. Parallel mode uses the Flux maximum of four workers. A pause request prevents a new item from starting and permits the active item to reach a persisted terminal state. Errors on one item mark only that item failed and allow the rest of the queue to continue.

## Implementation shape

A generic image-edit campaign, store, runner, and queue widget will take a small immutable model configuration containing the model ID, payload builder, label, persistence filename, snapshot fields, and table columns. Thin Nano and Qwen configuration entries define their differing payload fields. This shares recovery, persistence, upload, polling, cost, and UI behavior without mixing their API schemas.

Flux continues to use its existing queue classes. Reuse is limited to stable behavior and presentation patterns; no Flux persistence file or public class contract is migrated.

## Validation

Tests will cover separate store files, item round trips for each model, model-specific payload construction, fixed model routing, remote task recovery without duplicate submission, pause and retry behavior, and the two UI enqueue paths. The complete test suite must pass. No paid WaveSpeed request is made by automated tests.
