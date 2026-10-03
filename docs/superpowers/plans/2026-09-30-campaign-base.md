# Campaign Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a new editable Sogni campaign from an existing campaign without copying jobs or remote history.

**Architecture:** Add `CampaignManager.clone_campaign_base()` to copy campaign metadata only into a new `READY` row. Add `Usar como base` in the Campaign tab, which selects the copy and loads the editable form fields. Existing `Agregar jobs` remains the only operation that creates jobs.

**Tech Stack:** Python 3, SQLite, PySide6, pytest.

## Global Constraints

- Preserve source campaigns, jobs, outputs, workflow IDs, and runtime state exactly.
- Copy model, folders, prompt source, output, template, organization, concurrency, and `settings_json`, including LoRAs and R2V references.
- The resulting campaign has zero jobs and no remote workflow starts.
- Do not stage `data/`, `errorlogs.txt`, local media, or build outputs.

---

### Task 1: Clone campaign metadata

**Files:**

- Modify: `app/core/campaign_manager.py`
- Test: `tests/test_core_behaviors.py`

**Interface:** `CampaignManager.clone_campaign_base(campaign_id: int) -> Campaign`

- [ ] **Step 1: Write the failing test**

```python
def test_clone_campaign_base_copies_configuration_without_jobs(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    source_id = repo.insert_campaign({
        "name": "R2V master", "status": "COMPLETED",
        "model_id": "minimax-h3-ref2va-fp8_r2v", "model_name": "MiniMax H3 R2V",
        "frames_folder": "C:/media", "prompts_source": "C:/prompts.json",
        "output_folder": "C:/out", "filename_template": "{prompt_id}.mp4",
        "organization_mode": "flat", "concurrency": 2,
        "settings_json": json.dumps({"loras": ["style"], "loraStrengths": [0.7], "reference_media": ["C:/clip.mp4"]}),
    })
    clone = CampaignManager(repo).clone_campaign_base(source_id)
    source = repo.get_campaign(source_id)
    assert clone.id != source.id
    assert clone.name == "R2V master (copia)"
    assert clone.status == "READY"
    assert clone.settings_json == source.settings_json
    assert repo.count_jobs(clone.id) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_core_behaviors.py::test_clone_campaign_base_copies_configuration_without_jobs -q`

Expected: FAIL because `clone_campaign_base` does not exist.

- [ ] **Step 3: Implement the minimal manager method**

```python
def clone_campaign_base(self, campaign_id: int) -> Campaign:
    source = self.repo.get_campaign(campaign_id)
    clone_id = self.repo.insert_campaign({
        "name": f"{source.name} (copia)", "status": "READY",
        "model_id": source.model_id, "model_name": source.model_name,
        "frames_folder": source.frames_folder, "prompts_source": source.prompts_source,
        "output_folder": source.output_folder, "filename_template": source.filename_template,
        "organization_mode": source.organization_mode, "concurrency": source.concurrency,
        "settings_json": source.settings_json,
    })
    return self.repo.get_campaign(clone_id)
```

- [ ] **Step 4: Run the same test and commit**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_core_behaviors.py::test_clone_campaign_base_copies_configuration_without_jobs -q`

Then: `git add app/core/campaign_manager.py tests/test_core_behaviors.py; git commit -m "feat: clone campaign configuration"`

### Task 2: Add the Campaign-tab action and load copied settings

**Files:**

- Modify: `app/ui/main_window.py`
- Test: `tests/test_ui_frame_preview.py`

**Interface:** `MainWindow._use_campaign_as_base() -> None` uses `CampaignManager.clone_campaign_base()`.

- [ ] **Step 1: Write the failing UI test**

```python
def test_use_campaign_as_base_creates_selected_editable_copy(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    source_id = repo.insert_campaign({
        "name": "Original", "status": "COMPLETED", "model_id": "wan22", "model_name": "WAN 2.2",
        "frames_folder": "C:/frames", "prompts_source": "C:/prompts.json", "output_folder": "C:/out",
        "filename_template": "{prompt_id}.mp4", "organization_mode": "flat", "concurrency": 2,
        "settings_json": json.dumps({"duration_mode": "manual", "duration": 8, "aspectRatio": "9:16"}),
    })
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))
    window.current_campaign_id = source_id
    window._use_campaign_as_base()
    clone = repo.get_campaign(window.current_campaign_id)
    assert window.use_campaign_as_base_button.text() == "Usar como base"
    assert clone.name == "Original (copia)"
    assert repo.count_jobs(clone.id) == 0
    assert window.name_edit.text() == "Original (copia)"
    assert window.frames_edit.text() == "C:/frames"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_ui_frame_preview.py::test_use_campaign_as_base_creates_selected_editable_copy -q`

Expected: FAIL because the action does not exist.

- [ ] **Step 3: Implement the button and form loader**

Add `self.use_campaign_as_base_button = QPushButton("Usar como base")` beside `Create Campaign`, connected to `_use_campaign_as_base`. The handler clones the selected campaign, refreshes/selects its new ID, and loads name, folders, model, output, template, organization, concurrency, duration, aspect ratio, filter, LoRAs, and ordered R2V `reference_media` into existing controls. It must show a warning when no campaign is selected.

- [ ] **Step 4: Run the UI test and commit**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_ui_frame_preview.py::test_use_campaign_as_base_creates_selected_editable_copy -q`

Then: `git add app/ui/main_window.py tests/test_ui_frame_preview.py; git commit -m "feat: reuse campaign as editable base"`

### Task 3: Run focused regression checks

**Files:**

- Verify: `tests/test_core_behaviors.py`, `tests/test_ui_frame_preview.py`, `tests/test_sogni_loras.py`

- [ ] **Step 1: Run the focused suite**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_core_behaviors.py tests/test_ui_frame_preview.py tests/test_sogni_loras.py -q`

Expected: PASS.

- [ ] **Step 2: Check code quality and staged scope**

Run: `python -m py_compile app/core/campaign_manager.py app/ui/main_window.py; git diff --check; git status --short`

Expected: no syntax or whitespace errors and no runtime files staged.

## Self-review

- Spec coverage: Task 1 makes an isolated metadata copy, Task 2 provides editable campaign reuse including LoRAs and R2V references, and Task 3 checks source isolation and existing flows.
- Placeholder scan: no incomplete behavior or unspecified interfaces remain.
- Type consistency: Task 1 produces `clone_campaign_base(campaign_id: int) -> Campaign`, which Task 2 consumes.
