# Unified Image-Generation Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Flux Edit, Qwen Image 3.0 Edit, and Nano Banana Pro Edit use one durable batch queue that expands one task per source-image/repetition pair and executes it with bounded parallelism.

**Architecture:** Extend the existing generic `ImageEditQueue` into the sole runtime queue core. Model adapters provide payload construction, output extension, and a parallelism ceiling; the core owns batch expansion, persistence, upload caching, retries, and bounded workers. Replace Qwen-only and Flux-specific controller paths in `MainWindow` with model-keyed generic methods, while retaining legacy Flux JSON as read-only input and writing new campaigns separately.

**Tech Stack:** Python 3.14, PySide6, `concurrent.futures`, `threading`, `pytest`, WaveSpeed HTTP client.

## Global Constraints

- Preserve `data/`, API keys, existing campaign JSON, histories, outputs, builds, distributions, and logs; do not edit legacy `data/flux_campaigns.json` in place.
- Treat every selected image as an independent batch source. `N` images and `R` repetitions must create exactly `N * R` persisted task records before submission.
- Every task is independently persisted, submitted, completed, failed, and retryable. One failure must not prevent sibling tasks from running.
- Use the existing model-specific payload builders; never send controls from one model to another.
- Validate before upload and before billable submission. A stored provider task ID must be queried, not re-submitted, after restart.
- Use `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'` for all pytest commands on this Windows checkout.
- Do not execute billable live WaveSpeed submissions in automated verification.

---

## File structure

| File | Responsibility |
| --- | --- |
| `app/wavespeed/image_edit_queue.py` | Generic queue/item/campaign persistence, batch expansion, legacy Flux conversion, upload cache, bounded concurrent runner. |
| `app/wavespeed/flux_queue.py` | Legacy Flux dataclasses/store only; its runner ceases to be called by the UI. |
| `app/ui/image_edit_queue.py` | Shared queue widget: task state table, repetition/concurrency controls, campaign actions, retries. |
| `app/ui/main_window.py` | Model adapters, one generic image-edit queue controller, GUI-thread event handling, model tabs. |
| `tests/test_image_edit_queue.py` | Core batch, persistence, concurrency, failure isolation, upload cache, adapter and legacy-import tests. |
| `tests/test_ui_frame_preview.py` | PySide UI tests for all model controls, batch expansion, and queue state rendering. |
| `tests/test_flux_queue.py` | Keep legacy data-store coverage while removing assertions that require the retired Flux runner path. |

### Task 1: Make the generic queue model durable, batch-aware, and bounded-parallel

**Files:**
- Modify: `app/wavespeed/image_edit_queue.py`
- Modify: `tests/test_image_edit_queue.py`

**Interfaces:**
- Produces `ImageEditQueueConfig(key, model_id, payload_builder, campaign_filename, display_name, output_suffix, max_parallelism)`.
- Produces `ImageEditQueueItem(image_paths, prompt, settings, model_key, model_id, repetition, ...)`.
- Produces `expand_image_edit_batch(config, image_paths, repetitions, prompt, settings) -> list[ImageEditQueueItem]`.
- `ImageEditQueue.concurrency: int` stores requested workers; `ImageEditQueueRunner.run(queue)` honors `min(queue.concurrency, config.max_parallelism, pending_count)`.

