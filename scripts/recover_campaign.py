from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.database.db import Database
from app.core.filename_builder import FilenameBuilder, FilenameContext, sanitize_path_part
from app.core.prompt_importer import ImportedPrompt, import_prompts
from app.core.validators import IMAGE_EXTENSIONS, default_outfit_name
from app.utils.hashing import sha256_file
from app.utils.paths import APP_NAME, project_root
from app.utils.time import utc_now_iso


@dataclass(frozen=True)
class ScanInputs:
    run_dir: Path
    frames_dir: Path
    prompts_file: Path
    output_dir: Path
    frames: tuple[Path, ...]
    prompts: tuple[ImportedPrompt, ...]
    outputs: tuple[Path, ...]


@dataclass(frozen=True)
class RecoveryMatch:
    matches: dict[tuple[int, int], Path]
    ambiguous_jobs: dict[tuple[int, int], tuple[Path, ...]]
    unmatched_outputs: tuple[Path, ...]


@dataclass(frozen=True)
class RecoveryCampaignInput:
    name: str
    model_id: str
    model_name: str
    frames_folder: Path
    prompts_file: Path
    output_folder: Path
    filename_template: str
    organization_mode: str
    settings_json: str


@dataclass(frozen=True)
class RecoveryPreview:
    scan: ScanInputs
    match: RecoveryMatch
    campaign: RecoveryCampaignInput
    total_jobs: int
    completed_jobs: int
    pending_jobs: int


@dataclass(frozen=True)
class RecoveryResult:
    campaign_id: int
    completed_jobs: int
    pending_jobs: int
    backup_path: Path | None


