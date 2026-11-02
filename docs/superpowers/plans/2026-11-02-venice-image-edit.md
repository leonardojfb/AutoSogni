# Venice Multi-Edit Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Venice image-editing tab inside AutoSogni for ordered one-to-three-image synchronous edits, local output preview/download, and sanitized history.

**Architecture:** Create an isolated `app/venice/` provider layer and a self-contained `app/ui/venice.py` widget. The existing `MainWindow` will only construct the Venice dependencies and add the widget as a tab. Venice requests use documented JSON Base64 inputs and raw binary responses; no WaveSpeed client, upload cache, polling, or queue code is reused.

**Tech Stack:** Python 3, PySide6, `httpx`, JSON, standard-library image-header validation, pytest, `httpx.MockTransport`, Qt offscreen tests.

## Global Constraints

- Work on the current `feature/wavespeed-seedance-flux` branch; preserve all pre-existing modifications in `app/ui/main_window.py`, `app/wavespeed/client.py`, `app/wavespeed/validation.py`, `tests/test_ui_frame_preview.py`, `tests/test_wavespeed.py`, `app/wavespeed/flux3_multi.py`, `tests/test_flux3_multi.py`, `data/`, and `errorlogs.txt`.
- Do not stage or commit unrelated dirty-worktree files.
- Use the official Venice base URL `https://api.venice.ai/api/v1` and `POST /image/multi-edit`.
- Treat image editing as synchronous; do not add polling, automatic generation retries, remote cancellation, or duplicate resubmission after an uncertain timeout.
- Use only the verified requested model IDs: `qwen-image-2-1-pro-edit`, `nano-banana-pro-edit`, `seedream-v5-pro-edit`, and `flux-2-max-edit`.
- Send the prompt literally; do not translate, summarize, expand, or add hidden instructions.
- Send only documented compatible fields. Never send `seed`, `steps`, `guidance`, `negative_prompt`, WaveSpeed fields, or a model-incompatible option.
- Preserve visible reference order. Image 1 is Venice's base image; images 2 and 3 are additional references.
- Keep API keys, authorization headers, Base64 image data, and absolute source paths out of logs and history.
- Do not run paid Venice generations. All provider tests use mocked responses.
- Validate with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `QT_QPA_PLATFORM=offscreen` on Windows.

## File Map

Create:

- `app/venice/__init__.py` — provider package exports.
- `app/venice/validation.py` — catalog profiles, local image limits, and exact payload builders.
- `app/venice/client.py` — HTTP authentication, catalog requests, synchronous multi-edit, and sanitized errors.
- `app/venice/history.py` — local sanitized history store.
- `app/ui/venice.py` — self-contained PySide6 Venice tab.
- `tests/test_venice.py` — validation, client, response, error, and history tests.
- `tests/test_venice_ui.py` — offscreen UI ordering, model-controls, duplicate-click, and preview tests.

Modify:

- `app/sogni/auth.py` — add an optional environment-variable name while preserving the current default `SOGNI_API_KEY` behavior.
- `app/ui/main_window.py` — add only Venice imports, dependency construction, and one tab registration call.
- `README.md` — document the new tab and its synchronous local-image flow.

## Task 1: Add exact Venice model profiles and payload validation

**Files:**

- Create: `app/venice/__init__.py`
- Create: `app/venice/validation.py`
- Test: `tests/test_venice.py`

**Interfaces:**

- `ModelProfile(id: str, label: str, max_input_images: int, prompt_character_limit: int, resolutions: tuple[str, ...], aspect_ratios: tuple[str, ...], default_resolution: str = "")`
- `REQUESTED_MODEL_IDS: tuple[str, ...]`
- `parse_model_catalog(payload: dict) -> dict[str, ModelProfile]`
- `build_multi_edit_payload(*, profile: ModelProfile, prompt: str, images: list[str], aspect_ratio: str = "", resolution: str = "", output_format: str = "png", safe_mode: bool = True) -> dict[str, object]`
- `validate_prompt_and_images(*, profile: ModelProfile, prompt: str, image_paths: list[Path]) -> None`
- `validate_local_image(path: Path) -> ImageInfo`

