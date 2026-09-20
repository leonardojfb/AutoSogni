from __future__ import annotations

import json
from pathlib import Path

from app.core.prompt_importer import import_prompts
from app.core.validators import default_outfit_name, ensure_output_folder, validate_frames_folder
from app.database.models import Campaign
from app.database.repositories import CampaignRepository
from app.sogni.schemas import ModelDescriptor
from app.utils.hashing import sha256_file


class CampaignManager:
    def __init__(self, repo: CampaignRepository) -> None:
        self.repo = repo

    def create_campaign(
        self,
        name: str,
        frames_folder: Path,
        prompts_source: Path,
        output_folder: Path,
        model: ModelDescriptor,
        settings: dict,
        filename_template: str = "{outfit}__{prompt_id}_{prompt_name}.mp4",
        organization_mode: str = "by_outfit",
        concurrency: int = 1,
    ) -> Campaign:
        frames = validate_frames_folder(frames_folder)
        prompts = import_prompts(prompts_source)
        ensure_output_folder(output_folder)
        if not model.id:
            raise ValueError("Model ID is required.")
        if concurrency < 1:
            raise ValueError("Concurrency must be at least 1.")

        campaign_id = self.repo.insert_campaign(
            {
                "name": name.strip() or "Untitled Campaign",
                "status": "READY",
                "model_id": model.id,
                "model_name": model.name,
                "frames_folder": str(frames_folder),
                "prompts_source": str(prompts_source),
                "output_folder": str(output_folder),
                "filename_template": filename_template,
                "organization_mode": organization_mode,
                "concurrency": concurrency,
                "settings_json": json.dumps(settings),
            }
        )

        frame_ids = [
            self.repo.insert_frame(
                campaign_id,
                str(frame),
                frame.name,
                default_outfit_name(frame.name),
                sha256_file(frame),
            )
            for frame in frames
        ]
        prompt_ids = [
            self.repo.insert_prompt(campaign_id, prompt.prompt_code, prompt.prompt_name, prompt.prompt_text)
            for prompt in prompts
        ]

        order_index = 1
        for frame_id in frame_ids:
            for prompt_id in prompt_ids:
                self.repo.insert_job(campaign_id, frame_id, prompt_id, order_index, f"sva:{campaign_id}:{order_index:04d}")
                order_index += 1

        return self.repo.get_campaign(campaign_id)
