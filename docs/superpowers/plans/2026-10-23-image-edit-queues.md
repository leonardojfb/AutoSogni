# Independent Image Edit Queues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add independent, persistent Nano Banana Pro and Qwen Image 3.0 Edit queues with Flux-equivalent recovery and controls.

**Architecture:** A generic image-edit queue core receives an immutable configuration with a fixed model ID and payload builder. Two stores use distinct JSON paths, while a model-neutral widget renders their campaigns and items. \`MainWindow\` supplies model-specific snapshots and bridges queue callbacks to the existing WaveSpeed upload, output, and history services.

**Tech Stack:** Python 3.14, PySide6, dataclasses, JSON, threading, httpx, pytest.

## Global Constraints

- Keep Nano Banana Pro state in \`data/nano_banana_campaigns.json\` and Qwen Image 3.0 Edit state in \`data/qwen_image_campaigns.json\`.
- Preserve every existing \`data/\` file, WaveSpeed history, Flux queue format, and Flux UI behavior.
- Validate the complete local item snapshot before upload or paid submission.
- Persist the remote task ID before polling; recover it before considering a second submission.
- Keep the model ID and model-specific payload fields fixed per queue.
- Use a maximum of four workers in parallel mode and never submit another item after a pause request.
- Automated tests use fakes and never submit paid WaveSpeed requests.

---

### Task 1: Shared image-edit queue domain and persistence

**Files:**
- Create: \`app/wavespeed/image_edit_queue.py\`
- Create: \`tests/test_image_edit_queue.py\`

**Interfaces:**
- Produces \`ImageEditQueueConfig(key, model_id, payload_builder, campaign_filename, max_images)\`.
- Produces \`ImageEditQueueItem\`, \`ImageEditQueue\`, \`ImageEditCampaign\`, \`ImageEditCampaignStore\`, and \`ImageEditQueueRunner\`.
- Consumes \`QueueStatus\`, \`TERMINAL_STATUSES\`, \`validate_local_reference_file\`, and the WaveSpeed methods \`estimate_price\`, \`submit\`, \`get_result\`, and \`poll_result\`.

- [ ] **Step 1: Write the failing store-isolation test.**

~~~python
def test_campaign_stores_keep_nano_and_qwen_files_independent(tmp_path: Path):
    nano_store = ImageEditCampaignStore(tmp_path / "nano_banana_campaigns.json")
    qwen_store = ImageEditCampaignStore(tmp_path / "qwen_image_campaigns.json")
    nano_store.save(ImageEditCampaign(name="Nano", queue=ImageEditQueue(
        items=[ImageEditQueueItem(prompt="nano")])), active=True)
    qwen_store.save(ImageEditCampaign(name="Qwen", queue=ImageEditQueue(
        items=[ImageEditQueueItem(prompt="qwen")])), active=True)

    assert nano_store.load()[0][0].queue.items[0].prompt == "nano"
    assert qwen_store.load()[0][0].queue.items[0].prompt == "qwen"
~~~

- [ ] **Step 2: Run the test to prove it fails.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue.py::test_campaign_stores_keep_nano_and_qwen_files_independent -q\`

Expected: FAIL because \`app.wavespeed.image_edit_queue\` is absent.

- [ ] **Step 3: Implement the serializable queue types and atomic store.**

~~~python
@dataclass(frozen=True)
class ImageEditQueueConfig:
    key: str
    model_id: str
    payload_builder: Callable
    campaign_filename: str
    max_images: int

@dataclass
class ImageEditQueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    image_paths: list[str] = field(default_factory=list)
    prompt: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    status: str = QueueStatus.PENDING
    task_id: str = ""
    price: float | None = None
    output_file: str = ""
    error: str = ""
~~~

Give every persisted dataclass \`to_dict\` and \`from_dict\`. Build a \`campaigns_payload\` list from each campaign's \`to_dict\`, then write \`{"active_id": active_id, "campaigns": campaigns_payload}\` to \`<campaign_filename>.tmp\` and replace the target with \`os.replace\` while holding the store lock in \`save\` and \`delete\`.

- [ ] **Step 4: Run the persistence test to prove it passes.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue.py::test_campaign_stores_keep_nano_and_qwen_files_independent -q\`

Expected: PASS.

- [ ] **Step 5: Write failing runner tests for schema routing and recovery.**

~~~python
def test_runner_recovers_existing_task_without_second_submit(tmp_path: Path, fake_client):
    source = tmp_path / "input.png"
    source.write_bytes(b"image")
    item = ImageEditQueueItem(image_paths=[str(source)], prompt="edit", task_id="existing")
    queue = ImageEditQueue(items=[item], output_dir=str(tmp_path))

    ImageEditQueueRunner(NANO_CONFIG, fake_client, upload_file=fail_upload,
                         save_output=save_output).run(queue)

    assert fake_client.submitted == []
    assert fake_client.queried == ["existing"]
~~~

- [ ] **Step 6: Implement validation, upload, submit, polling, pause, and estimation.**

~~~python
def _payload(self, item: ImageEditQueueItem) -> dict[str, Any]:
    local = {"prompt": item.prompt, "images": item.image_paths, **item.settings}
    self.config.payload_builder(**local)
    urls = [self._upload(Path(name)) for name in item.image_paths]
    return self.config.payload_builder(prompt=item.prompt, images=urls, **item.settings)

def _process(self, item: ImageEditQueueItem, queue: ImageEditQueue,
             cancel_event=None) -> None:
    prediction = self.client.get_result(item.task_id) if item.task_id else self.client.submit(
        self._payload(item), model_id=self.config.model_id)
    item.task_id = prediction.id
~~~

Validate every local path before calling the upload callback. Notify after status changes, price assignment, task-ID assignment, and terminal outcomes. Save the first completed output URL; mark another terminal result failed with the provider error. Sequential mode checks \`queue.paused\` before every item. Parallel mode uses \`min(4, len(pending))\` workers and starts no work if paused.

- [ ] **Step 7: Run the domain suite.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue.py -q\`

Expected: PASS, including JSON round trips, schema-specific payload fields, no duplicate submission, failure, pause, and price estimation.

- [ ] **Step 8: Commit the domain deliverable.**

~~~powershell
git add app/wavespeed/image_edit_queue.py tests/test_image_edit_queue.py
git commit -m "feat: add persistent image edit queue core"
~~~

### Task 2: Model-neutral queue widget

**Files:**
- Create: \`app/ui/image_edit_queue.py\`
- Create: \`tests/test_image_edit_queue_widget.py\`

**Interfaces:**
- Produces \`ImageEditQueueWidget(default_campaign_name: str, settings_label: str)\`.
- Consumes \`ImageEditQueue\` and \`ImageEditQueueItem\`.
- Produces Qt signals \`campaign_selected\`, \`new_campaign_requested\`, \`save_campaign_requested\`, \`delete_campaign_requested\`, \`start_requested\`, \`pause_requested\`, \`retry_requested\`, \`retry_failed_requested\`, \`clear_completed_requested\`, \`estimate_requested\`, and \`queue_changed\`.

- [ ] **Step 1: Write the failing rendering test.**

~~~python
def test_widget_displays_image_edit_campaign_and_item(qapp):
    widget = ImageEditQueueWidget("Nueva campaña Nano Banana Pro", "Opciones")
    item = ImageEditQueueItem(prompt="preserve lighting", image_paths=["C:/input.png"],
                              settings={"resolution": "2k"})
    widget.load_queue(ImageEditQueue(items=[item]))

    assert widget.campaign_name_edit.text() == "Nueva campaña Nano Banana Pro"
    assert widget.table.item(0, widget.SETTINGS_COLUMN).text() == "resolution=2k"
~~~

- [ ] **Step 2: Run the test to prove it fails.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue_widget.py::test_widget_displays_image_edit_campaign_and_item -q\`

Expected: FAIL because \`ImageEditQueueWidget\` is absent.

- [ ] **Step 3: Implement the widget and editing boundary.**

~~~python
class ImageEditQueueWidget(QWidget):
    COLUMNS = ("#", "Imágenes", "Prompt", "Opciones", "Precio", "Estado",
               "Task", "Archivo", "Error", "Reintentar")
    SETTINGS_COLUMN = 3

    def load_queue(self, queue: ImageEditQueue) -> None:
        self._queue = queue
        self._reload_rows()

    def queue(self) -> ImageEditQueue:
        self._sync_prompt_cells()
        return self._queue

    def set_item_update(self, updated: ImageEditQueueItem) -> None:
        self._replace_row_for(updated)

    def set_campaigns(self, campaigns: list[ImageEditCampaign],
                      active_id: str | None) -> None:
        self._replace_campaign_choices(campaigns, active_id)

    def set_running(self, running: bool) -> None:
        self._set_mutating_controls_enabled(not running)
~~~

Use a sorted \`key=value\` summary for \`settings\`. Permit edits only to the prompt; image paths and settings stay frozen. Disable campaign, table, retry, clear, estimate, and removal controls during execution. Enable the per-item retry button only when status is \`FAILED\`.

- [ ] **Step 4: Run widget tests to prove they pass.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue_widget.py -q\`

Expected: PASS for load, prompt synchronization, item updates, and the running state.

- [ ] **Step 5: Commit the widget deliverable.**

~~~powershell
git add app/ui/image_edit_queue.py tests/test_image_edit_queue_widget.py
git commit -m "feat: add image edit queue widget"
~~~

### Task 3: Wire independent Nano and Qwen queues into the image tabs

**Files:**
- Modify: \`app/ui/main_window.py\`
- Modify: \`tests/test_ui_frame_preview.py\`

**Interfaces:**
- Consumes \`ImageEditQueueConfig\`, \`ImageEditCampaignStore\`, \`ImageEditQueueRunner\`, and \`ImageEditQueueWidget\`.
- Produces \`_image_edit_add_current_to_queue(key)\`, \`_start_image_edit_queue(key)\`, \`_pause_image_edit_queue(key)\`, \`_retry_image_edit_item(key, item_id)\`, \`_retry_image_edit_failed(key)\`, \`_clear_image_edit_completed(key)\`, and \`_estimate_image_edit_queue_prices(key)\`.

- [ ] **Step 1: Write the failing UI isolation test.**

~~~python
def test_image_edit_queues_keep_nano_and_qwen_snapshots_separate(tmp_path: Path):
    window = make_window(tmp_path)
    nano = window._image_edit_controls["nano_banana"]
    qwen = window._image_edit_controls["qwen_image"]
    add_image(nano["images"], tmp_path / "nano.png")
    add_image(qwen["images"], tmp_path / "qwen.png")

    window._image_edit_add_current_to_queue("nano_banana")
    window._image_edit_add_current_to_queue("qwen_image")

    assert (tmp_path / "nano_banana_campaigns.json").exists()
    assert (tmp_path / "qwen_image_campaigns.json").exists()
~~~

- [ ] **Step 2: Run the focused UI test to prove it fails.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_ui_frame_preview.py::test_image_edit_queues_keep_nano_and_qwen_snapshots_separate -q\`

Expected: FAIL because queue controls and handlers are absent.

- [ ] **Step 3: Add fixed model configurations and queue controls.**

~~~python
self.image_edit_queue_configs = {
    "nano_banana": ImageEditQueueConfig(
        "nano_banana", NANO_BANANA_PRO_MODEL_ID,
        build_nano_banana_pro_payload, "nano_banana_campaigns.json", 14),
    "qwen_image": ImageEditQueueConfig(
        "qwen_image", QWEN_IMAGE_3_EDIT_MODEL_ID,
        build_qwen_image_3_edit_payload, "qwen_image_campaigns.json", 3),
}
~~~

Extend \`_image_edit_tab(key)\` with \`Agregar a la cola\` and an \`ImageEditQueueWidget\`. Keep dictionaries keyed by model for campaign ID, campaign name, store, persistence lock, active queue, and running flag. Configure each store at \`data_dir() / config.campaign_filename\`. Connect each widget signal to a lambda that passes its immutable key.

- [ ] **Step 4: Implement persistence, runner callbacks, and history updates.**

~~~python
def _image_edit_queue_snapshot(self, key: str) -> ImageEditQueueItem:
    snapshot = self._image_edit_snapshot(key)
    return ImageEditQueueItem(image_paths=list(snapshot["images"]),
                              prompt=snapshot["prompt"],
                              settings=self._image_edit_queue_settings(key, snapshot))

def _persist_image_edit_campaign_queue(self, key: str,
                                       queue: ImageEditQueue) -> None:
    campaign = ImageEditCampaign(campaign_id=self._image_edit_campaign_ids[key],
                                 name=self._image_edit_campaign_names[key], queue=queue)
    self._image_edit_campaign_stores[key].save(campaign, active=True)

def _image_edit_queue_item_update(self, key: str, queue: ImageEditQueue,
                                  item: ImageEditQueueItem) -> None:
    self._persist_image_edit_campaign_queue(key, queue)
    self._post_wavespeed_event(f"{key}_queue_item", item)
~~~

Build items from \`_image_edit_snapshot(key)\` and copy only that model's fields into \`item.settings\`. Before enqueue or run, invoke the configured payload builder using local paths and validate every path with \`validate_local_reference_file\`. Run uploads through \`self._wavespeed_upload_cached\`; save results with \`self._wavespeed_save_output\`; and write terminal task records to \`self.wavespeed_history\` using the configuration model ID. Route item, estimate, and completion callbacks through distinct \`_post_wavespeed_event\` kinds for each key.

- [ ] **Step 5: Run focused integration tests.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_ui_frame_preview.py -q\`

Expected: PASS for independent persistence, fake-client execution, task recovery, and unchanged Flux campaign data.

- [ ] **Step 6: Commit the UI integration.**

~~~powershell
git add app/ui/main_window.py tests/test_ui_frame_preview.py
git commit -m "feat: queue Nano Banana and Qwen edits"
~~~

### Task 4: Regression validation and release boundary

**Files:**
- Modify: \`docs/superpowers/plans/2026-10-23-image-edit-queues.md\`

**Interfaces:**
- Consumes the completed queue domain, widget, and \`MainWindow\` integration.
- Produces verification evidence without adding runtime state to Git.

- [ ] **Step 1: Run the queue-focused suite.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue.py tests/test_image_edit_queue_widget.py tests/test_ui_frame_preview.py -q\`

Expected: PASS.

- [ ] **Step 2: Run the full regression suite.**

Run: \`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest -q\`

Expected: PASS; report dependency deprecation warnings separately from failures.

- [ ] **Step 3: Inspect tracked changes and runtime-state boundary.**

~~~powershell
git diff --check origin/master..HEAD
git status --short
git diff --name-only origin/master..HEAD
~~~

Expected: only implementation, tests, and design/plan documentation are tracked; \`data/\`, keys, outputs, logs, build artifacts, and distributions remain untracked or ignored.

- [ ] **Step 4: Commit the verified plan.**

~~~powershell
git add docs/superpowers/plans/2026-10-23-image-edit-queues.md
git commit -m "docs: plan image edit queues"
~~~