### Steps

- [ ] Write failing tests for catalog parsing. Feed four catalog entries with the verified `model_spec.constraints` shapes and assert exact IDs, labels, max-image counts, prompt limits, resolution tuples, aspect-ratio tuples, and default resolution. Assert an unknown catalog model is ignored.

```python
def test_parse_model_catalog_keeps_only_requested_edit_models():
    profiles = parse_model_catalog({"data": [
        {"id": "qwen-image-2-1-pro-edit", "model_spec": {
            "name": "Qwen Image 2.1 Pro", "constraints": {
                "maxInputImages": 10, "promptCharacterLimit": 10000,
                "resolutions": ["1K", "2K"], "defaultResolution": "1K",
                "aspectRatios": ["auto", "1:1"]}}},
        {"id": "unrelated-model", "model_spec": {"name": "Other"}},
    ]})
    assert profiles["qwen-image-2-1-pro-edit"].max_input_images == 10
    assert "unrelated-model" not in profiles
```

- [ ] Run `python -m pytest tests/test_venice.py::test_parse_model_catalog_keeps_only_requested_edit_models -q` with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; verify it fails because the module does not exist.

- [ ] Write failing tests for exact payload behavior: omit empty aspect ratio and resolution, include `modelId`, literal prompt, ordered images, output format, and safe mode; reject empty prompts, too many images, overlong prompts, unsupported ratios/resolutions, and non-`png`/`jpeg`/`webp` formats.

```python
def test_payload_preserves_prompt_and_reference_order():
    profile = _profile("qwen-image-2-1-pro-edit", max_input_images=10,
                       resolutions=("1K", "2K"), aspect_ratios=("auto", "1:1"))
    assert build_multi_edit_payload(
        profile=profile,
        prompt="  Keep this exact text.  ",
        images=["base64-a", "base64-b", "base64-c"],
        aspect_ratio="1:1", resolution="2K", output_format="webp", safe_mode=False,
    ) == {
        "modelId": "qwen-image-2-1-pro-edit",
        "prompt": "  Keep this exact text.  ",
        "images": ["base64-a", "base64-b", "base64-c"],
        "aspect_ratio": "1:1", "resolution": "2K",
        "output_format": "webp", "safe_mode": False,
    }
```

- [ ] Run the focused payload tests and confirm they fail for missing builder/validation.

- [ ] Implement `ModelProfile`, requested-ID filtering, profile defaults, exact field omission, and validation. `build_multi_edit_payload` must not add any field that was not passed or required by the selected verified profile.

- [ ] Add stdlib image-header parsing for PNG, JPEG, WebP, and GIF sufficient to enforce the official minimum 256×256 dimensions and total pixel cap of 33,177,600. Reject missing files, unsupported extensions, files at or above 25 MB, malformed headers, and unreadable dimensions with a user-readable `ValueError`.

- [ ] Run the focused validation tests and confirm PASS.

- [ ] Run `git diff --check` and inspect that only the new provider files/tests are staged for this task.

- [ ] Commit with `git add app/venice tests/test_venice.py && git commit -m "feat: add Venice image edit validation"`.

## Task 2: Implement the synchronous Venice HTTP client and key environment isolation

**Files:**

- Create: `app/venice/client.py`
- Modify: `app/sogni/auth.py`
- Test: `tests/test_venice.py`

**Interfaces:**

- `VeniceImageResult(content: bytes, content_type: str, request_id: str, headers: dict[str, str])`
- `VeniceApiError(message: str, status_code: int | None = None, provider_error: str = "")`
- `VeniceClient(api_key: str = "", timeout: float = 120.0, client: httpx.Client | None = None)`
- `VeniceClient.list_edit_models() -> dict[str, ModelProfile]`
- `VeniceClient.multi_edit(payload: dict[str, object]) -> VeniceImageResult`

