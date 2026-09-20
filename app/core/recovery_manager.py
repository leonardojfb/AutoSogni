from __future__ import annotations

from app.database.repositories import CampaignRepository


RECOVERABLE_LOCAL_STATES = {"PREPARING", "UPLOADING_FRAME", "SUBMITTING", "QUEUED", "GENERATING", "DOWNLOADING"}


class RecoveryManager:
    def __init__(self, repo: CampaignRepository, sogni_client=None) -> None:
        self.repo = repo
        self.sogni_client = sogni_client

    def recover_campaign(self, campaign_id: int) -> None:
        for job in self.repo.list_jobs(campaign_id):
            if job.status in RECOVERABLE_LOCAL_STATES:
                if job.artifact_url:
                    self.repo.set_job_status(job.id, "COMPLETED_REMOTE")
                elif job.workflow_id and self.sogni_client:
                    self.repo.set_job_status(job.id, "GENERATING")
                elif not job.workflow_id:
                    self.repo.set_job_status(job.id, "PENDING")
        campaign = self.repo.get_campaign(campaign_id)
        if campaign.status in {"RUNNING", "PAUSE_REQUESTED"}:
            self.repo.update_campaign_status(campaign_id, "PAUSED")
