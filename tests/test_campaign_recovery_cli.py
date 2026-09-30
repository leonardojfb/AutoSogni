import json
from pathlib import Path

from app.database.db import Database
from scripts.recover_campaign import main


def _run_folder(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_dir = tmp_path / "old-run"
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True)
    (frames_dir / "outfit_01_pink_dress.png").write_bytes(b"frame")
    (run_dir / "prompts.json").write_text(
        json.dumps([{"id": "P01", "name": "One", "text": "Literal prompt"}]),
        encoding="utf-8",
    )
    return run_dir, frames_dir, run_dir / "prompts.json"


def _args(run_dir: Path, frames_dir: Path, prompts_file: Path, database_path: Path) -> list[str]:
    return [
        "--run-folder", str(run_dir),
        "--frames-folder", str(frames_dir),
        "--prompts-file", str(prompts_file),
        "--output-folder", str(run_dir),
        "--name", "Recovered campaign",
        "--model-id", "wan22",
        "--model-name", "WAN 2.2",
        "--settings-json", '{"duration": 5}',
        "--template", "{outfit}__{prompt_id}_{prompt_name}.mp4",
        "--organization", "flat",
        "--database", str(database_path),
    ]


def test_cli_defaults_to_preview_and_does_not_create_database(tmp_path: Path, capsys):
    run_dir, frames_dir, prompts_file = _run_folder(tmp_path)
    database_path = tmp_path / "app.db"

    result = main(_args(run_dir, frames_dir, prompts_file, database_path))

    assert result == 0
    assert not database_path.exists()
    output = capsys.readouterr().out
    assert "Total jobs: 1" in output
    assert "Completed matches: 0" in output
    assert "Pending jobs: 1" in output


def test_interactive_cancel_leaves_database_unchanged(tmp_path: Path, monkeypatch, capsys):
    run_dir, frames_dir, prompts_file = _run_folder(tmp_path)
    database_path = tmp_path / "app.db"
    db = Database(database_path)
    db.initialize()
    before = database_path.read_bytes()
    answers = iter([
        str(run_dir), "", "", "", "", "wan22", "WAN 2.2", '{"duration": 5}', "", "", str(database_path), "n",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    result = main([])

    assert result == 0
    assert database_path.read_bytes() == before
    assert "Cancelled" in capsys.readouterr().out


def test_cli_apply_creates_campaign_and_reports_id(tmp_path: Path, capsys):
    run_dir, frames_dir, prompts_file = _run_folder(tmp_path)
    database_path = tmp_path / "app.db"

    result = main(_args(run_dir, frames_dir, prompts_file, database_path) + ["--apply"])

    assert result == 0
    db = Database(database_path)
    with db.connect() as conn:
        campaign = conn.execute("SELECT id, name FROM campaigns").fetchone()
    assert campaign["name"] == "Recovered campaign"
    assert f"Campaign ID: {campaign['id']}" in capsys.readouterr().out


def test_cli_rejects_invalid_settings_before_creating_database(tmp_path: Path, capsys):
    run_dir, frames_dir, prompts_file = _run_folder(tmp_path)
    database_path = tmp_path / "app.db"
    args = _args(run_dir, frames_dir, prompts_file, database_path)
    args[args.index("--settings-json") + 1] = "not-json"
    args.append("--apply")

    result = main(args)

    assert result == 1
    assert not database_path.exists()
    assert "Generation settings" in capsys.readouterr().err


def test_cli_accepts_settings_from_json_file(tmp_path: Path, capsys):
    run_dir, frames_dir, prompts_file = _run_folder(tmp_path)
    database_path = tmp_path / "app.db"
    settings_file = tmp_path / "generation-settings.json"
    settings_file.write_text('{"duration": 7, "aspectRatio": "9:16"}', encoding="utf-8")
    args = _args(run_dir, frames_dir, prompts_file, database_path)
    settings_index = args.index("--settings-json")
    del args[settings_index:settings_index + 2]
    args.extend(["--settings-file", str(settings_file), "--apply"])

    result = main(args)

    assert result == 0
    db = Database(database_path)
    with db.connect() as conn:
        settings = conn.execute("SELECT settings_json FROM campaigns").fetchone()[0]
    assert json.loads(settings) == {"duration": 7, "aspectRatio": "9:16"}