- [ ] **Step 1: Write the failing batch-expansion and persistence tests**

  Add these tests to `tests/test_image_edit_queue.py` before changing production code:

  ```python
  def test_expand_batch_creates_one_persistable_item_per_image_and_repetition(tmp_path: Path):
      sources = []
      for index in range(6):
          source = tmp_path / f"source-{index}.png"
          source.write_bytes(b"image")
          sources.append(str(source))

      items = expand_image_edit_batch(
          QWEN_CONFIG, sources, repetitions=2, prompt="replace background",
          settings={"resolution": "1k", "aspect_ratio": "", "enable_prompt_expansion": True, "seed": -1},
      )

      assert len(items) == 12
      assert [item.repetition for item in items].count(1) == 6
      assert [item.repetition for item in items].count(2) == 6
      assert all(len(item.image_paths) == 1 for item in items)
      assert {item.model_id for item in items} == {QWEN_IMAGE_3_EDIT_MODEL_ID}
      assert len({item.item_id for item in items}) == 12

  def test_campaign_round_trip_preserves_requested_concurrency_and_task_model(tmp_path: Path):
      store = ImageEditCampaignStore(tmp_path / "campaigns.json")
      queue = ImageEditQueue(concurrency=3, items=[ImageEditQueueItem(
          image_paths=["C:/input.png"], prompt="edit", model_key="qwen_image",
          model_id=QWEN_IMAGE_3_EDIT_MODEL_ID, repetition=2,
          settings={"resolution": "2k", "aspect_ratio": "", "enable_prompt_expansion": True, "seed": -1},
      )])
      campaign = ImageEditCampaign(name="Doce resultados", queue=queue)

      store.save(campaign, active=True)
      loaded, active_id = store.load()

      assert active_id == campaign.campaign_id
      assert loaded[0].queue.concurrency == 3
      assert loaded[0].queue.items[0].repetition == 2
      assert loaded[0].queue.items[0].model_id == QWEN_IMAGE_3_EDIT_MODEL_ID
  ```

