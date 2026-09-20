from __future__ import annotations

import os
from pathlib import Path

from app.utils.paths import data_dir


class ApiKeyStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "sogni_api_key.txt"

    def get(self) -> str:
        env_key = os.environ.get("SOGNI_API_KEY", "").strip()
        if env_key:
            return env_key
        if self.path.exists():
            return self.path.read_text(encoding="utf-8").strip()
        return ""

    def save(self, key: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(key.strip(), encoding="utf-8")
