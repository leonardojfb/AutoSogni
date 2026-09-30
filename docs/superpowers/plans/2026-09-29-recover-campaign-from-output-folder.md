# Recover an AutoSogni Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a Windows utility that reconstructs a campaign from its original frames, literal prompt source, and generated outputs, then saves it to the database AutoSogni uses.

**Architecture:** Keep the recovery logic in `scripts/recover_campaign.py`, with pure scan/match functions separated from database writes. Make read-only scanning the default, require explicit confirmation to apply, back up SQLite before a single transaction, and package the same script as a standalone console EXE.

**Tech Stack:** Python 3.14, stdlib `sqlite3`/`argparse`/`pathlib`, existing AutoSogni database/importer/filename helpers, pytest, and PyInstaller.

## Global Constraints

- Preserve prompt text exactly as returned by `import_prompts()`; never rewrite or merge prompts.
- Never modify or delete the source frames, prompts, or generated videos.
- Only mark a job `DONE` when one unique output file matches its frame/prompt using the selected filename template and organization mode.
- Leave unmatched jobs `PENDING` and ambiguous output matches unassigned.
- Do not start generation as part of recovery.
- Create a SQLite backup before applying changes and insert a complete campaign in one transaction.
- Preserve existing uncommitted `app/utils/paths.py`, `tests/test_paths.py`, `data/`, and `errorlogs.txt` changes; stage only files named in each task.
- Run tests with `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest ...` in PowerShell.

---

### Task 1: Deterministic scan and output matching

**Files:**
- Create: `scripts/recover_campaign.py`
- Test: `tests/test_campaign_recovery.py`
- Reuse: `app/core/prompt_importer.py`, `app/core/filename_builder.py`, `app/core/validators.py`

**Interfaces:**
- `scan_inputs(run_dir: Path, frames_dir: Path, prompts_file: Path, output_dir: Path) -> ScanInputs`
- `match_outputs(scan: ScanInputs, filename_template: str, organization_mode: str, campaign_name: str, model_name: str) -> RecoveryMatch`
- `ScanInputs` carries sorted frame paths, `ImportedPrompt` values, and recursively found MP4 paths.
- `RecoveryMatch` carries one optional output path per ordered frame/prompt pair, plus unmatched and ambiguous files.

- [x] **Step 1: Write failing tests for literal prompt loading and stable input discovery.**

Test JSON/CSV/TXT prompt input through `import_prompts()`, frame extension filtering, recursive MP4 discovery, stable ordering, and an error for missing/empty inputs. Include prompt text with newlines and punctuation and assert byte-for-byte string equality after import.

- [x] **Step 2: Run the new tests and verify they fail because recovery functions do not exist.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py -q`

Expected: collection or import failure for the not-yet-created recovery interfaces.

- [x] **Step 3: Write failing tests for unique, missing, and ambiguous output matches.**

Cover the default template `{outfit}__{prompt_id}_{prompt_name}.mp4`, `by_outfit`, `flat`, case-insensitive Windows suffixes, collision suffixes such as `__02`, and two files that both appear to match one job. Assert only a unique file is assigned; missing jobs and ambiguous matches remain unassigned.

- [x] **Step 4: Run the matching tests and confirm the expected failures.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py -q`

Expected: matching tests fail because `match_outputs()` is not implemented.

- [x] **Step 5: Implement the scan/match dataclasses and pure functions.**

Read names and prompt values from existing importer/validator helpers. Use the existing `FilenameBuilder` rendering convention and outfit-name helper. Keep all filesystem traversal read-only. Never choose between multiple candidates; record those as ambiguous.

- [x] **Step 6: Run Task 1 tests and commit the tested scanner.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py -q`

Expected: all scan and matching tests pass.

```powershell
git add -- scripts/recover_campaign.py tests/test_campaign_recovery.py
git commit -m "feat: scan campaign recovery inputs"
```

### Task 2: Safe transactional database recovery

**Files:**
- Modify: `scripts/recover_campaign.py`
- Test: `tests/test_campaign_recovery.py`
- Reuse: `app/database/db.py`, `app/utils/paths.py`, `app/utils/hashing.py`

**Interfaces:**
- `resolve_database_path(override: Path | None) -> Path` prefers an explicit override, then an existing packaged `%LOCALAPPDATA%` database, then the source database; if neither exists, use the packaged persistent path.
- `preview_recovery(scan: ScanInputs, match: RecoveryMatch, campaign: RecoveryCampaignInput) -> RecoveryPreview` computes counts without connecting for writes.
- `apply_recovery(database_path: Path, preview: RecoveryPreview) -> RecoveryResult` creates a backup and writes one campaign atomically.

- [x] **Step 1: Write failing tests for database-path selection and read-only preview.**

Test explicit override precedence, existing packaged DB precedence, source DB fallback, new packaged DB default, and that preview leaves the DB bytes and campaign count unchanged.

- [x] **Step 2: Run the tests and verify they fail because the path/preview functions do not exist.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py -q`