- [ ] **Step 2: Run the new tests and confirm they fail for missing API/fields**

  Run:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; .\.venv\Scripts\python.exe -m pytest tests/test_image_edit_queue.py -q
  ```

  Expected: FAIL because `expand_image_edit_batch`, `repetition`, `model_key`, `model_id`, or `concurrency` do not yet exist.

- [ ] **Step 3: Implement batch data and validation before any mutation**

  In `app/wavespeed/image_edit_queue.py`:

  ```python
  @dataclass(frozen=True)
  class ImageEditQueueConfig:
      key: str
      model_id: str
      payload_builder: Callable[..., dict[str, Any]]
      campaign_filename: str
      display_name: str
      output_suffix: str
      max_parallelism: int

  @dataclass
  class ImageEditQueueItem:
      image_paths: list[str] = field(default_factory=list)
      prompt: str = ""
      settings: dict[str, Any] = field(default_factory=dict)
      model_key: str = ""
      model_id: str = ""
      repetition: int = 1
      # retain item_id/status/task_id/price/output_file/error/created_at and retry()

  def expand_image_edit_batch(config, image_paths, *, repetitions, prompt, settings):
      if repetitions < 1:
          raise ValueError("Las repeticiones deben ser al menos 1.")
      prepared = [str(Path(path).resolve()) for path in image_paths]
      if not prepared:
          raise ValueError("Agregá al menos una imagen.")
      for path in prepared:
          validate_local_reference_file(Path(path), "reference_images")
          config.payload_builder(prompt=prompt, images=[path], **settings)
      return [
          ImageEditQueueItem(image_paths=[path], prompt=prompt, settings=dict(settings),
                             model_key=config.key, model_id=config.model_id, repetition=repetition)
          for path in prepared for repetition in range(1, repetitions + 1)
      ]
  ```

  Add `concurrency: int = 1` to `ImageEditQueue`, clamp invalid persisted values to `1` in `from_dict`, and retain legacy `image_paths` rows exactly as read so previous Qwen campaign rows are not lost.

- [ ] **Step 4: Re-run the focused tests and confirm the batch records persist**

  Run the command from Step 2.

  Expected: PASS, including existing restart-recovery tests.

- [ ] **Step 5: Write failing runner tests for bounded parallelism, failure isolation, and upload reuse**

  Add these tests:

  ```python
  def test_runner_submits_all_twelve_tasks_with_bounded_parallelism(tmp_path: Path):
      # Client increments `active` during submit, records every image-path payload,
      # and returns an immediately completed distinct prediction.
      # Queue has 12 items and concurrency=3; config.max_parallelism=2.
      runner.run(queue)
      assert len(client.submitted) == 12
      assert client.max_active <= 2
      assert {item.status for item in queue.items} == {QueueStatus.COMPLETED}

  def test_runner_continues_when_one_task_fails_and_retry_resubmits_only_that_task(tmp_path: Path):
      # The client raises only for source-2 on its first call, then succeeds.
      runner.run(queue)
      failed = [item for item in queue.items if item.status == QueueStatus.FAILED]
      assert len(failed) == 1
      assert sum(item.status == QueueStatus.COMPLETED for item in queue.items) == 11
      failed[0].retry()
      runner.run(queue)
      assert {item.status for item in queue.items} == {QueueStatus.COMPLETED}
      assert client.submitted.count("source-2.png") == 2

  def test_parallel_repetitions_upload_a_source_once(tmp_path: Path):
      source = tmp_path / "same.png"; source.write_bytes(b"image")
      queue = ImageEditQueue(concurrency=4, items=expand_image_edit_batch(
          QWEN_CONFIG, [str(source)], repetitions=6, prompt="edit", settings=QWEN_SETTINGS))
      uploads = []
      ImageEditQueueRunner(QWEN_CONFIG, client, upload_file=lambda path: uploads.append(path) or {
          "download_url": "https://cdn/same.png"}, save_output=lambda *_: "out.png").run(queue)
      assert uploads == [source]
  ```

- [ ] **Step 6: Replace sequential execution with the common safe worker pool**

  Keep `_process` task-local, but add `self._uploads: dict[str, str]` and `self._upload_lock = Lock()` in the runner. Build uploads with the same canonical path cache used by Flux. Implement `run` as:

  ```python
  pending = [item for item in queue.items if item.status in {QueueStatus.PENDING, QueueStatus.PAUSED}]
  worker_count = min(max(1, queue.concurrency), self.config.max_parallelism, len(pending))
  if queue.paused or not pending:
      return queue
  with ThreadPoolExecutor(max_workers=worker_count) as executor:
      futures = [executor.submit(self._process, item, queue) for item in pending]
      for future in as_completed(futures):
          future.result()
  return queue
  ```

  Before dispatching each task, re-check `queue.paused` inside a bounded scheduling loop so pause leaves undispatched rows pending. Catch exceptions inside `_process`; set only that item to `FAILED`; always call `on_update` after state changes. Derive the empty-output error from `self.config.display_name`, not `Qwen`.

- [ ] **Step 7: Run focused runner coverage**

  Run:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; .\.venv\Scripts\python.exe -m pytest tests/test_image_edit_queue.py -q
  ```

  Expected: PASS; twelve mock tasks submit exactly once, concurrency never exceeds the adapter cap, one failed item does not block siblings, and repeated source uploads occur once.

- [ ] **Step 8: Commit the queue core**

  ```powershell
  git add app/wavespeed/image_edit_queue.py tests/test_image_edit_queue.py
  git commit -m "feat: batch image edit queue execution"
  ```

### Task 2: Define all model adapters and retain legacy Flux campaigns as read-only input

**Files:**
- Modify: `app/wavespeed/image_edit_queue.py`
- Modify: `app/wavespeed/flux_queue.py`
- Modify: `tests/test_image_edit_queue.py`
- Modify: `tests/test_flux_queue.py`

**Interfaces:**
- Produces `flux_queue_config()`, `qwen_image_queue_config()`, and `nano_banana_queue_config()` in the image-edit queue module, or equivalent immutable `ImageEditQueueConfig` values assembled in `MainWindow`.
- Produces `convert_legacy_flux_campaign(campaign: FluxCampaign) -> ImageEditCampaign` without writing to `FluxCampaignStore.path`.

