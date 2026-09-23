from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.utils.paths import data_dir


class QueueStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_remote_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_remote_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_remote_urls(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return "<remote-url>"
    return value


@dataclass
class WaveSpeedQueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    video_path: str = ""
    prompt: str = ""
    resolution: str = "720p"
    aspect_ratio: str = "16:9"
    duration: int = 5
    enable_prompt_expansion: bool = False
    enable_audio: bool = True
    seed: int = -1
    status: str = QueueStatus.PENDING
    task_id: str = ""
    price: float | None = None
    output_file: str = ""
    error: str = ""
    created_at: str = field(default_factory=_now)

    def retry(self) -> None:
        self.status = QueueStatus.PENDING
        self.task_id = ""
        self.price = None
        self.output_file = ""
        self.error = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WaveSpeedQueueItem":
        fields = {
            "item_id",
            "video_path",
            "prompt",
            "resolution",
            "aspect_ratio",
            "duration",
            "enable_prompt_expansion",
            "enable_audio",
            "seed",
            "status",
            "task_id",
            "price",
            "output_file",
            "error",
            "created_at",
        }
        return cls(**{key: value[key] for key in fields if key in value})


@dataclass
class WaveSpeedQueue:
    frame_path: str = ""
    output_dir: str = ""
    items: list[WaveSpeedQueueItem] = field(default_factory=list)
    paused: bool = False
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_path": self.frame_path,
            "output_dir": self.output_dir,
            "items": [item.to_dict() for item in self.items],
            "paused": self.paused,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WaveSpeedQueue":
        raw_items = value.get("items") or []
        items = [WaveSpeedQueueItem.from_dict(item) for item in raw_items if isinstance(item, dict)]
        return cls(
            frame_path=str(value.get("frame_path") or ""),
            output_dir=str(value.get("output_dir") or ""),
            items=items,
            paused=bool(value.get("paused", False)),
            updated_at=str(value.get("updated_at") or _now()),
        )


class WaveSpeedQueueStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or data_dir() / "wavespeed_queue.json")

    def load(self) -> WaveSpeedQueue:
        if not self.path.exists():
            return WaveSpeedQueue()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return WaveSpeedQueue()
        if not isinstance(value, dict):
            return WaveSpeedQueue()
        return WaveSpeedQueue.from_dict(value)

    def save(self, queue: WaveSpeedQueue) -> None:
        queue.updated_at = _now()
        safe = _redact_remote_urls(queue.to_dict())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
