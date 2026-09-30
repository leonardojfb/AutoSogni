from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.filename_builder import FilenameBuilder, FilenameContext, sanitize_path_part
from app.core.prompt_importer import ImportedPrompt, import_prompts
from app.core.validators import IMAGE_EXTENSIONS, default_outfit_name


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