- [ ] **Step 1: Write failing adapter and compatibility tests**

  ```python
  def test_each_adapter_builds_only_its_own_payload(tmp_path: Path):
      source = tmp_path / "source.png"; source.write_bytes(b"image")
      flux = expand_image_edit_batch(FLUX_CONFIG, [str(source)], repetitions=1, prompt="edit",
          settings={"size": "1024*1024", "seed": 7, "enable_sync_mode": False, "enable_base64_output": False})[0]
      nano = expand_image_edit_batch(NANO_CONFIG, [str(source)], repetitions=1, prompt="edit",
          settings={"aspect_ratio": "4:5", "resolution": "4k", "output_format": "jpeg",
                    "enable_sync_mode": False, "enable_base64_output": False})[0]
      qwen = expand_image_edit_batch(QWEN_CONFIG, [str(source)], repetitions=1, prompt="edit", settings=QWEN_SETTINGS)[0]
      assert ImageEditQueueRunner(FLUX_CONFIG, client, upload_file=fake_upload, save_output=fake_save)._payload(flux)["size"] == "1024*1024"
      assert ImageEditQueueRunner(NANO_CONFIG, client, upload_file=fake_upload, save_output=fake_save)._payload(nano)["output_format"] == "jpeg"
      assert "size" not in ImageEditQueueRunner(QWEN_CONFIG, client, upload_file=fake_upload, save_output=fake_save)._payload(qwen)

  def test_legacy_flux_campaign_converts_without_mutating_its_store_file(tmp_path: Path):
      legacy_path = tmp_path / "flux_campaigns.json"
      store = FluxCampaignStore(legacy_path)
      store.save(FluxCampaign(name="Anterior", queue=FluxQueue(items=[FluxQueueItem(
          prompt="edit", image_paths=["C:/old.png"], size="1024*1024", seed=9)])), active=True)
      before = legacy_path.read_bytes()

      campaigns, active_id = load_legacy_flux_campaigns(store)

      assert active_id == campaigns[0].campaign_id
      assert campaigns[0].queue.items[0].model_id == FLUX_MODEL_ID
      assert campaigns[0].queue.items[0].settings["size"] == "1024*1024"
      assert legacy_path.read_bytes() == before
  ```

- [ ] **Step 2: Run and confirm the compatibility tests fail**

  Run:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; .\.venv\Scripts\python.exe -m pytest tests/test_image_edit_queue.py tests/test_flux_queue.py -q
  ```

  Expected: FAIL because Flux/Nano adapter values and legacy conversion do not exist.

- [ ] **Step 3: Add the three immutable adapter profiles and Flux conversion**

  Use the existing `build_flux_payload`, `build_qwen_image_3_edit_payload`, and `build_nano_banana_pro_payload` functions. Each profile must set its real WaveSpeed ID, a presentation name, a file suffix (`.png` for Flux/Qwen and Nano's chosen `output_format` at save time), and a conservative documented-in-code hard maximum of `4`, matching the established Flux queue maximum until account-specific provider capacity is verified.

  Convert each `FluxQueueItem` without loss:

  ```python
  ImageEditQueueItem(
      item_id=old.item_id, image_paths=list(old.image_paths), prompt=old.prompt,
      settings={"size": old.size, "seed": old.seed,
                "enable_sync_mode": old.enable_sync_mode,
                "enable_base64_output": old.enable_base64_output},
      model_key="flux", model_id=FLUX_MODEL_ID, repetition=1,
      status=old.status, task_id=old.task_id, price=old.price,
      output_file=old.output_file, error=old.error, created_at=old.created_at,
  )
  ```

  Keep `FluxCampaignStore` and dataclasses intact for decoder compatibility; remove only imports/calls to `FluxQueueRunner` from new UI code in Task 4.

- [ ] **Step 4: Run adapter and legacy compatibility coverage**

  Run the command from Step 2.

  Expected: PASS; payloads remain model-specific and legacy file bytes are unchanged.

- [ ] **Step 5: Commit the adapters and compatibility layer**

  ```powershell
  git add app/wavespeed/image_edit_queue.py app/wavespeed/flux_queue.py tests/test_image_edit_queue.py tests/test_flux_queue.py
  git commit -m "feat: adapt unified queue to image models"
  ```

### Task 3: Extend the shared queue widget with repetitions, concurrency, and per-result visibility

**Files:**
- Modify: `app/ui/image_edit_queue.py`
- Modify: `tests/test_ui_frame_preview.py`

**Interfaces:**
- `ImageEditQueueWidget.set_limits(max_parallelism: int) -> None` limits the queue concurrency spin box.
- `ImageEditQueueWidget.repetitions() -> int` returns the requested expansion multiplier.
- `ImageEditQueueWidget.queue()` reads `concurrency` into `ImageEditQueue`.

- [ ] **Step 1: Write a failing widget test for queue controls and task rows**

  ```python
  def test_image_edit_queue_widget_exposes_batch_controls_and_result_rows(qtbot):
      widget = ImageEditQueueWidget("Nueva campaña", "Opciones Qwen")
      qtbot.addWidget(widget)
      widget.set_limits(4)
      widget.repetition_spin.setValue(2)
      widget.concurrency_spin.setValue(3)
      item = ImageEditQueueItem(image_paths=["C:/source.png"], prompt="edit", model_key="qwen_image",
          model_id=QWEN_IMAGE_3_EDIT_MODEL_ID, repetition=2, settings=QWEN_SETTINGS)
      widget.load_queue(ImageEditQueue(concurrency=3, items=[item]))

      assert widget.repetitions() == 2
      assert widget.queue().concurrency == 3
      assert widget.table.item(0, widget.REPETITION_COLUMN).text() == "2"
      assert widget.table.item(0, widget.MODEL_COLUMN).text() == QWEN_IMAGE_3_EDIT_MODEL_ID
  ```

- [ ] **Step 2: Run the widget test and confirm it fails**

  Run:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; .\.venv\Scripts\python.exe -m pytest tests/test_ui_frame_preview.py -q
  ```

  Expected: FAIL for missing repetition/concurrency controls and columns.