def resolve_database_path(override: Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    packaged_db = local_app_data / APP_NAME / "data" / "app.db"
    source_db = project_root() / "data" / "app.db"
    if packaged_db.is_file():
        return packaged_db
    if source_db.is_file():
        return source_db
    return packaged_db if getattr(sys, "frozen", False) or "LOCALAPPDATA" in os.environ else source_db


def preview_recovery(
    scan: ScanInputs,
    match: RecoveryMatch,
    campaign: RecoveryCampaignInput,
) -> RecoveryPreview:
    if not campaign.name.strip():
        raise ValueError("Campaign name is required.")
    if not campaign.model_id.strip() or not campaign.model_name.strip():
        raise ValueError("Model ID and model name are required.")
    if not campaign.filename_template.strip():
        raise ValueError("Filename template is required.")
    if campaign.organization_mode not in {"flat", "by_outfit"}:
        raise ValueError("Organization mode must be 'flat' or 'by_outfit'.")
    settings = json.loads(campaign.settings_json)
    if not isinstance(settings, dict):
        raise ValueError("Generation settings must be a JSON object.")
    total = len(scan.frames) * len(scan.prompts)
    completed = len(match.matches)
    return RecoveryPreview(scan, match, campaign, total, completed, total - completed)


def apply_recovery(database_path: Path, preview: RecoveryPreview) -> RecoveryResult:
    database_path = Path(database_path).expanduser().resolve()
    backup_path = _backup_database(database_path)
    db = Database(database_path)
    db.initialize()
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _reject_duplicate_campaign(conn, preview.campaign)
        campaign = preview.campaign
        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO campaigns (
                name, status, model_id, model_name, frames_folder, prompts_source,
                output_folder, filename_template, organization_mode, concurrency,
                settings_json, created_at, updated_at
            ) VALUES (?, 'READY', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                campaign.name.strip(), campaign.model_id.strip(), campaign.model_name.strip(),
                str(campaign.frames_folder.resolve()), str(campaign.prompts_file.resolve()),
                str(campaign.output_folder.resolve()), campaign.filename_template,
                campaign.organization_mode, campaign.settings_json, now, now,
            ),
        )
        campaign_id = int(cursor.lastrowid)
        frame_ids: list[int] = []
        for frame_path in preview.scan.frames:
            cursor = conn.execute(
                """
                INSERT INTO frames (campaign_id, file_path, filename, outfit_name, sha256)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    campaign_id, str(frame_path.resolve()), frame_path.name,
                    default_outfit_name(frame_path.name), sha256_file(frame_path),
                ),
            )
            frame_ids.append(int(cursor.lastrowid))

        prompt_ids: list[int] = []
        for prompt in preview.scan.prompts:
            cursor = conn.execute(
                """
                INSERT INTO prompts (campaign_id, prompt_code, prompt_name, prompt_text)
                VALUES (?, ?, ?, ?)
                """,
                (campaign_id, prompt.prompt_code, prompt.prompt_name, prompt.prompt_text),
            )
            prompt_ids.append(int(cursor.lastrowid))

        completed = pending = 0
        for frame_index, frame_id in enumerate(frame_ids):
            for prompt_index, prompt_id in enumerate(prompt_ids):
                order_index = frame_index * len(prompt_ids) + prompt_index + 1
                output_file = preview.match.matches.get((frame_index, prompt_index))
                status = "DONE" if output_file else "PENDING"
                completed += status == "DONE"
                pending += status == "PENDING"
                conn.execute(
                    """
                    INSERT INTO jobs (
                        campaign_id, frame_id, prompt_id, order_index, status,
                        idempotency_key, output_file, downloaded_at, completed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign_id, frame_id, prompt_id, order_index, status,
                        f"recover:{campaign_id}:{order_index:04d}",
                        str(output_file.resolve()) if output_file else None,
                        now if output_file else None,
                        now if output_file else None,
                        now,
                    ),
                )
        conn.commit()
        return RecoveryResult(campaign_id, completed, pending, backup_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _backup_database(database_path: Path) -> Path | None:
    if not database_path.is_file() or database_path.stat().st_size == 0:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(f"{database_path.stem}.before-recovery-{stamp}.bak")
    source = sqlite3.connect(database_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    except Exception:
        backup.close()
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    backup.close()
    return backup_path


def _reject_duplicate_campaign(conn: sqlite3.Connection, campaign: RecoveryCampaignInput) -> None:
    identity = (
        campaign.name.strip().casefold(),
        str(campaign.frames_folder.resolve()).casefold(),
        str(campaign.prompts_file.resolve()).casefold(),
        str(campaign.output_folder.resolve()).casefold(),
    )
    rows = conn.execute(
        "SELECT name, frames_folder, prompts_source, output_folder FROM campaigns"
    ).fetchall()
    for row in rows:
        existing = (
            str(row["name"]).casefold(),
            str(Path(row["frames_folder"]).resolve()).casefold(),
            str(Path(row["prompts_source"]).resolve()).casefold(),
            str(Path(row["output_folder"]).resolve()).casefold(),
        )
        if existing == identity:
            raise ValueError("An identical campaign already exists in the selected database.")




def scan_inputs(run_dir: Path, frames_dir: Path, prompts_file: Path, output_dir: Path) -> ScanInputs:
    run_dir = Path(run_dir).expanduser().resolve()
    frames_dir = Path(frames_dir).expanduser().resolve()
    prompts_file = Path(prompts_file).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    if not run_dir.is_dir():
        raise ValueError(f"Execution folder does not exist: {run_dir}")
    if not frames_dir.is_dir():
        raise ValueError(f"Frames folder does not exist: {frames_dir}")
    if not prompts_file.is_file():
        raise ValueError(f"Prompt file does not exist: {prompts_file}")
    if not output_dir.is_dir():
        raise ValueError(f"Output folder does not exist: {output_dir}")

    frames = tuple(sorted(
        (path for path in frames_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    ))
    if not frames:
        raise ValueError(f"Frames folder contains no supported images: {frames_dir}")
    prompts = tuple(import_prompts(prompts_file))
    if not prompts:
        raise ValueError(f"Prompt file contains no prompts: {prompts_file}")
    outputs = tuple(sorted(
        (path for path in output_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".mp4"),
        key=lambda path: path.relative_to(output_dir).as_posix().casefold(),
    ))
    return ScanInputs(run_dir, frames_dir, prompts_file, output_dir, frames, prompts, outputs)


def match_outputs(
    scan: ScanInputs,
    filename_template: str,
    organization_mode: str,
    campaign_name: str,
    model_name: str,
) -> RecoveryMatch:
    if organization_mode not in {"flat", "by_outfit"}:
        raise ValueError("Organization mode must be 'flat' or 'by_outfit'.")

    outputs_by_relative_path = {
        _relative_key(path.relative_to(scan.output_dir)): path for path in scan.outputs
    }
    outputs_by_parent: dict[str, list[Path]] = {}
    for path in scan.outputs:
        parent_key = _relative_key(path.parent.relative_to(scan.output_dir))
        outputs_by_parent.setdefault(parent_key, []).append(path)

    candidates_by_job: dict[tuple[int, int], tuple[Path, ...]] = {}
    for frame_index, frame_path in enumerate(scan.frames):
        outfit = default_outfit_name(frame_path.name)
        for prompt_index, prompt in enumerate(scan.prompts):
            order_index = frame_index * len(scan.prompts) + prompt_index + 1
            filename = FilenameBuilder().preview(filename_template, FilenameContext(
                campaign=campaign_name,
                job_number=order_index,
                outfit=outfit,
                frame_name=frame_path.name,
                prompt_id=prompt.prompt_code,
                prompt_name=prompt.prompt_name,
                model=model_name,
            ))
            relative_dir = Path(sanitize_path_part(outfit)) if organization_mode == "by_outfit" else Path()
            expected_path = relative_dir / filename
            expected_key = _relative_key(expected_path)
            exact_path = outputs_by_relative_path.get(expected_key)
            candidate_paths = [exact_path] if exact_path else []

            expected_stem = Path(filename).stem
            expected_suffix = Path(filename).suffix.casefold()
            collision_re = re.compile(rf"^{re.escape(expected_stem)}__\d+{re.escape(expected_suffix)}$", re.IGNORECASE)
            for candidate in outputs_by_parent.get(_relative_key(relative_dir), []):
                if candidate == exact_path or candidate.suffix.casefold() != expected_suffix:
                    continue
                if collision_re.fullmatch(candidate.name):
                    candidate_paths.append(candidate)
            candidates_by_job[(frame_index, prompt_index)] = tuple(sorted(
                set(candidate_paths), key=lambda path: path.as_posix().casefold()
            ))

    claiming_jobs: dict[Path, list[tuple[int, int]]] = {}
    for job, paths in candidates_by_job.items():
        for path in paths:
            claiming_jobs.setdefault(path, []).append(job)

    matches: dict[tuple[int, int], Path] = {}
    ambiguous_jobs: dict[tuple[int, int], tuple[Path, ...]] = {}
    assigned_paths: set[Path] = set()
    for job, paths in candidates_by_job.items():
        if len(paths) == 1 and len(claiming_jobs[paths[0]]) == 1:
            matches[job] = paths[0]
            assigned_paths.add(paths[0])
        elif paths:
            ambiguous_jobs[job] = paths
            assigned_paths.update(paths)

    unmatched = tuple(path for path in scan.outputs if path not in assigned_paths)
    return RecoveryMatch(matches, ambiguous_jobs, unmatched)


def _relative_key(path: Path) -> str:
    return path.as_posix().strip("./").casefold()
