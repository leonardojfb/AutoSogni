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
    error_code: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BytePlusTask":
        content = payload.get("content") or {}
        error = payload.get("error") or {}
        return cls(
            id=str(payload.get("id") or ""),
            status=str(payload.get("status") or "queued"),
            video_url=str(content.get("video_url") or ""),
            last_frame_url=str(content.get("last_frame_url") or ""),
            error=str(error.get("message") if isinstance(error, dict) else error or ""),
            raw=payload,
            error_code=str(error.get("code") or "") if isinstance(error, dict) else "",
        )