- [ ] **Step 3: Implement the controls without allowing edits to immutable task identity**

  Add `WheelSafeSpinBox` controls labelled `Generaciones por imagen` (range `1..100`, default `1`) and `Paralelismo` (range `1..config.max_parallelism`, default `1`). Add non-editable `Repetición` and `Modelo` columns to the task table. Keep prompt editable before execution; do not make image path, settings snapshot, repetition, model, task ID, output file, error, or status editable.

  `load_queue` must set the concurrency spin from persisted `queue.concurrency`; it must not overwrite the current repetition control because repetitions affect only newly enqueued items. `set_running(True)` disables both spin boxes, table edits, campaign selectors, and queue-mutating actions.

- [ ] **Step 4: Re-run the UI test**

  Run the command from Step 2.

  Expected: PASS; one item visibly reports repetition and model, and the queue returns concurrency 3.

- [ ] **Step 5: Commit the shared widget**

  ```powershell
  git add app/ui/image_edit_queue.py tests/test_ui_frame_preview.py
  git commit -m "feat: expose image queue batch controls"
  ```

### Task 4: Route all three model tabs through one MainWindow controller and GUI-thread updates

**Files:**
- Modify: `app/ui/main_window.py`
- Modify: `tests/test_ui_frame_preview.py`

**Interfaces:**
- Produces `_enqueue_image_edit_batch(key: str) -> None`, `_start_image_edit_queue(key: str) -> None`, `_retry_image_edit_item(key: str, item_id: str) -> None`, `_load_image_edit_campaigns(key: str) -> None`, and `_handle_image_edit_queue_event(key: str, phase: str, payload: object) -> None`.
- Uses event kinds `image_edit_queue:<key>:item`, `image_edit_queue:<key>:prices`, and `image_edit_queue:<key>:finished` via `_post_wavespeed_event` so widget writes happen only from `customEvent` on the GUI thread.

