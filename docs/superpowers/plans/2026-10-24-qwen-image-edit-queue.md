# Qwen Image 3.0 Edit Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, recoverable Qwen Image 3.0 Edit queue, while exposing only Qwen in the UI.

**Architecture:** A generic image-edit queue core accepts immutable model configuration; only Qwen is registered. `MainWindow` bridges Qwen to the existing upload cache, saver, and history, without modifying Flux or Nano Banana.

**Tech Stack:** Python 3.14, PySide6, dataclasses, JSON, threading, pytest.

## Global Constraints

- Store only Qwen campaigns in `data/qwen_image_campaigns.json`.
- Preserve `data/`, WaveSpeed history, Flux queue format/UI, and Nano Banana behavior.
- Validate every snapshot and local file before uploads or paid submission.
- Persist `task_id` before polling and query it before resubmission.
- Register only Qwen, using `alibaba/qwen-image-3.0/edit` and `build_qwen_image_3_edit_payload`.
- Process sequentially; a pause starts no subsequent item.
- Tests use fakes and make no paid WaveSpeed request.

---

### Task 1: Generic queue domain and runner

**Files:**

- Create: `app/wavespeed/image_edit_queue.py`
- Create: `tests/test_image_edit_queue.py`

**Interfaces:**

- Consumes: `QueueStatus`, `TERMINAL_STATUSES`, `validate_local_reference_file`, a payload builder, and WaveSpeed `estimate_price`, `submit`, `get_result`, `poll_result`.
- Produces: `ImageEditQueueConfig`, `ImageEditQueueItem`, `ImageEditQueue`, `ImageEditCampaign`, `ImageEditCampaignStore`, and `ImageEditQueueRunner`.

- [ ] **Step 1: Write the failing persistence and recovery tests.**

```python
def test_store_recovers_running_item(tmp_path: Path):
    store = ImageEditCampaignStore(tmp_path / "qwen_image_campaigns.json")
    campaign = ImageEditCampaign(queue=ImageEditQueue(items=[ImageEditQueueItem(
        prompt="replace background", image_paths=["C:/input.png"],
        settings={"resolution": "2k", "enable_prompt_expansion": False, "seed": 7},
        status=QueueStatus.RUNNING, task_id="pred-1")]))
    store.save(campaign, active=True)
    campaigns, active_id = store.load()
    assert active_id == campaign.campaign_id
    assert campaigns[0].queue.items[0].status == QueueStatus.PENDING
    assert campaigns[0].queue.items[0].task_id == "pred-1"

def test_runner_recovers_task_without_second_submit(tmp_path: Path):
    item = ImageEditQueueItem(prompt="edit", image_paths=[str(tmp_path / "missing.png")], task_id="existing")
    queue = ImageEditQueue(items=[item], output_dir=str(tmp_path))
    client = FakeClient(completed_task="existing")
    ImageEditQueueRunner(QWEN_CONFIG, client, upload_file=fail_upload, save_output=lambda *_: "out.png").run(queue)
    assert client.submitted == []
    assert client.queried == ["existing"]
    assert item.status == QueueStatus.COMPLETED
```

- [ ] **Step 2: Run the focused test and prove red.**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue.py -q`

Expected: FAIL because `app.wavespeed.image_edit_queue` is absent.

- [ ] **Step 3: Implement the types and atomic persistence.**

```python
@dataclass(frozen=True)
class ImageEditQueueConfig:
    key: str
    model_id: str
    payload_builder: Callable[..., dict[str, Any]]
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
```

Implement `to_dict()` and `from_dict()` for persisted types. `ImageEditQueue.from_dict()` maps `RUNNING` and `PAUSED` to `PENDING`, preserving `task_id`. Under a `Lock`, store `active_id` and campaigns to a sibling `.tmp` then call `os.replace`.

- [ ] **Step 4: Implement runner submission, recovery, failures, and pause.**

```python
def _payload(self, item: ImageEditQueueItem) -> dict[str, Any]:
    local = {"prompt": item.prompt, "images": item.image_paths, **item.settings}
    self.config.payload_builder(**local)
    urls = []
    for name in item.image_paths:
        path = Path(name)
        validate_local_reference_file(path, "reference_images")
        urls.append(self._upload(path))
    return self.config.payload_builder(prompt=item.prompt, images=urls, **item.settings)
```

Existing `task_id`s call `get_result()` with neither upload nor submit. New items estimate, submit with `config.model_id`, persist their ID, then poll. Save the first completed output; mark terminal/provider errors only on the relevant item. Iterate in order and break before another item when `queue.paused` is true.

- [ ] **Step 5: Add the failing pause test, turn it green, then run the domain suite.**

```python
def test_pause_leaves_next_item_pending(tmp_path: Path):
    queue = ImageEditQueue(items=[make_item(tmp_path, "first"), make_item(tmp_path, "second")])
    runner = ImageEditQueueRunner(QWEN_CONFIG, successful_client(), upload_file=upload,
        save_output=lambda *_: "out.png", on_update=lambda item: setattr(queue, "paused", True)
        if item.status == QueueStatus.COMPLETED else None)
    runner.run(queue)
    assert queue.items[0].status == QueueStatus.COMPLETED
    assert queue.items[1].status == QueueStatus.PENDING
