from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BytePlusTask:
    id: str
    status: str = "queued"
    video_url: str = ""
    last_frame_url: str = ""
    error: str = ""
    raw: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BytePlusTask":
        content = payload.get("content") or {}
        error = payload.get("error") or {}
        return cls(str(payload.get("id") or ""), str(payload.get("status") or "queued"),
                   str(content.get("video_url") or ""), str(content.get("last_frame_url") or ""),
                   str(error.get("message") if isinstance(error, dict) else error or ""), payload)