### Steps

- [ ] Write a failing test that injects `httpx.MockTransport`, calls `list_edit_models`, and asserts `GET /api/v1/models?type=inpaint`, Bearer authentication when a key exists, and parsed profiles.

```python
def test_catalog_request_uses_venice_base_url_and_bearer_header():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [_catalog_entry("qwen-image-2-1-pro-edit")]})
    client = VeniceClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    profiles = client.list_edit_models()
    assert seen[0].url == "https://api.venice.ai/api/v1/models?type=inpaint"
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert "qwen-image-2-1-pro-edit" in profiles
```

- [ ] Write a failing test for `multi_edit` that inspects JSON, verifies the exact payload is sent unchanged, returns raw PNG bytes, extracts `x-request-id`, and never exposes the API key in the result.

```python
def test_multi_edit_returns_binary_result_without_rewriting_payload():
    payload = {"modelId": "qwen-image-2-1-pro-edit", "prompt": "Exact", "images": ["a", "b"]}
    def handler(request):
        assert request.url.path == "/api/v1/image/multi-edit"
        assert json.loads(request.content) == payload
        return httpx.Response(200, content=b"png-bytes",
                              headers={"content-type": "image/png", "x-request-id": "req-1"})
    result = VeniceClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler))).multi_edit(payload)
    assert result.content == b"png-bytes"
    assert result.content_type == "image/png"
    assert result.request_id == "req-1"
```

- [ ] Add parameterized tests for 400, 401, 402, 413, 415, 422, 429, 500, and 503 plus a timeout. Assert `VeniceApiError.status_code`, a useful sanitized message, and that the API key does not appear in `str(error)`.

- [ ] Extend `ApiKeyStore` with `env_name: str = "SOGNI_API_KEY"` while preserving all existing call sites. `get()` will read only the configured environment variable, so Venice can use `ApiKeyStore(data_dir() / "venice_api_key.txt", env_name="VENICE_API_KEY")` without accidentally reading the Sogni key.

- [ ] Implement the client with a single synchronous POST, no retry loop, timeout translation, response content-type validation (`image/png`, `image/jpeg`, `image/webp`), and sanitized JSON error extraction. Do not include request payloads or headers in raised error strings.

- [ ] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_venice.py -q` with `QT_QPA_PLATFORM=offscreen`; confirm PASS.

- [ ] Commit with `git add app/venice/client.py app/sogni/auth.py tests/test_venice.py && git commit -m "feat: add synchronous Venice client"`.

## Task 3: Add sanitized Venice history and binary output saving

**Files:**

- Create: `app/venice/history.py`
- Modify: `tests/test_venice.py`

**Interfaces:**

- `VeniceHistoryStore(path: Path | None = None)`
- `VeniceHistoryStore.list() -> list[dict[str, object]]`
- `VeniceHistoryStore.add(record: dict[str, object]) -> None`
- `sanitize_history_record(record: dict[str, object]) -> dict[str, object]`

### Steps

- [ ] Write a failing history test containing a fake Authorization header, Base64 image, absolute source path, literal prompt, ordered filenames, payload fields, and output path. Assert only non-secret metadata remains and records are capped at 100 with newest first.

```python
def test_venice_history_redacts_secrets_and_source_paths(tmp_path):
    store = VeniceHistoryStore(tmp_path / "venice_history.json")
    store.add({
        "provider": "Venice", "model_id": "qwen-image-2-1-pro-edit",
        "prompt": "Keep exact", "references": [
            {"slot": 1, "filename": "scene.png"},
            {"slot": 2, "filename": "face.png"},
        ],
        "parameters": {"images": ["a" * 100], "Authorization": "Bearer secret"},
        "source_paths": [str(tmp_path / "scene.png")],
        "output_file": str(tmp_path / "out.png"),
    })
    record = store.list()[0]
    encoded = json.dumps(record)
    assert "secret" not in encoded and "Bearer" not in encoded
    assert "a" * 100 not in encoded
    assert str(tmp_path) not in encoded
    assert record["references"] == [{"slot": 1, "filename": "scene.png"},
                                     {"slot": 2, "filename": "face.png"}]
