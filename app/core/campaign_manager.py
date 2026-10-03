from __future__ import annotations

import json
from pathlib import Path

from app.core.prompt_importer import import_prompts
from app.core.validators import (
    default_outfit_name, ensure_output_folder, validate_frames_folder,
    validate_minimax_reference_files,
)
from app.database.models import Campaign
from app.database.repositories import CampaignRepository
from app.sogni.schemas import ModelDescriptor
from app.utils.hashing import sha256_file


class CampaignManager:
    def __init__(self, repo: CampaignRepository) -> None:
        self.repo = repo

    def clone_campaign_base(self, campaign_id: int) -> Campaign:
        source = self.repo.get_campaign(campaign_id)
        clone_id = self.repo.insert_campaign(
            {
                "name": f"{source.name} (copia)",
                "status": "READY",
                "model_id": source.model_id,
                "model_name": source.model_name,
                "frames_folder": source.frames_folder,
                "prompts_source": source.prompts_source,
                "output_folder": source.output_folder,
                "filename_template": source.filename_template,
                "organization_mode": source.organization_mode,
                "concurrency": source.concurrency,
                "settings_json": source.settings_json,
            }
        )
        return self.repo.get_campaign(clone_id)

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
        enqueue_jobs: bool = True,
        reference_media: list[Path] | None = None,
    ) -> Campaign:
        is_r2v = _is_minimax_r2v(model.id)
        is_i2v = _is_minimax_i2v(model.id)
        references = validate_minimax_reference_files(reference_media or []) if is_r2v else []
        frames = ([references[0]] if is_r2v else
                  _validate_i2v_images(reference_media) if is_i2v else
                  validate_frames_folder(frames_folder))
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
                "settings_json": json.dumps({**settings, **({"reference_media": [str(p) for p in references]} if is_r2v else {})}),
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

        if enqueue_jobs:
            order_index = 1
            for frame_id in frame_ids:
                for prompt_id in prompt_ids:
                    self.repo.insert_job(
                        campaign_id, frame_id, prompt_id, order_index,
                        f"sva:{campaign_id}:{order_index:04d}",
                        [str(path) for path in references] if is_r2v else [],
                        settings,
                    )
                    order_index += 1

        return self.repo.get_campaign(campaign_id)

    def add_jobs_to_campaign(
        self,
        campaign_id: int,
        frames_folder: Path,
        prompts_source: Path,
        reference_media: list[Path] | None = None,
        job_settings: dict | None = None,
    ) -> int:
        campaign = self.repo.get_campaign(campaign_id)
        is_r2v = _is_minimax_r2v(campaign.model_id)
        is_i2v = _is_minimax_i2v(campaign.model_id)
        references = validate_minimax_reference_files(reference_media or []) if is_r2v else []
        uses_job_settings = job_settings is not None
        campaign_settings = dict(job_settings or json.loads(campaign.settings_json or "{}"))
        if is_r2v:
            campaign_settings["reference_media"] = [str(path) for path in references]
            if not uses_job_settings:
                self.repo.update_campaign_settings(campaign_id, campaign_settings)
        frames = ([references[0]] if is_r2v else
                  _validate_i2v_images(reference_media) if is_i2v else
                  validate_frames_folder(frames_folder))
        prompts = import_prompts(prompts_source)
        existing_jobs = self.repo.list_jobs(campaign_id)
        frame_by_id = {frame.id: frame for frame in self.repo.list_frames(campaign_id)}
        prompt_by_id = {prompt.id: prompt for prompt in self.repo.list_prompts(campaign_id)}
        existing_pairs = {
            (
                frame_by_id[job.frame_id].sha256,
                prompt_by_id[job.prompt_id].prompt_code,
                prompt_by_id[job.prompt_id].prompt_name,
                prompt_by_id[job.prompt_id].prompt_text,
                tuple(json.loads(job.reference_media_json or "[]")),
                _settings_key(json.loads(job.settings_json or "{}") or campaign_settings),
            )
            for job in existing_jobs
        }

        frame_ids_by_hash = {frame.sha256: frame.id for frame in frame_by_id.values()}
        prompt_ids_by_content = {
            (prompt.prompt_code, prompt.prompt_name, prompt.prompt_text): prompt.id
            for prompt in prompt_by_id.values()
        }
        order_index = max((job.order_index for job in existing_jobs), default=0) + 1
        added_count = 0
        for frame in frames:
            frame_hash = sha256_file(frame)
            for prompt in prompts:
                reference_key = tuple(str(path) for path in references) if is_r2v else ()
                pair = (frame_hash, prompt.prompt_code, prompt.prompt_name, prompt.prompt_text, reference_key, _settings_key(campaign_settings))
                if pair in existing_pairs:
                    continue

                frame_id = frame_ids_by_hash.get(frame_hash)
                if frame_id is None:
                    frame_id = self.repo.insert_frame(
                        campaign_id,
                        str(frame),
                        frame.name,
                        default_outfit_name(frame.name),
                        frame_hash,
                    )
                    frame_ids_by_hash[frame_hash] = frame_id
                prompt_key = (prompt.prompt_code, prompt.prompt_name, prompt.prompt_text)
                prompt_id = prompt_ids_by_content.get(prompt_key)
                if prompt_id is None:
                    prompt_id = self.repo.insert_prompt(
                        campaign_id, prompt.prompt_code, prompt.prompt_name, prompt.prompt_text
                    )
                    prompt_ids_by_content[prompt_key] = prompt_id
                self.repo.insert_job(
                    campaign_id,
                    frame_id,
                    prompt_id,
                    order_index,
                    f"sva:{campaign_id}:{order_index:04d}",
                    [str(path) for path in references] if is_r2v else [],
                    campaign_settings,
                )
                existing_pairs.add(pair)
                order_index += 1
                added_count += 1

        return added_count


def _is_minimax_r2v(model_id: str) -> bool:
    return "minimax-h3" in model_id.lower() and ("_r2v" in model_id.lower() or "-r2v" in model_id.lower())


def _settings_key(settings: dict) -> str:
    return json.dumps(settings, sort_keys=True, separators=(",", ":"))


def _is_minimax_i2v(model_id: str) -> bool:
    normalized = model_id.lower()
    return "minimax-h3" in normalized and "_i2v" in normalized and not _is_minimax_r2v(normalized)


def _validate_i2v_images(paths: list[Path] | None) -> list[Path]:
    selected = list(paths or [])
    if not selected:
        raise ValueError("Seleccioná al menos una imagen inicial.")
    if any(path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"} or not path.is_file() for path in selected):
        raise ValueError("I2V solo admite imágenes PNG, JPG, JPEG, WEBP o GIF existentes.")
    return selected
