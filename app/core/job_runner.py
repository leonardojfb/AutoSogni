from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.downloader import Downloader
from app.core.filename_builder import FilenameBuilder, FilenameContext
from app.core.model_settings import resolve_job_settings
from app.core.validators import prepare_minimax_h3_prompt
from app.database.repositories import CampaignRepository
from app.sogni.client import SogniRateLimitError, _detect_image_content_type


log = logging.getLogger(__name__)
TERMINAL_FAILED = {"failed", "canceled", "cancelled", "error", "errored"}


class SogniWorkflowFailure(RuntimeError):
    """A terminal Sogni workflow failure that only affects the current job."""


class JobRunner:
    def __init__(
        self,
        repo: CampaignRepository,
        sogni_client,
        downloader: Downloader | None = None,
        poll_interval: float = 2.0,
        max_polls: int = 1800,
    ) -> None:
        self.repo = repo
        self.sogni = sogni_client
        self.downloader = downloader or Downloader()
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.filename_builder = FilenameBuilder()

    def run(self, job_id: int) -> None:
        job, frame, prompt, campaign = self.repo.get_job_bundle(job_id)
        log.info("Starting job %s: campaign=%s frame=%s prompt=%s", job_id, campaign.id, frame.filename, prompt.prompt_code)
        campaign_settings = json.loads(campaign.settings_json or "{}")
        skip_prompt_processing = bool(campaign_settings.pop("skipPromptProcessing", True))
        settings = resolve_job_settings(campaign_settings, prompt.prompt_text)
        output_path = self.filename_builder.build_output_path(
            Path(campaign.output_folder),
            campaign.organization_mode,
            campaign.filename_template,
            FilenameContext(
                campaign=campaign.name,
                job_number=job.order_index,
                outfit=frame.outfit_name,
                frame_name=frame.filename,
                prompt_id=prompt.prompt_code,
                prompt_name=prompt.prompt_name,
                model=campaign.model_name,
            ),
        )
        log.debug("Job %s settings=%s output=%s", job_id, settings, output_path)

        try:
            self.repo.increment_attempts(job.id)
            artifact_url = job.artifact_url
            workflow_id = job.workflow_id
            original_prompt = prompt.prompt_text
            rendered_prompt = prepare_minimax_h3_prompt(original_prompt, campaign.model_id, language="English") if "minimax-h3" in campaign.model_id.lower() else original_prompt
            self.repo.save_rendered_prompt(job.id, rendered_prompt)
            if not artifact_url:
                if not workflow_id:
                    self.repo.set_job_status(job.id, "UPLOADING_FRAME")
                    media_reference = self._media_reference_for_frame(frame.sha256, job.id, Path(frame.file_path))
                    log.debug("Job %s media_reference=%s", job.id, media_reference)
                    self.repo.set_job_status(job.id, "SUBMITTING")
                    log.info("Submitting workflow for job %s with idempotency=%s", job.id, job.idempotency_key)
                    workflow = self.sogni.start_image_to_video_workflow(
                        title=f"{campaign.name} - Job {job.order_index:04d}",
                        prompt=rendered_prompt,
                        model_id=campaign.model_id,
                        settings=settings,
                        media_reference=media_reference,
                        idempotency_key=job.idempotency_key,
                        skip_prompt_processing=skip_prompt_processing,
                    )
                    log.info("Workflow response for job %s: %s", job.id, json.dumps(workflow, ensure_ascii=False, indent=2, default=str))
                    workflow_id = workflow.get("workflowId") or workflow.get("id")
                    if not workflow_id:
                        raise RuntimeError(f"Sogni workflow response did not include workflowId. Payload={json.dumps(workflow, ensure_ascii=False, indent=2, default=str)}")
                    self.repo.save_workflow_id(job.id, workflow_id)
                    log.info("Saved workflow_id=%s for job %s", workflow_id, job.id)
                artifact_url = self._wait_for_artifact(job.id, workflow_id)
                self.repo.save_artifact_url(job.id, artifact_url)
                log.info("Artifact ready for job %s: %s", job.id, artifact_url)

            self.repo.set_job_status(job.id, "DOWNLOADING")
            log.info("Downloading artifact for job %s to %s", job.id, output_path)
            downloaded = self.downloader.download(artifact_url, output_path)
            self.repo.mark_downloaded(job.id, str(downloaded))
            log.info("Job %s completed and saved to %s", job.id, downloaded)
        except SogniRateLimitError as exc:
            self.repo.set_job_status(job.id, "RETRY_WAIT", _scrub_secret(str(exc)))
            log.warning("Job %s deferred because of Sogni rate limit: %s", job.id, exc)
            raise
        except Exception as exc:
            log.exception("Job %s failed with exception: %s", job.id, exc)
            self.repo.set_job_status(job.id, "FAILED", _scrub_secret(str(exc)))
            raise

    def _media_reference_for_frame(self, sha256: str, job_id: int, frame_path: Path) -> dict[str, Any]:
        cached = self.repo.get_cached_upload(sha256)
        if cached and cached.get("url") and _presigned_media_reference_is_fresh(cached) and _cached_content_type_matches_file(cached, frame_path):
            return cached
        if cached and cached.get("url"):
            log.info("Cached media reference for frame %s is expired, near expiry, or has a mismatched content type; refreshing upload", frame_path)
        upload = self.sogni.get_image_upload_url(str(job_id), frame_path) if hasattr(self.sogni, "get_image_upload_url") else None
        if upload and hasattr(self.sogni, "upload_image_to_presigned_post"):
            media_reference = self.sogni.upload_image_to_presigned_post(upload, frame_path)
        else:
            raise RuntimeError(
                f"Sogni upload metadata unavailable for frame {frame_path}. "
                "A valid presigned upload URL is required before the workflow can start."
            )
        self.repo.save_cached_upload(sha256, media_reference)
        return media_reference

    def _wait_for_artifact(self, job_id: int, workflow_id: str) -> str:
        for poll in range(self.max_polls):
            self.repo.set_job_status(job_id, "GENERATING")
            workflow = self.sogni.read_workflow(workflow_id)
            status = str(workflow.get("status", "")).lower()
            artifact_url = extract_artifact_url(workflow)
            log.info("Polling workflow %s for job %s: poll=%s status=%s artifact=%s", workflow_id, job_id, poll, status, bool(artifact_url))
            log.info("Workflow payload for job %s at poll %s: %s", job_id, poll, json.dumps(workflow, ensure_ascii=False, indent=2, default=str))
            if artifact_url:
                return artifact_url
            if status == "waiting_for_user":
                if workflow.get("awaitingCostApproval"):
                    raise RuntimeError(
                        f"Sogni workflow is waiting for cost approval: workflow_id={workflow_id} "
                        f"payload={json.dumps(workflow, ensure_ascii=False, indent=2, default=str)}"
                    )
                if hasattr(self.sogni, "resume_workflow"):
                    log.warning("Auto-resuming Sogni workflow %s for job %s after waiting_for_user", workflow_id, job_id)
                    self.sogni.resume_workflow(workflow_id)
                    continue
                raise RuntimeError(f"Sogni workflow is waiting for user action but this client cannot resume it: workflow_id={workflow_id}")
            if status in TERMINAL_FAILED:
                message = f"Sogni workflow ended without artifact: status={status} payload={json.dumps(workflow, ensure_ascii=False, indent=2, default=str)}"
                if _workflow_failure_is_non_retryable(workflow):
                    raise SogniWorkflowFailure(message)
                raise RuntimeError(message)
            if self.poll_interval:
                time.sleep(self.poll_interval)
        raise TimeoutError(f"Timed out waiting for workflow {workflow_id}")


