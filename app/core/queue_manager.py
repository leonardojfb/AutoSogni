from __future__ import annotations

import logging
import time

import httpx

from app.core.job_runner import JobRunner, SogniWorkflowFailure
from app.database.repositories import CampaignRepository
from app.sogni.client import SogniRateLimitError


log = logging.getLogger(__name__)
MAX_AUTOMATIC_RETRIES = 3


class QueueManager:
    def __init__(self, repo: CampaignRepository, runner: JobRunner) -> None:
        self.repo = repo
        self.runner = runner
        self._stop_after_current = False

    def request_pause(self, campaign_id: int) -> None:
        self.repo.update_campaign_status(campaign_id, "PAUSE_REQUESTED")

    def resume(self, campaign_id: int) -> None:
        self.repo.update_campaign_status(campaign_id, "RUNNING")
        self.run_until_paused_or_complete(campaign_id)

    def run_until_paused_or_complete(self, campaign_id: int) -> None:
        self.repo.update_campaign_status(campaign_id, "RUNNING")
        log.info("Queue started for campaign %s", campaign_id)
        while True:
            campaign = self.repo.get_campaign(campaign_id)
            if campaign.status == "PAUSE_REQUESTED" or self._stop_after_current:
                self.repo.update_campaign_status(campaign_id, "PAUSED")
                log.info("Queue paused for campaign %s", campaign_id)
                return
            job = self.repo.claim_next_job(campaign_id)
            if job is None:
                counts = self.repo.job_counts(campaign_id)
                final_status = "FAILED" if counts.get("FAILED") else "COMPLETED"
                self.repo.update_campaign_status(campaign_id, final_status)
                log.info("Queue finished for campaign %s with status=%s counts=%s", campaign_id, final_status, counts)
                return
            log.info("Claimed job %s (campaign %s) order=%s status=%s", job.id, campaign_id, job.order_index, job.status)
            try:
                self.runner.run(job.id)
            except SogniRateLimitError as exc:
                self.repo.update_campaign_status(campaign_id, "PAUSED")
                log.warning("Queue paused for campaign %s because of Sogni rate limit: %s", campaign_id, exc)
                return
            except SogniWorkflowFailure as exc:
                log.warning(
                    "Continuing queue for campaign %s after terminal Sogni workflow failure in job %s: %s",
                    campaign_id,
                    job.id,
                    exc,
                )
                continue
            except Exception as exc:
                log.exception("Job %s failed in queue for campaign %s", job.id, campaign_id)
                if _is_auth_error(exc):
                    self.repo.update_campaign_status(campaign_id, "PAUSED")
                    log.error("Queue paused for campaign %s because Sogni authentication failed: %s", campaign_id, exc)
                    return
                if _is_permanent_configuration_error(exc):
                    self.repo.update_campaign_status(campaign_id, "PAUSED")
                    log.error("Queue paused for campaign %s because the job configuration is not supported: %s", campaign_id, exc)
                    return
                if _is_transient_error(exc) and self.repo.get_job(job.id).attempt_count < MAX_AUTOMATIC_RETRIES:
                    retry_number = self.repo.get_job(job.id).attempt_count
                    self.repo.set_job_status(job.id, "RETRY_WAIT", str(exc))
                    delay = min(2 ** retry_number, 30)
                    log.warning(
                        "Retrying transient failure for job %s in %s seconds (attempt %s/%s): %s",
                        job.id,
                        delay,
                        retry_number,
                        MAX_AUTOMATIC_RETRIES,
                        exc,
                    )
                    time.sleep(delay)
                    continue
                log.warning("Continuing queue for campaign %s after job %s failed: %s", campaign_id, job.id, exc)
                continue


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {502, 503, 504}
    return False


def _is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {401, 403}
    return False


def _is_permanent_configuration_error(exc: Exception) -> bool:
    return isinstance(exc, ValueError)
