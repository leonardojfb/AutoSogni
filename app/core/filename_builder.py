from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


WINDOWS_FORBIDDEN = r'<>:"/\\|?*'


@dataclass(frozen=True)
class FilenameContext:
    campaign: str
    job_number: int
    outfit: str
    frame_name: str
    prompt_id: str
    prompt_name: str
    model: str


class FilenameBuilder:
    def preview(self, template: str, context: FilenameContext) -> str:
        return self._render(template, context)

    def build_output_path(self, output_folder: Path, organization_mode: str, template: str, context: FilenameContext) -> Path:
        filename = self._render(template, context)
        folder = output_folder
        if organization_mode == "by_outfit":
            folder = output_folder / sanitize_path_part(context.outfit)
        folder.mkdir(parents=True, exist_ok=True)
        return self._avoid_collision(folder / filename)

    def _render(self, template: str, context: FilenameContext) -> str:
        values = {
            "job_number": f"{context.job_number:04d}",
            "outfit": sanitize_path_part(context.outfit),
            "frame_name": sanitize_path_part(Path(context.frame_name).stem),
            "prompt_id": sanitize_path_part(context.prompt_id),
            "prompt_name": sanitize_path_part(context.prompt_name),
            "model": sanitize_path_part(context.model),
            "campaign": sanitize_path_part(context.campaign),
        }
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", value)
        if not Path(rendered).suffix:
            rendered += ".mp4"
        return sanitize_filename(rendered, keep_extension=True)

    def _avoid_collision(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for idx in range(2, 10_000):
            candidate = path.with_name(f"{stem}__{idx:02d}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not find available filename for {path}")


def sanitize_path_part(value: str) -> str:
    cleaned = "".join("_" if char in WINDOWS_FORBIDDEN else char for char in value)
    cleaned = re.sub(r"[\s_]+", "_", cleaned.strip())
    cleaned = cleaned.strip(" ._")
    return cleaned or "untitled"


def sanitize_filename(value: str, keep_extension: bool = False) -> str:
    if keep_extension:
        path = Path(value)
        stem = "".join("_" if char in WINDOWS_FORBIDDEN else char for char in path.stem)
        stem = re.sub(r"\s+", "_", stem.strip()).strip(" ._")
        suffix = "".join("_" if char in WINDOWS_FORBIDDEN else char for char in path.suffix)
        return f"{stem or 'untitled'}{suffix}"[:180]
    return sanitize_path_part(value)[:180]
