import json
from pathlib import Path

import pytest

from scripts.recover_campaign import match_outputs, scan_inputs


def _scan(tmp_path: Path, *, duplicate_output: bool = False):
    run_dir = tmp_path / "run"
    frames_dir = run_dir / "frames"
    output_dir = run_dir / "output"
    frames_dir.mkdir(parents=True)
    output_dir.mkdir()
    (frames_dir / "outfit_01_pink_dress.png").write_bytes(b"frame")
    prompts_file = run_dir / "prompts.json"
    literal_prompt = "Keep the exact subject.\nDialogue: \"Stay here!\""
    prompts_file.write_text(json.dumps([
        {"id": "P01", "name": "Quick Question", "text": literal_prompt},
    ]), encoding="utf-8")
    return run_dir, frames_dir, prompts_file, output_dir, literal_prompt


def test_scan_loads_literal_prompts_and_finds_inputs_recursively(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, literal_prompt = _scan(tmp_path)
    output = output_dir / "Pink_Dress" / "Pink_Dress__P01_Quick_Question.mp4"
    output.parent.mkdir()
    output.write_bytes(b"video")

    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)

    assert scan.frames == (frames_dir / "outfit_01_pink_dress.png",)
    assert scan.prompts[0].prompt_text == literal_prompt
    assert scan.outputs == (output,)


def test_scan_rejects_missing_required_inputs(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    (frames_dir / "outfit_01_pink_dress.png").unlink()

    with pytest.raises(ValueError, match="frame"):
        scan_inputs(run_dir, frames_dir, prompts_file, output_dir)


def test_matching_marks_only_unique_existing_output(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    output = output_dir / "Pink_Dress" / "Pink_Dress__P01_Quick_Question.mp4"
    output.parent.mkdir()
    output.write_bytes(b"video")
    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)

    match = match_outputs(
        scan, "{outfit}__{prompt_id}_{prompt_name}.mp4", "by_outfit", "Campaign", "Model"
    )

    assert match.matches == {(0, 0): output}
    assert match.ambiguous_jobs == {}
    assert match.unmatched_outputs == ()


def test_matching_does_not_guess_between_collision_outputs(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    first = output_dir / "Pink_Dress" / "Pink_Dress__P01_Quick_Question.mp4"
    second = output_dir / "Pink_Dress" / "Pink_Dress__P01_Quick_Question__02.mp4"
    first.parent.mkdir()
    first.write_bytes(b"video-one")
    second.write_bytes(b"video-two")
    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)

    match = match_outputs(
        scan, "{outfit}__{prompt_id}_{prompt_name}.mp4", "by_outfit", "Campaign", "Model"
    )

    assert match.matches == {}
    assert list(match.ambiguous_jobs) == [(0, 0)]
    assert match.unmatched_outputs == ()


def test_matching_supports_flat_layout_and_case_insensitive_mp4_suffix(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    output = output_dir / "Pink_Dress__P01_Quick_Question.MP4"
    output.write_bytes(b"video")
    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)

    match = match_outputs(
        scan, "{outfit}__{prompt_id}_{prompt_name}.mp4", "flat", "Campaign", "Model"
    )

    assert match.matches == {(0, 0): output}


def test_matching_marks_shared_filename_as_ambiguous_for_each_job(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    prompts_file.write_text(json.dumps([
        {"id": "P01", "name": "Quick Question", "text": "First"},
        {"id": "P01", "name": "Quick Question", "text": "Second"},
    ]), encoding="utf-8")
    output = output_dir / "Pink_Dress" / "Pink_Dress__P01_Quick_Question.mp4"
    output.parent.mkdir()
    output.write_bytes(b"video")
    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)

    match = match_outputs(
        scan, "{outfit}__{prompt_id}_{prompt_name}.mp4", "by_outfit", "Campaign", "Model"
    )

    assert match.matches == {}
    assert set(match.ambiguous_jobs) == {(0, 0), (0, 1)}
    assert match.unmatched_outputs == ()
