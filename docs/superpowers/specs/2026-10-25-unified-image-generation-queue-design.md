# Unified image-generation queue for Flux, Qwen, and Nano Banana

## Goal

Replace the three divergent image-generation paths with one durable, model-adapted queue. It must expand a selection of independent source images and a requested number of generations into one persisted task per expected result, execute pending work with bounded parallelism, and retain independent result and error state.

The immediate acceptance example is six selected source images and two repetitions: the enqueue operation creates and saves exactly twelve task records before any provider submission begins. A failure of one record never prevents remaining records from being attempted.

## Scope and source-image semantics

This design applies to Flux Edit, Qwen Image 3.0 Edit, and Nano Banana Pro Edit under the Imágenes workspace.

In batch mode, every selected image is an independent source image, not a list of references for one shared generation. The queue takes the cartesian product of selected source images and repetitions. A source image appears in each of its repetition records with a snapshot of the prompt and model settings selected at enqueue time.

The design does not change WaveSpeed model validation or add a new remote API. Model-specific payload schemas remain in the existing validation builders.

## Alternatives considered

1. Duplicate FluxQueue for Qwen and Nano Banana. This is quick but repeats runner, persistence, retry, and UI logic; a later bug fix would need three changes.
2. Extend the existing Qwen-only ImageEditQueue, leaving Flux on FluxQueue. This reduces new code but keeps incompatible queue behavior and persistence paths.
3. Adopt a common queue core with model adapters. This is selected because queue mechanics are identical while request fields differ by model.

## Architecture

### Common queue domain

Introduce a single image-generation queue domain used by all three models. A campaign contains queue-wide execution settings and a list of durable task items. Every task item stores:

- a stable local item ID;
- model key and WaveSpeed model ID;
- one source image path;
- prompt and a complete immutable snapshot of compatible model settings;
- state (`pending`, `running`, `completed`, `failed`, or `paused`), provider task ID, estimated price, output path, error text, timestamps, and retry count.

The queue persists its campaign file using atomic replacement and a lock. On startup, records that were locally `running` or `paused` become `pending`. Records with a provider task ID resume by querying that ID rather than submitting a billable duplicate.

Existing Flux campaign JSON remains readable. Flux records are converted into the common in-memory representation without deleting the original file. New unified campaigns are written separately, so no existing campaign, history, output, credential, or runtime data is overwritten.

### Model adapters

Each adapter contains only model-specific facts:

- model key and WaveSpeed model ID;
- the existing payload builder and compatible setting schema;
- output extension;
- UI labels/options, including aspect-ratio rules;
- a configurable safe parallelism ceiling.

Flux keeps its size, seed, sync, and Base64 controls. Nano Banana keeps aspect ratio, resolution, output format, sync, and Base64 controls. Qwen keeps aspect ratio, resolution, prompt expansion, and seed controls. The UI never sends another model's option to an adapter.

The adapter validates the local snapshot before upload and validates the uploaded-URL payload before pricing or submission. A model-specific validation failure marks only that task as failed with its error.

### Batch expansion and execution

The enqueue command validates the selected sources, repetition count, and settings before mutating the queue. It then creates all source-image × repetition task records, appends them as one operation, persists the campaign, and reports the exact queued count.

The runner obtains only `pending` or resumable `paused` tasks. It uses a bounded worker pool whose effective size is the lesser of the campaign-selected concurrency, adapter ceiling, and pending-task count. It maintains a lock-protected upload cache keyed by canonical local path so repeated tasks reuse one upload. Each worker owns one task; submitting, polling, downloading, and failure handling are isolated per task.

Pause prevents dispatch of further pending records while allowing already-running workers to settle. Completion and failure update the campaign immediately. Individual retry clears only the selected record's terminal fields and provider ID; retry-all affects only failed records.

### UI and thread boundary

All models expose a consistent campaign selector, enqueue control, repetition spinner, concurrency selector, price estimate, task table, start/pause, retry, retry-failed, and clear-completed actions. The table displays the source image, repetition number, model settings, price, state, provider task ID, saved file, and error.

Worker callbacks never modify Qt widgets directly. They publish queue-state updates through the application's GUI-thread event/signal path. The GUI handler persists the update and refreshes the affected row. This makes parallel state rendering safe.

## Error behavior

- A failed upload, validation, price estimate, submit, poll, empty successful output, or download marks only the relevant task failed and records a useful error.
- Estimation errors do not prevent later submission; an unknown price is shown as unavailable.
- A task with a persisted provider ID is queried first after restart and is never resubmitted unless the user explicitly retries it after a terminal failure.
- At all times, task count is observable by state; no failure path silently removes an item.

## Tests and acceptance proof

Tests precede implementation and cover:

1. six independent source images with two repetitions persist as twelve unique task records before runner invocation;
2. sequential and bounded-parallel execution submit every record exactly once;
3. one failed task does not block successful siblings, and individual retry resubmits only that record;
4. one repeated source upload is cached despite parallel workers;
5. restart recovery queries a persisted provider ID without re-uploading or resubmitting it;
6. each Flux, Qwen, and Nano adapter produces only its compatible payload;
7. legacy Flux campaign data can still be loaded without mutation.

Verification includes focused queue and UI tests, the full test suite, static compilation, `git diff --check`, and a local GUI smoke test that queues six fixture images with two repetitions and visibly shows twelve state rows. Live API submission is deliberately excluded from automated tests to avoid billable work; if requested later, it will be reported separately with provider task IDs and result counts.

## Non-goals

- Altering remote WaveSpeed quotas or claiming an account-specific provider limit without provider evidence.
- Deleting or migrating runtime campaign/history files in place.
- Adding multi-reference grouping semantics to this batch interaction; selected batch images are intentionally independent source jobs.