- [ ] **Step 1: Write a failing end-to-end local UI batch test**

  ```python
  def test_qwen_batch_enqueue_persists_twelve_independent_tasks(tmp_path: Path):
      window = make_main_window(tmp_path)
      sources = add_images_to(window.qwen_image_images, tmp_path, count=6)
      window.qwen_image_prompt_edit.setPlainText("replace background")
      window.image_edit_queues["qwen_image"].repetition_spin.setValue(2)

      window._enqueue_image_edit_batch("qwen_image")

      queue = window.image_edit_queues["qwen_image"].queue()
      assert len(queue.items) == 12
      assert [item.image_paths[0] for item in queue.items].count(sources[0]) == 2
      assert all(item.status == QueueStatus.PENDING for item in queue.items)
      persisted, _ = window.image_edit_campaign_stores["qwen_image"].load()
      assert len(persisted[0].queue.items) == 12

  def test_qwen_batch_picker_keeps_six_independent_sources(tmp_path: Path, monkeypatch):
      window = make_main_window(tmp_path)
      sources = create_image_paths(tmp_path, count=6)
      monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args, **kwargs: (sources, ""))

      window._image_edit_add_images("qwen_image")

      assert window.qwen_image_images.count() == 6

  @pytest.mark.parametrize("key", ["flux", "qwen_image", "nano_banana"])
  def test_each_image_model_exposes_the_same_queue_actions(tmp_path: Path, key: str):
      window = make_main_window(tmp_path)
      controls = window._image_edit_controls[key]
      assert {"queue", "enqueue", "repetitions"} <= controls.keys()
      assert controls["queue"].start_button.text() == "Iniciar cola"
  ```

- [ ] **Step 2: Run the UI file and confirm it fails**

  Run:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; .\.venv\Scripts\python.exe -m pytest tests/test_ui_frame_preview.py -q
  ```

  Expected: FAIL because Flux/Nano lack shared queue controls and Qwen enqueues one multi-image record.

- [ ] **Step 3: Replace Qwen-specific and Flux-specific queue controllers with model-keyed state**

  In `MainWindow.__init__`, build adapters for `flux`, `qwen_image`, and `nano_banana`; one `ImageEditCampaignStore` per model uses new files `flux_image_edit_campaigns.json`, `qwen_image_campaigns.json`, and `nano_banana_campaigns.json`. Maintain runtime state in dictionaries keyed by the same value: campaign ID/name, store, running flag, active queue, and persistence lock.

  For Flux startup only, if the new Flux queue file has no campaign, call the read-only legacy converter from Task 2 and display converted campaigns. Do not save or delete through `FluxCampaignStore`; the first explicit queue save writes the new file only.

  In each image tab (including Flux), add the shared queue widget, bind signals with the model key, and add `Agregar a la cola`. Replace the direct-generation action with `_enqueue_image_edit_batch(key)` followed by `_start_image_edit_queue(key)` only when the user chose the one-click generate button. This ensures even a one-image generation uses durable queue state.

  Change `_image_edit_add_images` so its selection cap is a batch cap (at least 100 source files) rather than the model's per-request image cap. The adapters still validate each newly expanded record with exactly one `images` entry, so Qwen never sends more than its provider maximum of three references in a request. Preserve duplicate-path prevention and the existing add/remove/reorder behavior.

  The enqueue method must take the controls' `snapshot`, separate `output_dir`, call `expand_image_edit_batch(config, snapshot["images"], repetitions=widget.repetitions(), prompt=snapshot["prompt"], settings=model_settings)`, append all returned items before one `save`, set `queue.output_dir`, and report `"12 ítem(s) agregados a la cola."` for six images/two repetitions. Never append a partial subset after validation failure.

- [ ] **Step 4: Run the new UI test and confirm the 6 × 2 proof**

  Run the command from Step 2.

  Expected: PASS; Qwen persists 12 pending records, and Flux/Qwen/Nano each expose the same queue lifecycle.

- [ ] **Step 5: Implement runner events and retry behavior without background widget mutation**

  In the worker, instantiate `ImageEditQueueRunner` with the relevant adapter and emit each state update with `_post_wavespeed_event("image_edit_queue:<key>:item", item)`; persist the queue under its per-model lock before emitting. In `_handle_wavespeed_event`, parse only those event kinds and call `widget.set_item_update`, `widget.set_total_price`, or `widget.set_running` on the GUI thread.

  Use adapter output rules when saving: Flux and Qwen `.png`; Nano uses `f".{item.settings['output_format']}"`. Write history at terminal state under its existing lock. Individual retry calls `item.retry()`, persists, updates the row, and starts the queue; retry-failed resets only `FAILED` items. Pause sets `queue.paused = True`; no new task is dispatched after the runner observes it.

- [ ] **Step 6: Run focused queue and UI regression tests**

  Run:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; .\.venv\Scripts\python.exe -m pytest tests/test_image_edit_queue.py tests/test_flux_queue.py tests/test_ui_frame_preview.py -q
  ```

  Expected: PASS; no model-specific queue path bypasses the generic runner.

