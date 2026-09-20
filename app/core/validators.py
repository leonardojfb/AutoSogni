from __future__ import annotations

import logging
import re
from pathlib import Path


log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

_MINIMAX_H3_REQUIRED_FIELDS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)


def default_outfit_name(filename: str) -> str:
    stem = Path(filename).stem
    words = re.split(r"[_\-\s]+", stem)
    cleaned = [word for word in words if word and not word.isdigit() and word.lower() != "outfit"]
    return " ".join(word.capitalize() for word in cleaned) or Path(filename).stem


def validate_frames_folder(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        raise ValueError(f"Frames folder does not exist: {path}")
    frames = sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS)
    if not frames:
        raise ValueError("Frames folder must contain at least one PNG, JPG, JPEG, or WEBP image.")
    return frames


def ensure_output_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".autosogni_write_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


def prepare_minimax_h3_prompt(raw_prompt: str, mode: str, language: str = "English") -> str:
    normalized = (raw_prompt or "").strip()
    if not normalized:
        raise ValueError("Invalid MiniMax H3 prompt contract: prompt is empty.")

    if "minimax-h3" not in str(mode).lower():
        return normalized

    validate_minimax_h3_prompt(normalized, mode)
    log.info(
        "MiniMax H3 prompt validation passed without rewriting: format=%s length=%s",
        "legacy_timeline" if _looks_like_legacy_timeline(normalized) else "structured",
        len(normalized),
    )
    return normalized


def _looks_like_legacy_timeline(prompt: str) -> bool:
    return bool(re.search(r"(?m)^\s*\d{2}:\d{2}(?:\.\d{3})?\s+", prompt))


def validate_speaker_binding(rendered_prompt: str) -> str:
    dialogue_pattern = re.compile(r"<d>\s*\[[^\]]+\]\s*(.*?)\s*</d>", flags=re.IGNORECASE | re.DOTALL)
    speaker_pattern = re.compile(r"\(S\d+\)", flags=re.IGNORECASE)
    for match in dialogue_pattern.finditer(rendered_prompt):
        dialogue_text = match.group(1)
        if speaker_pattern.search(dialogue_text):
            raise ValueError("Invalid MiniMax H3 prompt contract: speaker ID must remain outside <d> dialogue blocks.")
        nearby_prefix = rendered_prompt[max(0, match.start() - 180) : match.start()]
        if not speaker_pattern.search(nearby_prefix):
            raise ValueError(
                "Invalid MiniMax H3 prompt contract: every spoken line must have a nearby stable (S1) speaker identifier."
            )
    return rendered_prompt


def validate_minimax_h3_prompt(prompt: str, model_id: str | None = None) -> str:
    if model_id is None or "minimax-h3" not in model_id.lower():
        return prompt

    normalized = (prompt or "").strip()
    if not normalized:
        raise ValueError("Invalid MiniMax H3 prompt contract: prompt is empty.")

    lower_prompt = normalized.lower()
    indices = [lower_prompt.index(field.lower()) if field.lower() in lower_prompt else -1 for field in _MINIMAX_H3_REQUIRED_FIELDS]
    if any(index == -1 for index in indices):
        raise ValueError(
            "Invalid MiniMax H3 prompt contract: prompt must include integrated_multimodal_description, overall_soundscape, and non_diegetic_music blocks."
        )

    if indices[0] > indices[1] or indices[1] > indices[2]:
        raise ValueError(
            "Invalid MiniMax H3 prompt contract: integrated_multimodal_description, overall_soundscape, and non_diegetic_music must appear in that order."
        )

    def block_text(label: str) -> str:
        pattern = rf"(?is){re.escape(label)}\s*(.*?)(?=\n\s*(?:integrated_multimodal_description:|overall_soundscape:|non_diegetic_music:)|$)"
        match = re.search(pattern, normalized)
        return (match.group(1).strip() if match else "").strip()

    if not block_text("integrated_multimodal_description:"):
        raise ValueError("Invalid MiniMax H3 prompt contract: integrated_multimodal_description cannot be empty.")
    if not block_text("overall_soundscape:"):
        raise ValueError("Invalid MiniMax H3 prompt contract: overall_soundscape cannot be empty.")
    if not block_text("non_diegetic_music:"):
        raise ValueError("Invalid MiniMax H3 prompt contract: non_diegetic_music cannot be empty.")

    if re.search(r"(?m)^\s*\d{1,2}:\d{2}(?:\.\d{3})?\s+", normalized):
        raise ValueError(
            "Invalid MiniMax H3 prompt contract: bare timestamps like '00:01' are not allowed; use [Shot N] At MM:SS.mmm, markers."
        )

    validate_speaker_binding(normalized)
    shot_numbers = [int(value) for value in re.findall(r"\[Shot\s+(\d+)\]", normalized, flags=re.IGNORECASE)]
    clock_pattern = r"(?<!\w)\d{1,2}:\d{2}(?:\.\d{3})?(?!\w)"
    clocks = list(re.finditer(clock_pattern, normalized))
    if not clocks:
        if shot_numbers and max(shot_numbers) > 1:
            raise ValueError(
                "Invalid MiniMax H3 prompt contract: Shot 2+ cut markers must include '[Shot N] At MM:SS.mmm,'."
            )
        return normalized

    if not shot_numbers or max(shot_numbers) == 1:
        raise ValueError(
            "Invalid MiniMax H3 prompt contract: clock times belong only on '[Shot N] At MM:SS.mmm,' cut markers; order events inside a shot with words."
        )

    valid_marker_clocks = list(re.finditer(r"\[Shot\s+(\d+)\]\s+At\s+\d{1,2}:\d{2}\.\d{3},", normalized, flags=re.IGNORECASE))
    if any(int(marker.group(1)) < 2 for marker in valid_marker_clocks):
        raise ValueError(
            "Invalid MiniMax H3 prompt contract: Shot 1 must not contain a clock timestamp."
        )
    covered_ranges = [(marker.start(), marker.end()) for marker in valid_marker_clocks]
    for clock in clocks:
        if not any(start <= clock.start() < end for start, end in covered_ranges):
            raise ValueError(
                "Invalid MiniMax H3 prompt contract: clock times belong only on '[Shot N] At MM:SS.mmm,' cut markers; order events inside a shot with words."
            )
    if any(number >= 2 and not re.search(rf"\[Shot\s+{number}\]\s+At\s+\d{{1,2}}:\d{{2}}\.\d{{3}},", normalized, flags=re.IGNORECASE) for number in set(shot_numbers)):
        raise ValueError(
            "Invalid MiniMax H3 prompt contract: every Shot 2+ cut marker must include '[Shot N] At MM:SS.mmm,'."
        )
    return normalized
