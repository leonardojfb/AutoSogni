from __future__ import annotations

import re
from typing import Any


ASPECT_RATIOS = ["9:16", "16:9", "1:1", "4:5"]
DURATION_MODES = ["auto", "manual"]


def extract_duration_seconds(prompt_text: str) -> int | None:
    patterns = [
        r"(?:lasting\s+)?approximately\s+(\d+(?:\.\d+)?)\s+seconds?",
        r"(?:lasting\s+)?(\d+(?:\.\d+)?)\s+seconds?",
        r"(\d+(?:\.\d+)?)\s+second\s+(?:vertical\s+)?clip",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt_text, re.IGNORECASE)
        if match:
            return int(round(float(match.group(1))))
    timestamps = [
        (int(match.group(1)), int(match.group(2)))
        for match in re.finditer(r"\b(?:At\s+)?(\d{2}):(\d{2})\b", prompt_text, re.IGNORECASE)
    ]
    if timestamps:
        return max(minutes * 60 + seconds for minutes, seconds in timestamps)
    return None


def build_campaign_settings(
    duration_mode: str,
    duration_seconds: str,
    aspect_ratio: str,
    skip_prompt_processing: bool = True,
) -> dict[str, Any]:
    if duration_mode not in DURATION_MODES:
        raise ValueError("Duration mode must be auto or manual.")
    if aspect_ratio not in ASPECT_RATIOS:
        raise ValueError("Unsupported aspect ratio.")

    settings: dict[str, Any] = {"duration_mode": duration_mode}
    if duration_mode == "manual":
        try:
            duration = int(duration_seconds.strip())
        except ValueError as exc:
            raise ValueError("Duration must be a number of seconds.") from exc
        if duration <= 0:
            raise ValueError("Duration must be greater than 0 seconds.")
        settings["duration"] = duration
    settings["aspectRatio"] = aspect_ratio
    settings["skipPromptProcessing"] = bool(skip_prompt_processing)
    return settings


def resolve_job_settings(campaign_settings: dict[str, Any], prompt_text: str) -> dict[str, Any]:
    resolved = dict(campaign_settings)
    mode = resolved.pop("duration_mode", "manual")
    if mode == "auto":
        duration = extract_duration_seconds(prompt_text)
        if duration is None:
            raise ValueError("Could not detect duration from prompt text.")
        resolved["duration"] = duration
    return {key: value for key, value in resolved.items() if value not in ("", None)}
