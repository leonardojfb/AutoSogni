from __future__ import annotations

import json
from pathlib import Path

from app.database.repositories import CampaignRepository


def export_campaign_manifest(repo: CampaignRepository, campaign_id: int) -> Path:
    campaign = repo.get_campaign(campaign_id)
    frames = {frame.id: frame for frame in repo.list_frames(campaign_id)}
    prompts = {prompt.id: prompt for prompt in repo.list_prompts(campaign_id)}
    jobs = repo.list_jobs(campaign_id)
    output = {
        "campaign": campaign.name,
        "model": {"id": campaign.model_id, "name": campaign.model_name},
        "frames": len(frames),
        "prompts": len(prompts),
        "total_jobs": len(jobs),
        "jobs": [
            {
                "job_number": job.order_index,
                "outfit": frames[job.frame_id].outfit_name,
                "frame": frames[job.frame_id].filename,
                "prompt_id": prompts[job.prompt_id].prompt_code,
                "prompt_name": prompts[job.prompt_id].prompt_name,
                "output_file": job.output_file,
                "status": job.status,
                "workflow_id": job.workflow_id,
            }
            for job in jobs
        ],
    }
    path = Path(campaign.output_folder) / "campaign_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return path