```

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the domain deliverable.**

```powershell
git add app/wavespeed/image_edit_queue.py tests/test_image_edit_queue.py
git commit -m "feat: add generic image edit queue core"
```

### Task 2: Generic queue widget

**Files:**

- Create: `app/ui/image_edit_queue.py`
- Create: `tests/test_image_edit_queue_widget.py`

**Interfaces:**

- Consumes: image-edit campaign, queue, and item types.
- Produces: `ImageEditQueueWidget` with campaign, start, pause, retry, clear, estimate, and queue-changed signals.

- [ ] **Step 1: Write the failing rendering/running-state test.**

```python
def test_widget_displays_settings_and_disables_mutation_while_running(qapp):
    widget = ImageEditQueueWidget("Nueva campaña Qwen", "Opciones Qwen")
    item = ImageEditQueueItem(prompt="keep face", image_paths=["C:/one.png"], settings={"resolution": "2k", "seed": 42})
    widget.load_queue(ImageEditQueue(items=[item]))
    assert widget.table.item(0, widget.SETTINGS_COLUMN).text() == "resolution=2k; seed=42"
    widget.set_running(True)
    assert not widget.start_button.isEnabled()
    assert not widget.estimate_button.isEnabled()
```

- [ ] **Step 2: Run it and prove red.**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue_widget.py -q`

Expected: FAIL because `ImageEditQueueWidget` is absent.

- [ ] **Step 3: Implement widget rendering and control state.**

```python
class ImageEditQueueWidget(QWidget):
    COLUMNS = ("#", "Imágenes", "Prompt", "Opciones", "Precio", "Estado", "Task", "Archivo", "Error", "Reintentar")
    SETTINGS_COLUMN = 3
    def queue(self) -> ImageEditQueue:
        self._sync_prompt_cells()
        return self._queue
    def set_item_update(self, item: ImageEditQueueItem) -> None:
        self._replace_row(item)
```

Render sorted settings as semicolon-separated `key=value`. Prompt is the only editable snapshot field; paths/settings remain immutable. Retry only failed rows. During a run, disable mutation, retry, clear, and estimate while keeping pause available.

- [ ] **Step 4: Run widget tests and commit.**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue_widget.py -q`

Expected: PASS.

```powershell
git add app/ui/image_edit_queue.py tests/test_image_edit_queue_widget.py
git commit -m "feat: add image edit queue widget"
```

### Task 3: Qwen-only UI wiring

**Files:**

- Modify: `app/ui/main_window.py`
- Modify: `tests/test_ui_frame_preview.py`

**Interfaces:**

- Consumes: generic core/widget, `QWEN_IMAGE_3_EDIT_MODEL_ID`, and `build_qwen_image_3_edit_payload`.
- Produces: Qwen-only enqueue, persistence, start, pause, retry, clear, estimate, and event handling under `qwen_image`.

- [ ] **Step 1: Write the failing frozen-snapshot integration test.**

```python
def test_qwen_queue_persists_frozen_snapshot(tmp_path: Path):
    window = make_window(tmp_path)
    source = tmp_path / "source.png"; source.write_bytes(b"image")
    add_image(window.qwen_image_images, source)
    window.qwen_image_prompt_edit.setPlainText("replace the wall")
    window.qwen_image_resolution_combo.setCurrentText("2k")
    window.qwen_image_prompt_expansion_check.setChecked(False)
    window.qwen_image_random_seed_check.setChecked(False)
    window.qwen_image_seed_spin.setValue(42)
    window._image_edit_add_current_to_queue("qwen_image")
    campaigns, _ = ImageEditCampaignStore(tmp_path / "qwen_image_campaigns.json").load()
    assert campaigns[0].queue.items[0].settings == {"aspect_ratio": "", "resolution": "2k", "enable_prompt_expansion": False, "seed": 42}
```

- [ ] **Step 2: Run it and prove red.**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_ui_frame_preview.py::test_qwen_queue_persists_frozen_snapshot -q`

Expected: FAIL because Qwen queue controls and handler are absent.

- [ ] **Step 3: Register only Qwen and add controls.**

```python
self.image_edit_queue_configs = {
    "qwen_image": ImageEditQueueConfig("qwen_image", QWEN_IMAGE_3_EDIT_MODEL_ID,
        build_qwen_image_3_edit_payload, "qwen_image_campaigns.json", 3),
}
```

Create its store at `data_dir() / config.campaign_filename`, and add/attach generic queue controls only in the Qwen tab. Do not construct Nano state, store, widget, or a Nano configuration.

- [ ] **Step 4: Implement Qwen settings and runtime callbacks.**

```python
def _image_edit_queue_settings(self, key: str, snapshot: dict) -> dict:
    if key != "qwen_image":
        raise ValueError(f"No image-edit queue configuration for {key}.")
    return {name: snapshot[name] for name in ("aspect_ratio", "resolution", "enable_prompt_expansion", "seed")}
```

Validate at enqueue/start, persist each callback, use the upload cache and `.png` output saver, and write terminal history with Qwen's model ID. Route distinct Qwen item/price/finished events and restore UI controls on completion.

- [ ] **Step 5: Run focused regression tests and commit.**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit_queue.py tests/test_image_edit_queue_widget.py tests/test_ui_frame_preview.py -q`

Expected: PASS, including Flux UI coverage.

```powershell
git add app/ui/main_window.py tests/test_ui_frame_preview.py
git commit -m "feat: queue Qwen image edits"
```

### Task 4: Verification

**Files:**

- Modify: `docs/superpowers/plans/2026-10-24-qwen-image-edit-queue.md`

- [ ] **Step 1: Run full regression.**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ..\\..\\.venv\\Scripts\\python.exe -m pytest -q`

Expected: PASS; report warnings separately.

- [ ] **Step 2: Check tracked/runtime boundaries.**

Run: `git diff --check origin/feature/image-edit-queues..HEAD; git status --short; git diff --name-only origin/feature/image-edit-queues..HEAD`

Expected: no whitespace faults and no data, keys, outputs, or logs staged.

- [ ] **Step 3: Mark complete and commit verification.**

```powershell
git add docs/superpowers/plans/2026-10-24-qwen-image-edit-queue.md
git commit -m "docs: verify Qwen image edit queue"
```