Expected: missing-interface failures.

- [x] **Step 3: Write failing tests for backup, rollback, duplicate protection, and recovered job states.**

Use a temporary SQLite database initialized with the app schema. Assert a confirmed recovery creates a byte-readable pre-recovery backup, inserts one campaign and its complete frame × prompt matrix, preserves each literal prompt, marks only uniquely matched files `DONE` with their physical path, leaves all other jobs `PENDING`, and returns the new campaign ID. Assert repeating the same recovery is rejected before writing. Inject a database constraint failure and assert the transaction leaves no partial campaign.

- [x] **Step 4: Run these tests and verify they fail before implementation.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py -q`

Expected: missing recovery transaction implementation.

- [x] **Step 5: Implement backup and atomic insertion.**

Before schema initialization or any mutation, create a timestamped backup using SQLite's backup API when the database already exists. Then initialize the schema, reject an identical campaign identity, and use `BEGIN IMMEDIATE` to insert campaign, frames with SHA-256, prompts, and ordered jobs. Use idempotency keys `recover:<campaign_id>:<order_index>`. Set `DONE`, `output_file`, `downloaded_at`, and `completed_at` only for unique matches. Roll back and raise a clear error on any failure.

- [x] **Step 6: Run database recovery tests and commit the tested transaction.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py -q`

Expected: scan, mapping, backup, duplicate, and transaction tests pass.

```powershell
git add -- scripts/recover_campaign.py tests/test_campaign_recovery.py
git commit -m "feat: restore campaigns from generated outputs"
```

### Task 3: Interactive Windows entry point and launcher

**Files:**
- Modify: `scripts/recover_campaign.py`
- Create: `RecuperarCampana.bat`
- Create: `tests/test_campaign_recovery_cli.py`
- Modify: `README.md`

**Interfaces:**
- `main(argv: list[str] | None = None) -> int` supports command-line overrides and an interactive no-argument flow.
- `--apply` is required for persistent writes; no-argument mode scans, prints the preview, and asks for the same explicit confirmation before applying.

- [x] **Step 1: Write failing CLI tests for no-write default, required campaign metadata, confirmation, and cancellation.**

Inject temporary frame/prompt/output paths and a temporary database. Assert invalid/missing model data exits without DB changes, a declined confirmation exits without a backup or campaign, and accepted confirmation prints the DB path and campaign ID.

- [x] **Step 2: Run CLI tests and verify expected failures.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery_cli.py -q`

Expected: missing CLI entry point or recovery prompts.

- [x] **Step 3: Implement the interactive prompts and command-line options.**

Allow selecting the execution folder, frame directory, prompt file, output directory, campaign/model names, model ID, settings JSON, organization mode, template, and database override. Auto-select only when a candidate is unique. Show the number of frames, prompts, total matrix jobs, matched videos, ambiguous videos, and pending jobs before asking for confirmation. Validate settings JSON before preview. Never launch the queue.

- [x] **Step 4: Add a Windows launcher and concise README usage.**

The `.bat` runs the repo virtual environment when available, otherwise `python`, forwards arguments, preserves the exit code, and pauses only for double-click use. README documents source execution, flags, input folder expectations, database selection, and the explicit apply behavior.

- [x] **Step 5: Run CLI and scanner tests, then commit the entry point.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py tests/test_campaign_recovery_cli.py -q`

Expected: all recovery and CLI tests pass.

```powershell
git add -- scripts/recover_campaign.py RecuperarCampana.bat tests/test_campaign_recovery_cli.py README.md
git commit -m "feat: add Windows campaign recovery launcher"
```

### Task 4: Standalone executable and end-to-end smoke test

**Files:**
- Create: `RecoveryUtility.spec`
- Build: `dist/RecuperarCampana.exe` (ignored build artifact)

- [x] **Step 1: Build a console one-file executable from the same script.**

Run: `& '.\.venv\Scripts\python.exe' -m PyInstaller --clean --noconfirm RecoveryUtility.spec`

Expected: `dist\RecuperarCampana.exe` exists and imports SQLite, prompt importer, hashing, and recovery modules without requiring Qt.

- [x] **Step 2: Run an isolated executable smoke test.**

Use a temporary `LOCALAPPDATA` and synthetic run folder with one frame, one literal prompt, and one matching MP4. Run the executable in preview mode, verify it reports one match without creating a database, then run with explicit apply/confirmation and verify the created DB has one `DONE` job and no pending job. Do not use the user's actual campaign folder or database for this smoke test.

- [x] **Step 3: Run the focused project gate and inspect the final diff.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_campaign_recovery.py tests/test_campaign_recovery_cli.py -q`; then `git diff --check`.

Expected: all focused tests pass, the smoke test reports the recovered ID, and only the named implementation files are staged.

- [x] **Step 4: Commit the build spec and documentation gate.**

```powershell
git add -- RecoveryUtility.spec
git commit -m "build: package campaign recovery utility"
```

Do not publish a GitHub release or push without an explicit release request.