```

- [ ] Run the focused test and verify failure.

- [ ] Implement atomic JSON writes using a sibling `.tmp` file and `os.replace`, lock writes with `threading.Lock`, store under `data/venice_history.json`, and explicitly whitelist record keys rather than recursively retaining arbitrary payload data.

- [ ] Add a small tested helper in `app/venice/client.py` or `app/ui/venice.py` to save response bytes to `output_dir / f"venice_{request_id_or_timestamp}.{suffix}"`, creating the directory and deriving the suffix only from the validated response content type/selected output format.

- [ ] Run the full provider tests and confirm PASS.

- [ ] Commit with `git add app/venice/history.py tests/test_venice.py && git commit -m "feat: persist sanitized Venice history"`.

## Task 4: Build the self-contained Venice PySide6 tab

**Files:**

- Create: `app/ui/venice.py`
- Test: `tests/test_venice_ui.py`

**Interfaces:**

- `VeniceEditWidget(QWidget)`
- `VeniceEditWidget(client: VeniceClient, key_store: ApiKeyStore, history_store: VeniceHistoryStore | None = None, parent: QWidget | None = None)`
- `VeniceEditWidget._ordered_reference_paths() -> list[Path]`
- `VeniceEditWidget._build_request_snapshot() -> dict[str, object]`
- `VeniceEditWidget._build_payload_from_snapshot(snapshot: dict[str, object]) -> dict[str, object]`

### Steps

- [ ] Write an offscreen UI test that constructs the widget with a fake client/key store, fills the three slots with temporary valid PNGs, moves slot 3 above slot 2, and asserts `_ordered_reference_paths()` and the payload image order match the visible order.

```python
def test_venice_slots_preserve_visible_order_after_reorder(tmp_path):
    app = QApplication.instance() or QApplication([])
    widget = VeniceEditWidget(FakeVeniceClient(), ApiKeyStore(tmp_path / "key.txt"),
                              VeniceHistoryStore(tmp_path / "history.json"))
    widget.set_reference_path(0, scene)
    widget.set_reference_path(1, face)
    widget.set_reference_path(2, body)
    widget.move_reference(2, -1)
    assert widget._ordered_reference_paths() == [scene, body, face]
