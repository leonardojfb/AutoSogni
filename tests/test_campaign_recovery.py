import csv
import json
import sqlite3
from pathlib import Path

import pytest

from app.database.db import Database
from scripts.recover_campaign import match_outputs, scan_inputs
from scripts.recover_campaign import (
    RecoveryCampaignInput,
    apply_recovery,
    preview_recovery,
    resolve_database_path,
)


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


@pytest.mark.parametrize("suffix", [".csv", ".txt"])
def test_scan_uses_existing_csv_and_text_prompt_import_rules(tmp_path: Path, suffix: str):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    prompts_file.unlink()
    prompts_file = run_dir / f"prompts{suffix}"
    expected = "First prompt line.\nSecond line stays intact."
    if suffix == ".csv":
        with prompts_file.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("id", "name", "text"))
            writer.writeheader()
            writer.writerow({"id": "P01", "name": "Literal", "text": expected})
    else:
        prompts_file.write_text(expected, encoding="utf-8")

    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)

    assert scan.prompts[0].prompt_text == expected


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


def test_scan_orders_frames_and_outputs_deterministically(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    (frames_dir / "outfit_02_black_top.webp").write_bytes(b"second-frame")
    output_a = output_dir / "a.mp4"
    output_b = output_dir / "z.mp4"
    output_a.write_bytes(b"a")
    output_b.write_bytes(b"z")

    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)

    assert [path.name for path in scan.frames] == ["outfit_01_pink_dress.png", "outfit_02_black_top.webp"]
    assert [path.name for path in scan.outputs] == ["a.mp4", "z.mp4"]


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


def _prepared_recovery(tmp_path: Path):
    run_dir, frames_dir, prompts_file, output_dir, _ = _scan(tmp_path)
    output = output_dir / "Pink_Dress" / "Pink_Dress__P01_Quick_Question.mp4"
    output.parent.mkdir()
    output.write_bytes(b"completed-output")
    scan = scan_inputs(run_dir, frames_dir, prompts_file, output_dir)
    match = match_outputs(
        scan, "{outfit}__{prompt_id}_{prompt_name}.mp4", "by_outfit", "Recovered", "Model"
    )
    recovery_input = RecoveryCampaignInput(
        name="Recovered",
        model_id="wan22",
        model_name="WAN 2.2",
        frames_folder=frames_dir,
        prompts_file=prompts_file,
        output_folder=output_dir,
        filename_template="{outfit}__{prompt_id}_{prompt_name}.mp4",
        organization_mode="by_outfit",
        settings_json="{\"duration\": 5}",
    )
    return scan, match, recovery_input


def test_database_path_prefers_override_then_existing_packaged_db(tmp_path: Path, monkeypatch):
    local_app_data = tmp_path / "LocalAppData"
    packaged_db = local_app_data / "SogniVideoAutomator" / "data" / "app.db"
    packaged_db.parent.mkdir(parents=True)
    packaged_db.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    override = tmp_path / "chosen.db"

    assert resolve_database_path(override) == override.resolve()
    assert resolve_database_path() == packaged_db


def test_recovery_preview_is_read_only_and_counts_matched_and_pending_jobs(tmp_path: Path):
    scan, match, recovery_input = _prepared_recovery(tmp_path)
    database_path = tmp_path / "app.db"
    db = Database(database_path)
    db.initialize()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO campaigns (name,status,model_id,model_name,frames_folder,prompts_source,output_folder,filename_template,organization_mode) VALUES ('Existing','READY','wan22','WAN 2.2','','','','','flat')"
        )
    before = database_path.read_bytes()

    preview = preview_recovery(scan, match, recovery_input)

    assert preview.total_jobs == 1
    assert preview.completed_jobs == 1
    assert preview.pending_jobs == 0
    assert database_path.read_bytes() == before


def test_apply_recovery_backups_db_and_creates_matched_done_job(tmp_path: Path):
    scan, match, recovery_input = _prepared_recovery(tmp_path)
    database_path = tmp_path / "app.db"
    db = Database(database_path)
    db.initialize()
    with db.connect() as conn:
        conn.execute("CREATE TABLE preserved_marker (value TEXT)")
        conn.execute("INSERT INTO preserved_marker VALUES ('keep')")
    preview = preview_recovery(scan, match, recovery_input)

    result = apply_recovery(database_path, preview)

    assert result.completed_jobs == 1
    assert result.pending_jobs == 0
    assert result.backup_path is not None and result.backup_path.is_file()
    with sqlite3.connect(result.backup_path) as backup:
        assert backup.execute("SELECT value FROM preserved_marker").fetchone() == ("keep",)
    with db.connect() as conn:
        campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (result.campaign_id,)).fetchone()
        job = conn.execute("SELECT * FROM jobs WHERE campaign_id=?", (result.campaign_id,)).fetchone()
        prompt = conn.execute("SELECT prompt_text FROM prompts WHERE campaign_id=?", (result.campaign_id,)).fetchone()
    assert campaign["status"] == "READY"
    assert campaign["settings_json"] == recovery_input.settings_json
    assert job["status"] == "DONE"
    assert job["output_file"] == str(next(iter(match.matches.values())))
    assert prompt["prompt_text"] == scan.prompts[0].prompt_text


def test_backup_is_taken_before_schema_migration(tmp_path: Path):
    scan, match, recovery_input = _prepared_recovery(tmp_path)
    database_path = tmp_path / "app.db"
    db = Database(database_path)
    db.initialize()
    with db.connect() as conn:
        conn.execute("ALTER TABLE jobs DROP COLUMN rendered_prompt")
        conn.execute("PRAGMA user_version = 1")
    preview = preview_recovery(scan, match, recovery_input)

    result = apply_recovery(database_path, preview)

    assert result.backup_path is not None
    with sqlite3.connect(result.backup_path) as backup:
        legacy_columns = {row[1] for row in backup.execute("PRAGMA table_info(jobs)")}
        assert "rendered_prompt" not in legacy_columns
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
    with db.connect() as conn:
        current_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        assert "rendered_prompt" in current_columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_apply_recovery_rejects_an_identical_campaign(tmp_path: Path):
    scan, match, recovery_input = _prepared_recovery(tmp_path)
    database_path = tmp_path / "app.db"
    db = Database(database_path)
    db.initialize()
    preview = preview_recovery(scan, match, recovery_input)
    apply_recovery(database_path, preview)

    with pytest.raises(ValueError, match="identical campaign already exists"):
        apply_recovery(database_path, preview)

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 1


def test_recovery_failure_rolls_back_partial_campaign(tmp_path: Path, monkeypatch):
    scan, match, recovery_input = _prepared_recovery(tmp_path)
    database_path = tmp_path / "app.db"
    db = Database(database_path)
    db.initialize()
    preview = preview_recovery(scan, match, recovery_input)
    monkeypatch.setattr("scripts.recover_campaign.sha256_file", lambda _path: (_ for _ in ()).throw(OSError("hash failure")))

    with pytest.raises(OSError, match="hash failure"):
        apply_recovery(database_path, preview)

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 0
