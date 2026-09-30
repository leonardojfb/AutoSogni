from __future__ import annotations

import json
from typing import Any

from app.database.db import Database
from app.database.models import Campaign, Frame, Job, Prompt
from app.utils.time import utc_now_iso


class CampaignRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_campaign(self, data: dict[str, Any]) -> int:
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        with self.db.connect() as conn:
            cur = conn.execute(f"INSERT INTO campaigns ({columns}) VALUES ({placeholders})", tuple(data.values()))
            return int(cur.lastrowid)

    def insert_frame(self, campaign_id: int, file_path: str, filename: str, outfit_name: str, sha256: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO frames (campaign_id, file_path, filename, outfit_name, sha256) VALUES (?, ?, ?, ?, ?)",
                (campaign_id, file_path, filename, outfit_name, sha256),
            )
            return int(cur.lastrowid)

    def insert_prompt(self, campaign_id: int, prompt_code: str, prompt_name: str, prompt_text: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO prompts (campaign_id, prompt_code, prompt_name, prompt_text) VALUES (?, ?, ?, ?)",
                (campaign_id, prompt_code, prompt_name, prompt_text),
            )
            return int(cur.lastrowid)

    def insert_job(self, campaign_id: int, frame_id: int, prompt_id: int, order_index: int, idempotency_key: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (campaign_id, frame_id, prompt_id, order_index, status, idempotency_key)
                VALUES (?, ?, ?, ?, 'PENDING', ?)
                """,
                (campaign_id, frame_id, prompt_id, order_index, idempotency_key),
            )
            return int(cur.lastrowid)

    def get_campaign(self, campaign_id: int) -> Campaign:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row is None:
            raise ValueError(f"Campaign not found: {campaign_id}")
        return _campaign(row)

    def list_campaigns(self) -> list[Campaign]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM campaigns ORDER BY updated_at DESC, id DESC").fetchall()
        return [_campaign(row) for row in rows]

    def list_frames(self, campaign_id: int) -> list[Frame]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM frames WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall()
        return [_frame(row) for row in rows]

    def list_prompts(self, campaign_id: int) -> list[Prompt]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM prompts WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall()
        return [_prompt(row) for row in rows]

    def list_jobs(self, campaign_id: int) -> list[Job]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE campaign_id = ? ORDER BY order_index", (campaign_id,)).fetchall()
        return [_job(row) for row in rows]

    def count_jobs(self, campaign_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM jobs WHERE campaign_id = ?", (campaign_id,)).fetchone()
        return int(row["count"])

    def delete_pending_job(self, campaign_id: int, job_id: int) -> bool:
        with self.db.connect() as conn:
            deleted = conn.execute(
                """
                DELETE FROM jobs
                WHERE id = ? AND campaign_id = ? AND status = 'PENDING'
                  AND started_at IS NULL AND workflow_id IS NULL
                  AND artifact_url IS NULL AND output_file IS NULL
                """,
                (job_id, campaign_id),
            ).rowcount
        return deleted == 1

    def update_campaign_status(self, campaign_id: int, status: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE campaigns SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now_iso(), campaign_id),
            )

    def update_frame_outfit(self, frame_id: int, outfit_name: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE frames SET outfit_name = ? WHERE id = ?", (outfit_name, frame_id))

    def update_prompt(self, prompt_id: int, prompt_name: str, prompt_text: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE prompts SET prompt_name = ?, prompt_text = ? WHERE id = ?",
                (prompt_name, prompt_text, prompt_id),
            )

    def claim_next_job(self, campaign_id: int) -> Job | None:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE campaign_id = ? AND status IN ('PENDING', 'RETRY_WAIT')
                ORDER BY order_index
                LIMIT 1
                """,
                (campaign_id,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            updated = conn.execute(
                """
                UPDATE jobs
                SET status = 'PREPARING', started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE id = ? AND status IN ('PENDING', 'RETRY_WAIT')
                """,
                (utc_now_iso(), utc_now_iso(), row["id"]),
            ).rowcount
            conn.commit()
        return self.get_job(row["id"]) if updated else None

    def get_job(self, job_id: int) -> Job:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"Job not found: {job_id}")
        return _job(row)

    def get_job_bundle(self, job_id: int) -> tuple[Job, Frame, Prompt, Campaign]:
        with self.db.connect() as conn:
            job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            frame_row = conn.execute("SELECT f.* FROM frames f JOIN jobs j ON j.frame_id = f.id WHERE j.id = ?", (job_id,)).fetchone()
            prompt_row = conn.execute("SELECT p.* FROM prompts p JOIN jobs j ON j.prompt_id = p.id WHERE j.id = ?", (job_id,)).fetchone()
            campaign_row = conn.execute(
                "SELECT c.* FROM campaigns c JOIN jobs j ON j.campaign_id = c.id WHERE j.id = ?",
                (job_id,),
            ).fetchone()
        return _job(job_row), _frame(frame_row), _prompt(prompt_row), _campaign(campaign_row)

    def set_job_status(self, job_id: int, status: str, last_error: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (status, last_error, utc_now_iso(), job_id),
            )

    def retry_job(self, job_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'RETRY_WAIT', workflow_id = NULL, artifact_url = NULL, output_file = NULL,
                    idempotency_key = idempotency_key || ':retry:' || (attempt_count + 1),
                    last_error = NULL, remote_completed_at = NULL, downloaded_at = NULL,
                    completed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (utc_now_iso(), job_id),
            )

    def save_rendered_prompt(self, job_id: int, rendered_prompt: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET rendered_prompt = ?, updated_at = ? WHERE id = ?",
                (rendered_prompt, utc_now_iso(), job_id),
            )

    def save_workflow_id(self, job_id: int, workflow_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET workflow_id = ?, status = 'QUEUED', updated_at = ? WHERE id = ?",
                (workflow_id, utc_now_iso(), job_id),
            )

    def save_artifact_url(self, job_id: int, artifact_url: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET artifact_url = ?, status = 'COMPLETED_REMOTE', remote_completed_at = ?, updated_at = ? WHERE id = ?",
                (artifact_url, utc_now_iso(), utc_now_iso(), job_id),
            )

    def mark_downloaded(self, job_id: int, output_file: str) -> None:
        now = utc_now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET output_file = ?, status = 'DONE', downloaded_at = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (output_file, now, now, now, job_id),
            )

    def increment_attempts(self, job_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE jobs SET attempt_count = attempt_count + 1 WHERE id = ?", (job_id,))

    def job_counts(self, campaign_id: int) -> dict[str, int]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM jobs WHERE campaign_id = ? GROUP BY status",
                (campaign_id,),
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def get_cached_upload(self, sha256: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT media_reference_json FROM frame_uploads WHERE sha256 = ?", (sha256,)).fetchone()
        return json.loads(row["media_reference_json"]) if row else None

    def save_cached_upload(self, sha256: str, media_reference: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO frame_uploads (sha256, media_reference_json)
                VALUES (?, ?)
                ON CONFLICT(sha256) DO UPDATE SET media_reference_json = excluded.media_reference_json
                """,
                (sha256, json.dumps(media_reference)),
            )


def _campaign(row) -> Campaign:
    return Campaign(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        model_id=row["model_id"],
        model_name=row["model_name"],
        frames_folder=row["frames_folder"],
        prompts_source=row["prompts_source"],
        output_folder=row["output_folder"],
        filename_template=row["filename_template"],
        organization_mode=row["organization_mode"],
        concurrency=row["concurrency"],
        settings_json=row["settings_json"],
    )


def _frame(row) -> Frame:
    return Frame(row["id"], row["campaign_id"], row["file_path"], row["filename"], row["outfit_name"], row["sha256"])


def _prompt(row) -> Prompt:
    return Prompt(row["id"], row["campaign_id"], row["prompt_code"], row["prompt_name"], row["prompt_text"])


def _job(row) -> Job:
    return Job(
        id=row["id"],
        campaign_id=row["campaign_id"],
        frame_id=row["frame_id"],
        prompt_id=row["prompt_id"],
        order_index=row["order_index"],
        status=row["status"],
        workflow_id=row["workflow_id"],
        idempotency_key=row["idempotency_key"],
        artifact_url=row["artifact_url"],
        output_file=row["output_file"],
        attempt_count=row["attempt_count"],
        last_error=row["last_error"],
        rendered_prompt=row["rendered_prompt"] if "rendered_prompt" in row.keys() else "",
    )