- [ ] **Step 7: Commit the UI integration**

  ```powershell
  git add app/ui/main_window.py tests/test_ui_frame_preview.py
  git commit -m "feat: unify image model queue controls"
  ```

### Task 5: Regression proof and local six-by-two smoke test

**Files:**
- Modify only if an assertion exposes a real regression: files from Tasks 1–4.

**Interfaces:**
- Verifies the public UI path from selected images to persisted independent tasks; it makes no live provider request.

- [ ] **Step 1: Run static syntax checks for changed modules**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m py_compile app\wavespeed\image_edit_queue.py app\wavespeed\flux_queue.py app\ui\image_edit_queue.py app\ui\main_window.py
  ```

  Expected: exit code 0.

- [ ] **Step 2: Run the full automated suite**

  Run:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; .\.venv\Scripts\python.exe -m pytest -q
  ```

  Expected: PASS with no collection errors or failures.

- [ ] **Step 3: Perform the local UI smoke test with fixture images and no WaveSpeed submission**

  Launch the normal desktop application with a temporary data directory or injected temporary campaign stores. In each image-edit tab, select six local fixture PNGs, set repetitions to `2`, press `Agregar a la cola`, and inspect the table before pressing `Iniciar cola`.

  Expected for each tab: exactly 12 rows, all `pending`, each source filename appears exactly twice, repetition values are 1 and 2, model settings show only compatible fields, and the campaign JSON contains exactly 12 items. Do not press `Iniciar cola`; this confirms the no-loss enqueue boundary without a billable request.

- [ ] **Step 4: Inspect the final diff and runtime-state boundary**

  Run:

  ```powershell
  git diff --check
  git status --short
  git diff --stat HEAD~4..HEAD
  ```

  Expected: no whitespace errors; only source/tests/docs commits are tracked; ignored `data/` remains unmodified by Git.

- [ ] **Step 5: Commit only a real verification-fix change, if one was necessary**

  If verification exposed a product defect, use a narrow commit such as:

  ```powershell
  git add <changed-source-and-test-files>
  git commit -m "fix: preserve image queue task state"
  ```

  If no source changed in this task, do not create an empty commit.

## Plan self-review

- Spec coverage: Task 1 covers durable state, batch count, concurrency, errors, retries, upload reuse, and restart safety. Task 2 covers Flux/Qwen/Nano adapters and legacy Flux read-only compatibility. Task 3 makes batch controls and states visible. Task 4 integrates all three models and GUI-thread state delivery. Task 5 verifies automated and local non-billable acceptance proof.
- Placeholder scan: clean; each code change has an explicit interface, test, command, and expected outcome.
- Type consistency: `ImageEditQueueConfig`, `ImageEditQueueItem`, `ImageEditQueue`, `expand_image_edit_batch`, and `ImageEditQueueRunner` use the same names across tasks. All UI controller methods are model-keyed by `flux`, `qwen_image`, or `nano_banana`.
