from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils.paths import data_dir


def _redact_remote_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_remote_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_remote_urls(item) for item in value]
    if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://")):
        return "<remote-url>"
    return value


class WaveSpeedHistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or data_dir() / "wavespeed_history.json")

    def list(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return value if isinstance(value, list) else []

    def add(self, record: dict[str, Any]) -> None:
        safe = dict(record)
        safe["payload"] = _redact_remote_urls(safe.get("payload") or {})
        safe.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        rows = [item for item in self.list() if item.get("task_id") != safe.get("task_id")]
        rows.insert(0, safe)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(rows[:100], ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
