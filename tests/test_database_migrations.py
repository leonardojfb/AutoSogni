import sqlite3
from pathlib import Path

from app.database.db import DB_VERSION, Database
from app.database.repositories import CampaignRepository


def test_new_database_contains_rendered_prompt_column(tmp_path: Path):
    db = Database(tmp_path / "new.db")

    db.initialize()

    with db.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert "rendered_prompt" in columns
    assert version == DB_VERSION


def test_legacy_database_migrates_without_changing_existing_job(tmp_path: Path):
    path = tmp_path / "legacy.db"
    _create_legacy_database(path)
    db = Database(path)

    with db.connect() as conn:
        before = tuple(conn.execute("SELECT campaign_id, frame_id, prompt_id, order_index, status, workflow_id, idempotency_key, artifact_url, output_file, attempt_count, last_error FROM jobs").fetchone())

    db.initialize()

    with db.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        after = tuple(conn.execute("SELECT campaign_id, frame_id, prompt_id, order_index, status, workflow_id, idempotency_key, artifact_url, output_file, attempt_count, last_error FROM jobs").fetchone())

    assert "rendered_prompt" in columns
    assert after == before


def test_migrating_twice_is_idempotent_and_save_rendered_prompt_works(tmp_path: Path):
    path = tmp_path / "legacy.db"
    _create_legacy_database(path)
    db = Database(path)
    db.initialize()
    db.initialize()

    repo = CampaignRepository(db)
    repo.save_rendered_prompt(1, "adapted H3 prompt")

    with db.connect() as conn:
        row = conn.execute("SELECT status, workflow_id, rendered_prompt FROM jobs WHERE id = 1").fetchone()

    assert row["status"] == "QUEUED"
    assert row["workflow_id"] == "workflow-1"
    assert row["rendered_prompt"] == "adapted H3 prompt"


def _create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                frames_folder TEXT NOT NULL,
                prompts_source TEXT NOT NULL,
                output_folder TEXT NOT NULL,
                filename_template TEXT NOT NULL,
                organization_mode TEXT NOT NULL,
                concurrency INTEGER NOT NULL DEFAULT 1,
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                outfit_name TEXT NOT NULL,
                sha256 TEXT NOT NULL
            );
            CREATE TABLE prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                prompt_code TEXT NOT NULL,
                prompt_name TEXT NOT NULL,
                prompt_text TEXT NOT NULL
            );
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                frame_id INTEGER NOT NULL,
                prompt_id INTEGER NOT NULL,
                order_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                workflow_id TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                artifact_url TEXT,
                output_file TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                remote_completed_at TEXT,
                downloaded_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO campaigns (name, status, model_id, model_name, frames_folder, prompts_source, output_folder, filename_template, organization_mode)
            VALUES ('Legacy', 'RUNNING', 'model', 'Model', '', '', '', '', 'flat');
            INSERT INTO frames (campaign_id, file_path, filename, outfit_name, sha256)
            VALUES (1, 'frame.png', 'frame.png', 'Outfit', 'hash');
            INSERT INTO prompts (campaign_id, prompt_code, prompt_name, prompt_text)
            VALUES (1, 'P01', 'Prompt', 'Original prompt');
            INSERT INTO jobs (campaign_id, frame_id, prompt_id, order_index, status, workflow_id, idempotency_key, attempt_count)
            VALUES (1, 1, 1, 1, 'QUEUED', 'workflow-1', 'legacy-key', 2);
            """
        )