def extract_artifact_url(workflow: dict[str, Any]) -> str | None:
    for step in workflow.get("steps") or []:
        for artifact in step.get("artifacts") or []:
            url = artifact.get("url") or artifact.get("downloadUrl")
            if url:
                return str(url)
    artifacts = workflow.get("artifacts") or []
    for artifact in artifacts:
        url = artifact.get("url") or artifact.get("downloadUrl")
        if url:
            return str(url)
    return None


def _presigned_media_reference_is_fresh(media_reference: dict[str, Any], safety_seconds: int = 300) -> bool:
    url = str(media_reference.get("url") or "")
    if not url:
        return False
    query = parse_qs(urlparse(url).query)
    signed_at = (query.get("X-Amz-Date") or [None])[0]
    expires = (query.get("X-Amz-Expires") or [None])[0]
    if not signed_at or not expires:
        return True
    try:
        expiration = datetime.strptime(signed_at, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) + timedelta(seconds=int(expires))
    except (TypeError, ValueError):
        return True
    return expiration > datetime.now(timezone.utc) + timedelta(seconds=safety_seconds)


def _cached_content_type_matches_file(media_reference: dict[str, Any], frame_path: Path) -> bool:
    cached_content_type = media_reference.get("contentType")
    if not cached_content_type:
        return True
    return cached_content_type == _detect_image_content_type(frame_path)


def _workflow_failure_is_non_retryable(workflow: dict[str, Any]) -> bool:
    for event in workflow.get("events") or []:
        if not isinstance(event, dict):
            continue
        data = event.get("data") or {}
        if isinstance(data, dict) and data.get("retryable") is False:
            return True
    return False


def _scrub_secret(message: str) -> str:
    return message.replace("Authorization", "[redacted]")
