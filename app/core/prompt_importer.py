from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ImportedPrompt:
    prompt_code: str
    prompt_name: str
    prompt_text: str


def import_prompts(path: Path) -> list[ImportedPrompt]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Prompt JSON must be a list.")
        return [_prompt_from_mapping(row, idx) for idx, row in enumerate(raw, start=1)]
    if suffix == ".csv":
        with path.open("r", newline="", encoding="utf-8-sig") as fh:
            return [_prompt_from_mapping(row, idx) for idx, row in enumerate(csv.DictReader(fh), start=1)]
    if suffix == ".txt":
        blocks = [block.strip() for block in path.read_text(encoding="utf-8").split("\n\n") if block.strip()]
        return [
            ImportedPrompt(prompt_code=f"P{idx:02d}", prompt_name=f"Prompt {idx:02d}", prompt_text=block)
            for idx, block in enumerate(blocks, start=1)
        ]
    raise ValueError("Supported prompt files are .json, .csv, and .txt.")


def _prompt_from_mapping(row: dict[str, Any], index: int) -> ImportedPrompt:
    prompt_text = str(row.get("text") or row.get("prompt") or row.get("prompt_text") or "").strip()
    if not prompt_text:
        raise ValueError(f"Prompt {index} has empty text.")
    return ImportedPrompt(
        prompt_code=str(row.get("id") or row.get("prompt_code") or f"P{index:02d}").strip(),
        prompt_name=str(row.get("name") or row.get("prompt_name") or f"Prompt {index:02d}").strip(),
        prompt_text=prompt_text,
    )
