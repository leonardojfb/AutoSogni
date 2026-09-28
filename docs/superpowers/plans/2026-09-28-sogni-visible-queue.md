# Cola Sogni visible por campaña Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir jobs a la campaña Sogni seleccionada y mostrar su cola en Campaign.

**Architecture:** `CampaignManager` crea jobs nuevos en una campaña existente con índices y claves posteriores a los existentes. `MainWindow` cambia el botón ambiguo y presenta una tabla de cola con el mismo contenido esencial que Jobs.

**Tech Stack:** Python 3.14, PySide6, SQLite, pytest.

## Global Constraints

- No borrar ni modificar campañas, jobs, claves ni SQLite de runtime existentes.
- Solo Crear campaña nueva crea una campaña.
- Agregar jobs no inicia una generación ni llama a Sogni.

---

### Task 1: Agregar la matriz de jobs a una campaña existente

**Files:**
- Modify: `app/core/campaign_manager.py`
- Modify: `tests/test_core_behaviors.py`

**Interfaces:** Produces `CampaignManager.add_jobs_to_campaign(campaign_id: int, frames_folder: Path, prompts_source: Path) -> int`.

- [x] **Step 1: Write the failing test**

Add a campaign with one existing job, then call the new API with one frame and two prompts:

```python
added = CampaignManager(repo).add_jobs_to_campaign(campaign.id, extra_frames, extra_prompts)
assert added == 2
assert repo.get_campaign(campaign.id).id == campaign.id
assert [job.order_index for job in repo.list_jobs(campaign.id)] == [1, 2, 3]
assert [job.idempotency_key for job in repo.list_jobs(campaign.id)] == [
    f"sva:{campaign.id}:0001", f"sva:{campaign.id}:0002", f"sva:{campaign.id}:0003"
]
```

- [x] **Step 2: Verify RED**

Run `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest tests/test_core_behaviors.py::test_add_jobs_to_existing_campaign_keeps_campaign_and_orders_jobs_after_existing -q`. Expected: FAIL because the method does not exist.

- [x] **Step 3: Implement the minimal API**

Validate/import current inputs, create their frame and prompt records under `campaign_id`, compute `max(job.order_index, default=0) + 1`, insert their Cartesian product with `PENDING` and `f"sva:{campaign_id}:{order_index:04d}"`, then return the inserted count.

- [x] **Step 4: Verify GREEN and commit**

Run the Step 2 command; expected PASS. Commit with `git add app/core/campaign_manager.py tests/test_core_behaviors.py; git commit -m "feat: add jobs to selected Sogni campaign"`.

### Task 2: Mostrar la cola y usar la campaña seleccionada

**Files:**
- Modify: `app/ui/main_window.py:153-227,1682-1721,1736-1789`
- Modify: `tests/test_ui_frame_preview.py`

**Interfaces:** Consumes `add_jobs_to_campaign`; produces `MainWindow._add_jobs_to_selected_campaign()` and `MainWindow.sogni_queue_table`.

- [x] **Step 1: Write the failing test**

Create one campaign, select it, set a one-frame/one-prompt input and invoke the desired UI API:

```python
before_count = len(repo.list_campaigns())
window._add_jobs_to_selected_campaign()
assert len(repo.list_campaigns()) == before_count
assert window.sogni_queue_table.rowCount() == 2
assert window.sogni_queue_table.item(0, 4).text() == "Pending"
assert window.campaign_queue_add_button.text() == "Agregar jobs a campaña seleccionada"
```

- [x] **Step 2: Verify RED**

Run `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest tests/test_ui_frame_preview.py::test_add_jobs_to_selected_campaign_keeps_campaign_count_and_updates_visible_sogni_queue -q`. Expected: FAIL because the method/table do not exist.

- [x] **Step 3: Implement the UI**

Change the button label and connect it to `_add_jobs_to_selected_campaign`. That method rejects a missing selection with `QMessageBox.warning`, calls the manager with `Path(self.frames_edit.text())` and `Path(self.prompts_edit.text())`, then calls `_refresh_tables`. Add a `QGroupBox("Cola Sogni")` with a read-only table headed `#, Frame, Prompt, Modelo, Estado, Intentos, Acción`. Extract the current Jobs row rendering into one helper that fills both tables and creates `Reintentar` only for FAILED jobs.

- [x] **Step 4: Verify GREEN and commit**

Run the Step 2 test plus `test_campaign_panel_exposes_queue_then_sequential_start_actions`; expected PASS. Commit with `git add app/ui/main_window.py tests/test_ui_frame_preview.py; git commit -m "feat: show Sogni campaign queue"`.

### Task 3: Validate integrated behavior

**Files:** Verify `app/core/campaign_manager.py`, `app/ui/main_window.py`, and their tests.

- [x] **Step 1: Run target regression**

Run `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest tests/test_core_behaviors.py tests/test_ui_frame_preview.py -q` with `QT_QPA_PLATFORM=offscreen`. Expected: PASS.

- [x] **Step 2: Run full verification**

Run `& 'E:\Descargas\TODO\Proyectos codigo\AutoSogni\.venv\Scripts\python.exe' -m pytest -q` with `QT_QPA_PLATFORM=offscreen`. Result: **100 passed**.

- [x] **Step 3: Inspect delivery**

Run `git diff origin/master...HEAD --check`, `git status --short`, and `git log --oneline origin/master..HEAD`. Expected: no whitespace errors and only design, plan, code, and tests in this branch.
