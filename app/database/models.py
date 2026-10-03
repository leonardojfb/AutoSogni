from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Campaign:
    id: int
    name: str
    status: str
    model_id: str
    model_name: str
    frames_folder: str
    prompts_source: str
    output_folder: str
    filename_template: str
    organization_mode: str
    concurrency: int
    settings_json: str


@dataclass(frozen=True)
class Frame:
    id: int
    campaign_id: int
    file_path: str
    filename: str
    outfit_name: str
    sha256: str


@dataclass(frozen=True)
class Prompt:
    id: int
    campaign_id: int
    prompt_code: str
    prompt_name: str
    prompt_text: str


@dataclass(frozen=True)
class Job:
    id: int
    campaign_id: int
    frame_id: int
    prompt_id: int
    order_index: int
    status: str
    workflow_id: str | None
    idempotency_key: str
    artifact_url: str | None
    output_file: str | None
    attempt_count: int
    last_error: str | None
    rendered_prompt: str = ""
    reference_media_json: str = "[]"
    settings_json: str = "{}"
