# Recover an AutoSogni campaign from its execution folder

## Goal

Recreate a campaign in AutoSogni after its local database was lost, using the
original frame files, literal prompt source, and generated videos from the
execution folder. This supports inspecting the campaign and resuming jobs whose
outputs are missing.

## Evidence and limits

- The application database stores campaign settings, frames, prompts, jobs,
  output paths, and statuses.
- The application has a campaign manifest exporter, but the current code does
  not call it automatically. A recovery flow therefore cannot rely on a
  manifest being present.
- Output filenames may identify a frame/prompt pair when they follow the
  selected filename template. The recoverer must leave uncertain matches
  unmatched instead of marking the wrong job complete.
- Model ID and generation settings cannot be inferred reliably from MP4 files.
  The user must provide them unless a valid manifest or recovery metadata file
  supplies them.

## User flow

1. The user launches a Windows recovery utility and chooses the execution
   folder. The folder must contain or point to the generated videos, original
   frames, and prompt source (`.json`, `.csv`, or `.txt`).
2. The utility scans without writing to the database or changing source files.
   If any input folder or file is ambiguous, it asks the user to select it.
3. The utility reads prompt text literally and asks for the campaign name, model
   ID/name, generation settings, organization mode, and filename template when
   those values cannot be read from metadata.
4. It maps only unambiguous video-to-frame/prompt matches. Matched outputs
   become `DONE`; all other matrix jobs become `PENDING`.
5. It displays the proposed campaign, match counts, unmatched videos, and
   pending jobs. No persistent change occurs until the user confirms.
6. On confirmation, it backs up the target SQLite database, then inserts the
   campaign, frames, prompts, and full Frame × Prompt job matrix in one
   transaction. It records matched output paths and completion timestamps.
7. The utility reports the database path and recovered campaign ID so the user
   can verify it in AutoSogni.

## Database selection

Use an explicit `--database` override when supplied. Otherwise prefer the
existing packaged-app database under `%LOCALAPPDATA%\SogniVideoAutomator\data`;
if it does not exist, use the source checkout's `data\app.db` when available;
otherwise initialize the packaged-app path. This lets the utility share state
with both a source run and the corrected packaged application.

## Deliverables

- A Python recovery script in `scripts/` that can also run from a terminal.
- A small Windows launcher for source users.
- A separately built console executable in `dist/` for users who only have the
  packaged application and no Python installation.
- Focused tests for prompt preservation, deterministic output matching,
  ambiguous/missing files, dry-run behavior, transactional recovery, database
  backup, and duplicate protection.

## Safety and failure behavior

- Scanning is read-only. Applying recovery requires an explicit confirmation.
- The utility never overwrites or deletes source media, prompts, outputs, or an
  existing campaign.
- A SQLite backup is created before the recovery transaction.
- Input validation or mapping errors abort before writing. A database error
  rolls back the transaction and leaves the backup available.
- Ambiguous filename matches are reported and left pending; the utility does
  not guess.
- The utility does not start generation after recovery.

## Acceptance criteria

- A complete source/output folder produces a new campaign with literal prompts
  and one job per frame/prompt pair.
- Only uniquely matched existing videos are marked `DONE` and linked to jobs.
- Unmatched combinations remain `PENDING` and can be resumed from AutoSogni.
- The dry run produces a clear report and causes no database changes.
- Applying recovery creates a backup and a single complete campaign transaction.
- The standalone Windows executable starts and uses the same persistent
  database path as the packaged application.
