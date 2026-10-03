from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    try:
        settings = json.loads(campaign.settings_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Generation settings must be valid JSON.") from exc
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild an AutoSogni campaign from its original frames, prompts, and generated videos."
    )
    parser.add_argument("--run-folder", type=Path)
    parser.add_argument("--frames-folder", type=Path)
    parser.add_argument("--prompts-file", type=Path)
    parser.add_argument("--output-folder", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--model-id")
    parser.add_argument("--model-name")
    parser.add_argument("--settings-json")
    parser.add_argument("--settings-file", type=Path, help="Read generation settings from a UTF-8 JSON file.")
    parser.add_argument("--template", default="{outfit}__{prompt_id}_{prompt_name}.mp4")
    parser.add_argument("--organization", choices=("by_outfit", "flat"), default="by_outfit")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write the recovery after showing its preview.")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if not argv:
            return _run_interactive()
        required = (
            "run_folder", "frames_folder", "prompts_file", "output_folder", "name", "model_id", "model_name",
        )
        missing = [name.replace("_", "-") for name in required if getattr(args, name) is None]
        if args.settings_json is None and args.settings_file is None:
            missing.append("settings-json or settings-file")
        if args.settings_json is not None and args.settings_file is not None:
            parser.error("Use either --settings-json or --settings-file, not both.")
        if missing:
            parser.error(f"Missing required options: {', '.join('--' + name for name in missing)}")
        settings_json = (
            args.settings_file.read_text(encoding="utf-8-sig")
            if args.settings_file is not None else args.settings_json
        )
        return _run_recovery(
            run_dir=args.run_folder,
            frames_dir=args.frames_folder,
            prompts_file=args.prompts_file,
            output_dir=args.output_folder,
            name=args.name,
            model_id=args.model_id,
            model_name=args.model_name,
            settings_json=settings_json,
            filename_template=args.template,
            organization_mode=args.organization,
            database_override=args.database,
            apply=args.apply,
            confirm=False,
        )
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"Recovery stopped: {exc}", file=sys.stderr)
        return 1


def _run_interactive() -> int:
    print("AutoSogni campaign recovery (scan first; nothing is saved until you confirm).")
    run_dir = _ask_path("Execution folder", required=True)
    default_frames = run_dir / "frames"
    frame_candidates = [
        path for path in run_dir.rglob("*")
        if path.is_dir() and any(child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS for child in path.iterdir())
    ]
    frames_dir = _choose_path("Frames folder", default_frames if default_frames.is_dir() else None, frame_candidates)
    prompt_candidates = [
        path for path in run_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".csv", ".txt"}
        and "prompt" in path.stem.casefold()
    ]
    prompts_file = _choose_path("Prompt source file", None, prompt_candidates)
    output_default = run_dir / "output"
    output_dir = _ask_path("Output folder", required=True, default=output_default if output_default.is_dir() else run_dir)
    name = input(f"Campaign name [{run_dir.name}]: ").strip() or run_dir.name
    model_id = _ask_text("Model ID")
    model_name = _ask_text("Model name")
    settings_json = _ask_text("Original generation settings as JSON object")
    filename_template = input("Output filename template [{outfit}__{prompt_id}_{prompt_name}.mp4]: ").strip()
    filename_template = filename_template or "{outfit}__{prompt_id}_{prompt_name}.mp4"
    organization_mode = input("Organization mode [by_outfit/flat] (default by_outfit): ").strip() or "by_outfit"
    if organization_mode not in {"by_outfit", "flat"}:
        raise ValueError("Organization mode must be 'by_outfit' or 'flat'.")
    database_answer = input(f"Database path [{resolve_database_path()}]: ").strip()
    database_override = Path(database_answer) if database_answer else None
    return _run_recovery(
        run_dir, frames_dir, prompts_file, output_dir, name, model_id, model_name,
        settings_json, filename_template, organization_mode, database_override,
        apply=False, confirm=True,
    )


def _ask_path(label: str, required: bool, default: Path | None = None) -> Path:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip().strip('"')
        if value:
            return Path(value).expanduser().resolve()
        if default:
            return default.resolve()
        if not required:
            return Path()
        print(f"{label} is required.")


def _choose_path(label: str, default: Path | None, candidates: list[Path]) -> Path:
    if default is not None:
        return default.resolve()
    unique = sorted(set(candidates), key=lambda path: str(path).casefold())
    if len(unique) == 1:
        print(f"{label}: {unique[0]}")
        return unique[0].resolve()
    if unique:
        print(f"Found multiple candidates for {label}:")
        for candidate in unique:
            print(f"  {candidate}")
    return _ask_path(label, required=True)


def _ask_text(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print(f"{label} is required.")


def _run_recovery(
    run_dir: Path,
    frames_dir: Path,
    prompts_file: Path,
    output_dir: Path,
    name: str,
    model_id: str,
    model_name: str,
    settings_json: str,
    filename_template: str,
    organization_mode: str,
    database_override: Path | None,
    apply: bool,
    confirm: bool,
) -> int:
    print("Close AutoSogni before applying recovery to avoid concurrent database changes.")
    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)
    matches = match_outputs(scan, filename_template, organization_mode, name, model_name)
    campaign = RecoveryCampaignInput(
        name, model_id, model_name, frames_dir, prompts_file, output_dir,
        filename_template, organization_mode, settings_json,
    )
    preview = preview_recovery(scan, matches, campaign)
    database_path = resolve_database_path(database_override)
    print(f"\nCampaign: {campaign.name}")
    print(f"Model: {campaign.model_name} ({campaign.model_id})")
    print(f"Frames: {len(scan.frames)} | Prompts: {len(scan.prompts)}")
    print(f"Total jobs: {preview.total_jobs}")
    print(f"Completed matches: {preview.completed_jobs}")
    print(f"Pending jobs: {preview.pending_jobs}")
    print(f"Ambiguous jobs: {len(matches.ambiguous_jobs)}")
    print(f"Unmatched videos: {len(matches.unmatched_outputs)}")
    print(f"Database: {database_path}")
    if matches.ambiguous_jobs:
        for (frame_index, prompt_index), candidates in matches.ambiguous_jobs.items():
            print(f"  Ambiguous frame {frame_index + 1} / prompt {prompt_index + 1}: {len(candidates)} videos")
    if matches.unmatched_outputs:
        for path in matches.unmatched_outputs[:10]:
            print(f"  Unmatched video: {path}")
        if len(matches.unmatched_outputs) > 10:
            print(f"  ... and {len(matches.unmatched_outputs) - 10} more")

    if confirm:
        answer = input("Type RECUPERAR to create this campaign; anything else cancels: ").strip()
        if answer != "RECUPERAR":
            print("Cancelled. No database changes were made.")
            return 0
    elif not apply:
        print("Preview only. Re-run with --apply to save this campaign.")
        return 0

    result = apply_recovery(database_path, preview)
    print(f"Campaign ID: {result.campaign_id}")
    print(f"Saved jobs: {result.completed_jobs} DONE, {result.pending_jobs} PENDING")
    if result.backup_path:
        print(f"Database backup: {result.backup_path}")
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