```

- [ ] Add tests that changing models updates resolution/aspect controls from the selected profile, that Qwen is selected by default only after catalog success, and that unavailable catalog entries are not offered as enabled models.

- [ ] Add a test that clicks generate twice while the fake client blocks and asserts only one request starts and the button is disabled during the request.

- [ ] Add a test with a fake successful binary response that verifies output bytes are saved, preview `QPixmap` is populated, and history contains model/prompt/reference order/output path but no Base64.

- [ ] Add a test with a fake provider error that verifies the prompt and selected files remain in the controls and the status contains only the sanitized error.

- [ ] Implement the widget with:
  - a password API-key field, save button, and `VENICE_API_KEY`-aware key store;
  - catalog refresh executed in a daemon worker, with Qwen selected when present;
  - a `QComboBox` storing exact model IDs and displaying the requested readable names;
  - a `QTextEdit` whose `toPlainText()` is sent unchanged;
  - three labeled reference rows with select/replace/remove/up/down controls; internal list order is the only request order;
  - dynamic controls for `aspect_ratio`, `resolution`, `output_format`, and `safe_mode`, with unsupported model controls omitted or disabled;
  - a generate button protected by an `_request_active` flag and `try/finally` restoration;
  - a worker signal carrying either `VeniceImageResult`/saved-output metadata or an exception back to the Qt thread;
  - a preview `QLabel` using `QPixmap` from the saved file, output-folder chooser, status label, and history table;
  - no polling timer, retry button, remote-cancel button, or WaveSpeed import.

- [ ] Ensure all worker snapshots contain copied lists/dicts and `Path` values before the thread starts, so subsequent UI edits cannot reorder or mutate an in-flight request.

- [ ] Run `QT_QPA_PLATFORM=offscreen PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_venice_ui.py -q`; confirm PASS.

- [ ] Commit with `git add app/ui/venice.py tests/test_venice_ui.py && git commit -m "feat: add Venice image edit tab"`.

## Task 5: Wire Venice into MainWindow and document usage

**Files:**

- Modify: `app/ui/main_window.py`
- Modify: `README.md`
- Test: `tests/test_venice_ui.py`

**Interfaces:**

- Extend `MainWindow.__init__` only with optional trailing arguments: `venice_client=None`, `venice_key_store=None`, `venice_history_store=None`.
- Construct defaults from `data_dir() / "venice_api_key.txt"` and `VeniceHistoryStore()`.
- Register `VeniceEditWidget(...)` as a root tab labeled exactly `Venice`.

### Steps

- [ ] Write a regression test constructing `MainWindow` with existing fake/default dependencies and assert the root tab list contains `Venice`, the widget has a separate key-store path, and the existing WaveSpeed/BytePlus/Image tabs are still present.

- [ ] Run that focused UI regression test before editing and record the expected failure.

- [ ] Add only the Venice imports, optional constructor arguments, dependency defaults, and one `self.tabs.addTab(self.venice_tab, "Venice")` wiring point. Do not reformat or rewrite unrelated dirty sections of `main_window.py`.

- [ ] Update `README.md` with a short `Venice` section: configure the key in the tab or `VENICE_API_KEY`, refresh the verified model catalog, fill 1–3 ordered references, generate synchronously, and find saved images/history under the local data directory. State that no paid generation is used by tests.

- [ ] Run focused UI tests and then the pre-existing UI/provider tests that cover `MainWindow`, WaveSpeed, and image edits.

- [ ] Inspect `git diff -- app/ui/main_window.py README.md` to verify no existing local WaveSpeed changes were overwritten or staged accidentally.

- [ ] Commit with `git add app/ui/main_window.py README.md tests/test_venice_ui.py && git commit -m "feat: expose Venice tab in AutoSogni"`.

## Task 6: Full verification and handoff evidence

**Files:**

- Modify only if verification finds a defect: the files from Tasks 1–5.

### Steps

- [ ] Run the complete suite with Windows-safe settings:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q
```

Expected: all tests pass; no Venice network request is made.

- [ ] Run `git diff --check` and `git status --short`.

- [ ] Review changed-file scope and confirm user runtime data (`data/`, `errorlogs.txt`, local keys, outputs, logs) is not staged.

- [ ] Run a static secret scan over tracked Venice source/tests for `VENICE_API_KEY` values, `Bearer ` literals containing credentials, Base64 fixtures larger than necessary, and accidental absolute source paths. The only allowed key references are environment-variable names and runtime header construction.

- [ ] If packaging is requested later, run the existing PyInstaller build separately; this implementation does not claim packaged-executable proof.

- [ ] Report exact verification evidence: test count/result, model IDs confirmed by the live catalog/docs, output/history paths, and the explicit limitation that no paid live generation was performed.

## Plan self-review

- Coverage: catalog/model availability, model-specific fields, ordered references, prompt fidelity, synchronous execution, output preview/save, history sanitization, key isolation, duplicate-click protection, errors, regressions, and no-paid-call validation are covered by Tasks 1–6.
- Placeholder scan: no incomplete marker or unspecified edge-case step is present.
- Interface consistency: `ModelProfile` and `VeniceClient` are introduced before UI consumption; the UI consumes `VeniceImageResult`, `VeniceHistoryStore`, and `build_multi_edit_payload` exactly as defined.
- Scope: no provider queues, video work, or unrelated refactoring is included.
